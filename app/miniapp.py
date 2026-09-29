from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import hmac
import io
import json

import httpx

import qrcode
import qrcode.image.svg
from datetime import datetime, timezone

import stripe
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.models import Account, CommunityResult, Plan, Project, SearchRun, TelegramConnection, UsageEvent
from app.schemas import ProjectCreate, SearchCreate
from app.security import validate_telegram_init_data
from app.services.searches import execute_search
from app.services.web_discovery import WebDiscovery


router = APIRouter(prefix="/api/app", tags=["miniapp"])
settings = get_settings()
PLAN_LIMITS = {Plan.free: 5, Plan.pro: 100, Plan.team: 500}
QR_LOGIN_TASKS: dict[int, asyncio.Task] = {}


def _fernet() -> Fernet:
    digest = hashlib.sha256(f"{settings.admin_api_key}:tg-raketa-session".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii") if value else ""


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise HTTPException(500, "Не удалось расшифровать Telegram-сессию") from exc


def account_telegram_connection(db: Session, account_id: int) -> TelegramConnection | None:
    return db.scalar(select(TelegramConnection).where(TelegramConnection.account_id == account_id))


def telegram_display_name(me) -> str:
    first = (getattr(me, "first_name", "") or "").strip()
    last = (getattr(me, "last_name", "") or "").strip()
    username = (getattr(me, "username", "") or "").strip()
    name = " ".join(part for part in (first, last) if part).strip()
    if username:
        name = f"{name} (@{username})".strip()
    return name or "Telegram account"


def qr_svg(url: str) -> str:
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=7, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode("utf-8")


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
    x_api_key: str = Header(default="", alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> Account:
    # Browser dashboard fallback for the owner. Telegram Mini App keeps using
    # signed initData; the web dashboard may use the existing ADMIN_API_KEY.
    if x_api_key and hmac.compare_digest(x_api_key, settings.admin_api_key):
        account = db.scalar(
            select(Account)
            .where(Account.telegram_id.is_not(None))
            .order_by(Account.id.desc())
        )
        if not account:
            account = db.scalar(select(Account).order_by(Account.id))
        if not account:
            raise HTTPException(500, "No workspace configured")
        return account

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
        if not run:
            return
        project = db.get(Project, run.project_id)
        connection = account_telegram_connection(db, project.account_id) if project else None
        telegram_session = ""
        if connection and connection.status == "connected" and connection.session_encrypted:
            telegram_session = decrypt_secret(connection.session_encrypted)
        await execute_search(db, run, referral_prefix, limit, telegram_session=telegram_session or None)


@router.get("/me")
def me(account: Account = Depends(current_account), db: Session = Depends(get_db)) -> dict:
    used = monthly_used(db, account)
    limit = PLAN_LIMITS[account.plan]
    connection = account_telegram_connection(db, account.id)
    connected = bool(connection and connection.status == "connected" and connection.session_encrypted)
    return {
        "id": account.id,
        "name": account.name,
        "plan": account.plan.value,
        "monthly_limit": limit,
        "used": used,
        "remaining": max(0, limit - used),
        "telegram_ready": connected or settings.telegram_ready,
        "telegram_api_ready": bool(settings.tg_api_id and settings.tg_api_hash),
        "telemetr_ready": bool(settings.telemetr_api_key),
        "tgstat_ready": bool(settings.tgstat_api_token),
    }


@router.get("/radar/status")
async def radar_status(
    account: Account = Depends(current_account),
) -> dict:
    result = {
        "tgstat_configured": bool(settings.tgstat_api_token),
        "tgstat_ok": False,
        "tgstat_status": "not_configured",
        "tgstat_usage": None,
        "telemetr_configured": bool(settings.telemetr_api_key),
        "telemetr_ok": False,
        "telemetr_status": "not_configured",
        "telemetr_limits": None,
        "search_t_me": True,
        "public_web": True,
    }

    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        if settings.tgstat_api_token:
            try:
                response = await client.get(
                    "https://api.tgstat.ru/usage/stat",
                    params={"token": settings.tgstat_api_token},
                )
                result["tgstat_http_status"] = response.status_code
                payload = response.json()
                if response.status_code == 200 and isinstance(payload, dict) and payload.get("status") == "ok":
                    result["tgstat_ok"] = True
                    result["tgstat_status"] = "ok"
                    usage = payload.get("response")
                    if isinstance(usage, list):
                        result["tgstat_usage"] = [
                            {
                                "serviceKey": item.get("serviceKey"),
                                "requestCount": item.get("requestCount"),
                                "requestLimit": item.get("requestLimit"),
                                "dateEnd": item.get("dateEnd"),
                            }
                            for item in usage
                            if isinstance(item, dict)
                        ]
                else:
                    result["tgstat_status"] = "limited_or_invalid"
            except Exception as exc:
                result["tgstat_status"] = "unreachable"
                result["tgstat_error"] = type(exc).__name__

        if settings.telemetr_api_key:
            headers = {
                "Authorization": f"Bearer {settings.telemetr_api_key}",
                "Accept": "application/json",
            }
            try:
                response = await client.get("https://api.telemetr.me/v1/limits", headers=headers)
                result["telemetr_http_status"] = response.status_code
                if response.status_code == 200:
                    result["telemetr_ok"] = True
                    result["telemetr_status"] = "ok"
                    payload = response.json()
                    if isinstance(payload, dict):
                        result["telemetr_limits"] = {
                            "model": payload.get("model"),
                            "platform": payload.get("platform"),
                            "requests": payload.get("requests"),
                            "channels": payload.get("channels"),
                            "search": payload.get("search"),
                            "posts": payload.get("posts"),
                            "lifetime": payload.get("lifetime"),
                        }
                elif response.status_code == 401:
                    result["telemetr_status"] = "invalid_key"
                elif response.status_code == 403:
                    result["telemetr_status"] = "forbidden"
                elif response.status_code == 429:
                    result["telemetr_status"] = "rate_limited"
                else:
                    result["telemetr_status"] = "error"
            except Exception as exc:
                result["telemetr_status"] = "unreachable"
                result["telemetr_error"] = type(exc).__name__

    return result


@router.get("/radar/analyze")
async def radar_analyze(
    username: str,
    query: str = "",
    account: Account = Depends(current_account),
) -> dict:
    row = await WebDiscovery().analyze_channel(username, query)
    if not row:
        raise HTTPException(404, "Не удалось получить публичные данные канала")

    er = round((row.avg_views / row.subscribers * 100), 1) if row.subscribers > 0 and row.avg_views > 0 else 0.0
    reasons = []
    if row.relevance_score >= 70:
        reasons.append("высокое тематическое совпадение")
    elif row.relevance_score >= 45:
        reasons.append("среднее тематическое совпадение")
    else:
        reasons.append("совпадение по теме ограниченное")

    if row.messages_30d >= 20:
        reasons.append("канал публикуется регулярно")
    elif row.messages_30d > 0:
        reasons.append("канал активен, но публикуется умеренно")
    else:
        reasons.append("свежая активность не подтверждена")

    if row.avg_views > 0:
        reasons.append(f"средние просмотры около {int(row.avg_views):,}".replace(",", " "))

    if row.public_contacts:
        reasons.append("есть публичный контакт для сотрудничества")

    if row.relevance_score >= 65 and row.messages_30d >= 10:
        recommendation = "Подходит для шорт-листа. Сначала запросить условия размещения и сверить свежие охваты."
    elif row.relevance_score >= 45:
        recommendation = "Можно рассматривать как тестовую площадку после ручной проверки последних публикаций."
    else:
        recommendation = "Низкий приоритет: использовать только после дополнительной ручной проверки."

    return {
        "username": f"@{row.username}",
        "title": row.title,
        "url": row.url,
        "subscribers": row.subscribers,
        "messages_30d": row.messages_30d,
        "avg_views": row.avg_views,
        "estimated_er": er,
        "relevance_score": row.relevance_score,
        "activity_score": row.activity_score,
        "audience_score": row.audience_score,
        "total_score": row.total_score,
        "public_contacts": row.public_contacts,
        "snippets": row.snippets[:5],
        "why": ". ".join(reasons).capitalize() + ".",
        "recommendation": recommendation,
    }


@router.get("/telegram/connection")
def telegram_connection_status(
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> dict:
    connection = account_telegram_connection(db, account.id)
    return {
        "api_ready": bool(settings.tg_api_id and settings.tg_api_hash),
        "connected": bool(connection and connection.status == "connected" and connection.session_encrypted),
        "status": connection.status if connection else "disconnected",
        "display_name": connection.display_name if connection else "",
    }


async def _finish_qr_login(account_id: int, client: TelegramClient, qr) -> None:
    try:
        user = await qr.wait(timeout=90)
        with SessionLocal() as db:
            connection = account_telegram_connection(db, account_id)
            if not connection:
                connection = TelegramConnection(account_id=account_id)
                db.add(connection)
            connection.status = "connected"
            connection.session_encrypted = encrypt_secret(client.session.save())
            connection.display_name = telegram_display_name(user)
            db.commit()
    except SessionPasswordNeededError:
        with SessionLocal() as db:
            connection = account_telegram_connection(db, account_id)
            if connection:
                connection.status = "two_factor_required"
                db.commit()
    except asyncio.TimeoutError:
        with SessionLocal() as db:
            connection = account_telegram_connection(db, account_id)
            if connection and connection.status == "waiting_qr":
                connection.status = "expired"
                db.commit()
    except Exception:
        with SessionLocal() as db:
            connection = account_telegram_connection(db, account_id)
            if connection:
                connection.status = "error"
                db.commit()
    finally:
        try:
            await client.disconnect()
        finally:
            QR_LOGIN_TASKS.pop(account_id, None)


@router.post("/telegram/qr/start")
async def telegram_qr_start(
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> dict:
    if not settings.tg_api_id or not settings.tg_api_hash:
        raise HTTPException(503, "TG_API_ID и TG_API_HASH ещё не настроены")

    existing = QR_LOGIN_TASKS.pop(account.id, None)
    if existing:
        existing.cancel()
        try:
            await existing
        except asyncio.CancelledError:
            pass

    connection = account_telegram_connection(db, account.id)
    if not connection:
        connection = TelegramConnection(account_id=account.id)
        db.add(connection)
    connection.status = "waiting_qr"
    connection.display_name = ""
    db.commit()

    client = TelegramClient(StringSession(), settings.tg_api_id, settings.tg_api_hash)
    await client.connect()
    qr = await client.qr_login()
    QR_LOGIN_TASKS[account.id] = asyncio.create_task(_finish_qr_login(account.id, client, qr))
    return {
        "status": "waiting_qr",
        "login_url": qr.url,
        "qr_svg": qr_svg(qr.url),
        "expires_at": qr.expires.isoformat(),
    }


@router.delete("/telegram/connection")
def telegram_disconnect(
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> dict:
    connection = account_telegram_connection(db, account.id)
    if connection:
        connection.status = "disconnected"
        connection.session_encrypted = ""
        connection.pending_session_encrypted = ""
        connection.pending_phone_encrypted = ""
        connection.pending_code_hash_encrypted = ""
        connection.display_name = ""
        db.commit()
    return {"status": "disconnected"}


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
