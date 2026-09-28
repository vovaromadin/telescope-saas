from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.models import Account, CommunityResult, Plan, Project, SearchRun, UsageEvent
from app.schemas import ProjectCreate, SearchCreate
from app.security import validate_telegram_init_data
from app.services.searches import execute_search


router = APIRouter(prefix="/api/app", tags=["miniapp"])
settings = get_settings()
PLAN_LIMITS = {Plan.free: 5, Plan.pro: 100, Plan.team: 500}


def monthly_used(db: Session, account: Account) -> int:
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    value = db.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.account_id == account.id,
            UsageEvent.event_type == "search",
            UsageEvent.created_at >= month_start,
        )
    )
    return int(value or 0)


def ensure_limit(db: Session, account: Account) -> None:
    if monthly_used(db, account) >= PLAN_LIMITS[account.plan]:
        raise HTTPException(status_code=402, detail=f"Monthly {account.plan.value} plan limit reached")


def current_account(
    x_telegram_init_data: str = Header(default="", alias="X-Telegram-Init-Data"),
    db: Session = Depends(get_db),
) -> Account:
    payload = validate_telegram_init_data(x_telegram_init_data, settings.tg_bot_token)
    user = payload["user"]
    telegram_id = str(user["id"])
    account = db.scalar(select(Account).where(Account.telegram_id == telegram_id))

    display_name = " ".join(part for part in [user.get("first_name", ""), user.get("last_name", "")] if part).strip()
    if user.get("username"):
        display_name = f"{display_name} (@{user['username']})".strip()
    display_name = display_name or f"Telegram {telegram_id}"

    if not account:
        account = Account(name=display_name[:160], telegram_id=telegram_id, plan=Plan.free)
        db.add(account)
        db.commit()
        db.refresh(account)
    elif account.name != display_name[:160]:
        account.name = display_name[:160]
        db.commit()
        db.refresh(account)
    return account


def owned_project(db: Session, account: Account, project_id: int) -> Project:
    project = db.scalar(select(Project).where(Project.id == project_id, Project.account_id == account.id))
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def owned_search(db: Session, account: Account, search_id: int) -> SearchRun:
    run = db.scalar(
        select(SearchRun)
        .join(Project, Project.id == SearchRun.project_id)
        .where(SearchRun.id == search_id, Project.account_id == account.id)
    )
    if not run:
        raise HTTPException(404, "Search not found")
    return run


def serialize_result(row: CommunityResult) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "username": f"@{row.username}",
        "url": row.url,
        "description": row.description,
        "public_contacts": json.loads(row.public_contacts or "[]"),
        "subscribers": row.subscribers,
        "messages_30d": row.messages_30d,
        "avg_views": row.avg_views,
        "relevance_score": row.relevance_score,
        "activity_score": row.activity_score,
        "audience_score": row.audience_score,
        "total_score": row.total_score,
        "matched_snippets": json.loads(row.matched_snippets or "[]"),
        "referral_url": row.referral_url,
        "max_referral_url": row.max_referral_url,
    }


async def run_search_background(run_id: int, referral_prefix: str, limit: int) -> None:
    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        if run:
            await execute_search(db, run, referral_prefix, limit)


@router.get("/me")
def me(account: Account = Depends(current_account), db: Session = Depends(get_db)) -> dict:
    used = monthly_used(db, account)
    limit = PLAN_LIMITS[account.plan]
    return {
        "id": account.id,
        "name": account.name,
        "plan": account.plan.value,
        "monthly_limit": limit,
        "used": used,
        "remaining": max(0, limit - used),
        "telegram_ready": settings.telegram_ready,
    }


@router.get("/projects")
def list_projects(account: Account = Depends(current_account), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(Project).where(Project.account_id == account.id).order_by(Project.created_at.desc())
    ).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "referral_prefix": row.referral_prefix,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/projects")
def create_project(
    payload: ProjectCreate,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> dict:
    project = Project(account_id=account.id, **payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "referral_prefix": project.referral_prefix,
        "created_at": project.created_at.isoformat(),
    }


@router.get("/projects/{project_id}/searches")
def list_searches(
    project_id: int,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> list[dict]:
    owned_project(db, account, project_id)
    rows = db.scalars(
        select(SearchRun).where(SearchRun.project_id == project_id).order_by(SearchRun.created_at.desc())
    ).all()
    return [
        {
            "id": row.id,
            "query": row.query,
            "status": row.status.value,
            "error": row.error,
            "result_count": row.result_count,
            "created_at": row.created_at.isoformat(),
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
        for row in rows
    ]


@router.post("/projects/{project_id}/searches")
def create_search(
    project_id: int,
    payload: SearchCreate,
    background: BackgroundTasks,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> dict:
    ensure_limit(db, account)
    project = owned_project(db, account, project_id)
    run = SearchRun(project_id=project.id, query=payload.query)
    db.add_all([run, UsageEvent(account_id=account.id, event_type="search")])
    db.commit()
    db.refresh(run)
    background.add_task(run_search_background, run.id, project.referral_prefix, payload.limit)
    return {
        "id": run.id,
        "query": run.query,
        "status": run.status.value,
        "error": run.error,
        "result_count": run.result_count,
    }


@router.get("/searches/{search_id}")
def get_search(
    search_id: int,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> dict:
    run = owned_search(db, account, search_id)
    return {
        "id": run.id,
        "query": run.query,
        "status": run.status.value,
        "error": run.error,
        "result_count": run.result_count,
        "created_at": run.created_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


@router.get("/searches/{search_id}/results")
def results(
    search_id: int,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> list[dict]:
    owned_search(db, account, search_id)
    rows = db.scalars(
        select(CommunityResult)
        .where(CommunityResult.search_id == search_id)
        .order_by(CommunityResult.total_score.desc())
    ).all()
    return [serialize_result(row) for row in rows]


@router.get("/searches/{search_id}/export.{format}")
def export(
    search_id: int,
    format: str,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    owned_search(db, account, search_id)
    rows = db.scalars(
        select(CommunityResult)
        .where(CommunityResult.search_id == search_id)
        .order_by(CommunityResult.total_score.desc())
    ).all()
    data = [serialize_result(row) for row in rows]

    if format == "json":
        return JSONResponse(
            data,
            headers={"Content-Disposition": f'attachment; filename="tg-market-radar-search-{search_id}.json"'},
        )
    if format != "csv":
        raise HTTPException(404, "Supported formats: csv, json")

    fields = [
        "kind",
        "title",
        "username",
        "url",
        "public_contacts",
        "subscribers",
        "messages_30d",
        "avg_views",
        "relevance_score",
        "activity_score",
        "audience_score",
        "total_score",
        "referral_url",
        "max_referral_url",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for item in data:
        writer.writerow(
            {
                key: ", ".join(item[key]) if isinstance(item.get(key), list) else item.get(key, "")
                for key in fields
            }
        )
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="tg-market-radar-search-{search_id}.csv"'},
    )


@router.post("/billing/checkout")
def billing_checkout(
    plan: Plan,
    account: Account = Depends(current_account),
) -> dict:
    if not settings.stripe_secret_key:
        raise HTTPException(503, "Payments are not configured yet")

    price = (
        settings.stripe_price_pro
        if plan == Plan.pro
        else settings.stripe_price_team
        if plan == Plan.team
        else ""
    )
    if not price:
        raise HTTPException(400, "This plan has no checkout price")

    stripe.api_key = settings.stripe_secret_key
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price, "quantity": 1}],
        success_url=f"{settings.public_base_url}/app?billing=success",
        cancel_url=f"{settings.public_base_url}/app?billing=cancelled",
        client_reference_id=str(account.id),
        customer=account.stripe_customer_id or None,
        metadata={"plan": plan.value},
    )
    return {"url": session.url}
