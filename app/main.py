import asyncio
import csv
import io
import json
from datetime import datetime, timezone

import httpx
import stripe
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import Base, SessionLocal, engine, get_db
from app.models import Account, CommunityResult, Plan, Project, SearchRun, UsageEvent
from app.schemas import ProjectCreate, ProjectOut, SearchCreate, SearchOut
from app.miniapp import router as miniapp_router
from app.growth import router as growth_router
from app.security import require_admin
from app.services.searches import execute_search
from app.services.telemetr_discovery import TelemetrDiscovery
from app.services.tgstat_discovery import TGStatDiscovery
from app.services.tgstat_public_discovery import TGStatPublicDiscovery
from app.services.opentg_discovery import OpenTGDiscovery
from app.services.web_discovery import WebDiscovery


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.2.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(miniapp_router)
app.include_router(growth_router)

PLAN_LIMITS = {Plan.free: 5, Plan.pro: 100, Plan.team: 500}


async def configure_telegram_webhook() -> None:
    if not (settings.tg_bot_token and settings.tg_webhook_secret and settings.public_base_url):
        return
    webhook_url = f"{settings.public_base_url.rstrip('/')}/webhooks/telegram/{settings.tg_webhook_secret}"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{settings.tg_bot_token}/setWebhook",
            json={
                "url": webhook_url,
                "secret_token": settings.tg_webhook_secret,
                "allowed_updates": ["message"],
                "drop_pending_updates": False,
            },
        )
        response.raise_for_status()


@app.on_event("startup")
async def startup() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if not db.scalar(select(Account).limit(1)):
            db.add(Account(name="Default workspace", plan=Plan.free))
            db.commit()
    if settings.tg_bot_token:
        try:
            await configure_telegram_webhook()
            print(f"Telegram webhook configured for @{settings.tg_bot_username or 'bot'}", flush=True)
        except httpx.HTTPError as exc:
            print(f"Telegram webhook configuration failed: {type(exc).__name__}", flush=True)

    try:
        radar_check = await radar_health()
        print("RADAR_SELF_CHECK=" + json.dumps(radar_check, ensure_ascii=False), flush=True)
    except Exception as exc:
        print(f"RADAR_SELF_CHECK_FAILED={type(exc).__name__}:{str(exc)[:180]}", flush=True)


@app.get("/", response_class=HTMLResponse)
def landing() -> str:
    with open("app/static/landing.html", encoding="utf-8") as handle:
        return (
            handle.read()
            .replace("{{APP_NAME}}", settings.app_name)
            .replace("{{BOT_USERNAME}}", settings.tg_bot_username.lstrip("@"))
        )


@app.get("/admin", response_class=HTMLResponse)
def dashboard() -> str:
    with open("app/static/index.html", encoding="utf-8") as handle:
        return handle.read().replace("{{APP_NAME}}", settings.app_name)


@app.get("/app", response_class=HTMLResponse)
def mini_app() -> str:
    with open("app/static/miniapp.html", encoding="utf-8") as handle:
        return handle.read()


@app.get("/health/radar")
async def radar_health() -> dict:
    query = "ставки футбол"
    result = {
        "query": query,
        "telemetr": {"configured": bool(settings.telemetr_api_key), "count": 0, "error": None},
        "tgstat": {"configured": bool(settings.tgstat_api_token), "count": 0, "error": None},
        "open_tg": {"configured": True, "count": 0, "error": None},
        "tgstat_public": {"configured": True, "count": 0, "error": None},
        "public_web": {"configured": True, "count": 0, "error": None},
    }

    async def run_source(name: str, coro) -> None:
        try:
            rows = await coro
            result[name]["count"] = len(rows)
        except Exception as exc:
            result[name]["error"] = f"{type(exc).__name__}: {str(exc)[:180]}"

    tasks = [
        run_source("open_tg", OpenTGDiscovery().search(query, 10)),
        run_source("tgstat_public", TGStatPublicDiscovery().search(query, 10)),
        run_source("public_web", WebDiscovery().search(query, 10)),
    ]
    if settings.telemetr_api_key:
        tasks.append(run_source("telemetr", TelemetrDiscovery(settings.telemetr_api_key).search(query, 10)))
    if settings.tgstat_api_token:
        tasks.append(
            run_source(
                "tgstat",
                TGStatDiscovery(
                    settings.tgstat_api_token,
                    settings.tgstat_country,
                    settings.tgstat_language,
                ).search(query, 10),
            )
        )

    await asyncio.gather(*tasks)
    return result


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    db.execute(select(1))
    return {"status": "ok", "telegram_radar": settings.telegram_ready, "telegram_bot": bool(settings.tg_bot_token), "version": app.version}


def default_account(db: Session) -> Account:
    account = db.scalar(select(Account).order_by(Account.id))
    if not account:
        raise HTTPException(500, "No workspace configured")
    return account


def ensure_limit(db: Session, account: Account) -> None:
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = db.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.account_id == account.id,
            UsageEvent.event_type == "search",
            UsageEvent.created_at >= month_start,
        )
    )
    if int(used or 0) >= PLAN_LIMITS[account.plan]:
        raise HTTPException(402, f"Monthly {account.plan.value} plan limit reached")


@app.get("/api/meta", dependencies=[Depends(require_admin)])
def meta(db: Session = Depends(get_db)) -> dict:
    account = default_account(db)
    return {
        "telegram_ready": settings.telegram_ready,
        "plan": account.plan.value,
        "monthly_limit": PLAN_LIMITS[account.plan],
        "privacy_boundary": "Public communities and explicitly published business contacts only",
    }


@app.get("/api/projects", response_model=list[ProjectOut], dependencies=[Depends(require_admin)])
def list_projects(db: Session = Depends(get_db)):
    account = default_account(db)
    return db.scalars(select(Project).where(Project.account_id == account.id).order_by(Project.created_at.desc())).all()


@app.post("/api/projects", response_model=ProjectOut, dependencies=[Depends(require_admin)])
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    account = default_account(db)
    project = Project(account_id=account.id, **payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@app.get("/api/projects/{project_id}/searches", response_model=list[SearchOut], dependencies=[Depends(require_admin)])
def list_searches(project_id: int, db: Session = Depends(get_db)):
    return db.scalars(select(SearchRun).where(SearchRun.project_id == project_id).order_by(SearchRun.created_at.desc())).all()


@app.get("/api/searches/{search_id}", response_model=SearchOut, dependencies=[Depends(require_admin)])
def get_search(search_id: int, db: Session = Depends(get_db)):
    run = db.get(SearchRun, search_id)
    if not run:
        raise HTTPException(404, "Search not found")
    return run


async def run_search_background(run_id: int, referral_prefix: str, limit: int) -> None:
    with SessionLocal() as db:
        run = db.get(SearchRun, run_id)
        if run:
            await execute_search(db, run, referral_prefix, limit)


@app.post("/api/projects/{project_id}/searches", response_model=SearchOut, dependencies=[Depends(require_admin)])
async def create_search(project_id: int, payload: SearchCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    account = default_account(db)
    ensure_limit(db, account)
    project = db.scalar(select(Project).where(Project.id == project_id, Project.account_id == account.id))
    if not project:
        raise HTTPException(404, "Project not found")
    run = SearchRun(project_id=project.id, query=payload.query)
    db.add_all([run, UsageEvent(account_id=account.id, event_type="search")])
    db.commit()
    db.refresh(run)
    background.add_task(run_search_background, run.id, project.referral_prefix, payload.limit)
    return run


@app.get("/api/searches/{search_id}/results", dependencies=[Depends(require_admin)])
def results(search_id: int, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(CommunityResult).where(CommunityResult.search_id == search_id).order_by(CommunityResult.total_score.desc())
    ).all()
    return [serialize_result(row) for row in rows]


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


@app.get("/api/searches/{search_id}/export.{format}", dependencies=[Depends(require_admin)])
def export(search_id: int, format: str, db: Session = Depends(get_db)):
    rows = db.scalars(select(CommunityResult).where(CommunityResult.search_id == search_id).order_by(CommunityResult.total_score.desc())).all()
    data = [serialize_result(row) for row in rows]
    if format == "json":
        return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="search-{search_id}.json"'})
    if format != "csv":
        raise HTTPException(404, "Supported formats: csv, json")
    output = io.StringIO()
    fields = ["kind", "title", "username", "url", "public_contacts", "subscribers", "messages_30d", "avg_views", "relevance_score", "activity_score", "audience_score", "total_score", "referral_url", "max_referral_url"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for item in data:
        writer.writerow({key: ", ".join(item[key]) if isinstance(item.get(key), list) else item.get(key, "") for key in fields})
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="search-{search_id}.csv"'})


@app.post("/api/billing/checkout", dependencies=[Depends(require_admin)])
def billing_checkout(plan: Plan, db: Session = Depends(get_db)) -> dict:
    if not settings.stripe_secret_key:
        raise HTTPException(503, "Stripe is not configured")
    price = settings.stripe_price_pro if plan == Plan.pro else settings.stripe_price_team if plan == Plan.team else ""
    if not price:
        raise HTTPException(400, "This plan has no checkout price")
    account = default_account(db)
    stripe.api_key = settings.stripe_secret_key
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price, "quantity": 1}],
        success_url=f"{settings.public_base_url}/?billing=success",
        cancel_url=f"{settings.public_base_url}/?billing=cancelled",
        client_reference_id=str(account.id),
        customer=account.stripe_customer_id or None,
        metadata={"plan": plan.value},
    )
    return {"url": session.url}


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, stripe_signature: str = Header(default=""), db: Session = Depends(get_db)):
    if not settings.stripe_webhook_secret:
        raise HTTPException(503, "Stripe webhook is not configured")
    try:
        event = stripe.Webhook.construct_event(await request.body(), stripe_signature, settings.stripe_webhook_secret)
    except Exception as exc:
        raise HTTPException(400, "Invalid Stripe webhook") from exc
    if event["type"] == "checkout.session.completed":
        obj = event["data"]["object"]
        account = db.get(Account, int(obj["client_reference_id"]))
        if account:
            account.stripe_customer_id = obj.get("customer")
            account.stripe_subscription_id = obj.get("subscription")
            account.plan = Plan(obj.get("metadata", {}).get("plan", "pro"))
            db.commit()
    return {"received": True}


async def bot_send(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    if not settings.tg_bot_token:
        return
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{settings.tg_bot_token}/sendMessage",
            json=payload,
        )
        response.raise_for_status()


async def max_send(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    if not settings.max_bot_token:
        return
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            "https://platform-api2.max.ru/messages",
            params={"chat_id": chat_id},
            headers={"Authorization": settings.max_bot_token},
            json={"text": text},
        )
        response.raise_for_status()


def telegram_account(db: Session, chat_id: int, user: dict | None = None) -> Account:
    user = user or {}
    telegram_id = str(user.get("id") or chat_id)
    account = db.scalar(select(Account).where(Account.telegram_id == telegram_id))
    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    username = (user.get("username") or "").strip()
    name = " ".join(part for part in (first, last) if part).strip()
    if username:
        name = f"{name} (@{username})".strip()
    name = name or f"Telegram {telegram_id}"
    if not account:
        account = Account(name=name[:160], telegram_id=telegram_id, plan=Plan.free)
        db.add(account)
        db.commit()
        db.refresh(account)
    elif account.name != name[:160]:
        account.name = name[:160]
        db.commit()
        db.refresh(account)
    return account


async def handle_chat_command(
    chat_id: int,
    text: str,
    sender,
    user: dict | None = None,
    platform: str = "telegram",
) -> None:
    with SessionLocal() as db:
        account = telegram_account(db, chat_id, user) if platform == "telegram" else default_account(db)

        if text == "/start":
            markup = None
            if platform == "telegram":
                markup = {
                    "inline_keyboard": [[
                        {
                            "text": "🚀 Открыть TG Ракета",
                            "web_app": {"url": f"{settings.public_base_url.rstrip('/')}/app"},
                        }
                    ]]
                }
            await sender(
                chat_id,
                "TG Ракета ищет публичные Telegram-каналы и группы по любым нишам. "
                "Открой приложение кнопкой ниже или используй команды: /projects, /new Название, /search ID запрос, /plan",
                markup,
            )
            return

        if text == "/plan":
            month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            used = db.scalar(
                select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
                    UsageEvent.account_id == account.id,
                    UsageEvent.event_type == "search",
                    UsageEvent.created_at >= month_start,
                )
            )
            await sender(
                chat_id,
                f"Тариф: {account.plan.value}. Использовано: {int(used or 0)} из {PLAN_LIMITS[account.plan]} поисков в этом месяце.",
                None,
            )
            return

        if text == "/projects":
            items = db.scalars(
                select(Project)
                .where(Project.account_id == account.id)
                .order_by(Project.id)
            ).all()
            await sender(chat_id, "\n".join(f"{p.id}: {p.name}" for p in items) or "Проектов пока нет", None)
            return

        if text.startswith("/new "):
            project = Project(account_id=account.id, name=text[5:][:160])
            db.add(project)
            db.commit()
            db.refresh(project)
            await sender(chat_id, f"Проект создан: {project.id}", None)
            return

        if text.startswith("/search "):
            try:
                project_id_text, query = text[8:].split(" ", 1)
                project_id = int(project_id_text)
            except ValueError:
                await sender(chat_id, "Формат: /search ID запрос", None)
                return

            project = db.scalar(
                select(Project).where(Project.id == project_id, Project.account_id == account.id)
            )
            if not project:
                await sender(chat_id, "Проект не найден", None)
                return

            ensure_limit(db, account)
            run = SearchRun(project_id=project.id, query=query[:300])
            db.add_all([run, UsageEvent(account_id=account.id, event_type="search")])
            db.commit()
            db.refresh(run)
            await execute_search(db, run, project.referral_prefix)
            await sender(
                chat_id,
                f"Готово: {run.result_count} сообществ. Открой приложение: {settings.public_base_url}/app",
                None,
            )
            return

        await sender(chat_id, "Неизвестная команда. /start — открыть TG Ракета", None)


@app.post("/webhooks/telegram/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if not settings.tg_bot_token or not settings.tg_webhook_secret or secret != settings.tg_webhook_secret:
        raise HTTPException(404)
    supplied = request.headers.get("x-telegram-bot-api-secret-token", "")
    if supplied != settings.tg_webhook_secret:
        raise HTTPException(401, "Invalid Telegram webhook secret")
    update = await request.json()
    message = update.get("message") or {}
    chat_id = int((message.get("chat") or {}).get("id", 0))
    text = (message.get("text") or "").strip()
    user = message.get("from") or {}
    if not chat_id:
        return {"ok": True}
    await handle_chat_command(chat_id, text, bot_send, user=user, platform="telegram")
    return {"ok": True}


@app.post("/webhooks/max")
async def max_webhook(request: Request):
    if not settings.max_bot_token:
        raise HTTPException(404)
    # MAX sends the configured subscription secret in the request header.
    supplied = request.headers.get("x-max-bot-api-secret", "")
    if settings.max_webhook_secret and supplied != settings.max_webhook_secret:
        raise HTTPException(401, "Invalid MAX webhook secret")
    update = await request.json()
    update_type = update.get("update_type")
    if update_type == "bot_started":
        await max_send(int(update.get("chat_id", 0)), "TG Ракета готов. /start — команды")
        return {"ok": True}
    if update_type != "message_created":
        return {"ok": True}
    message = update.get("message") or {}
    recipient = message.get("recipient") or {}
    body = message.get("body") or {}
    chat_id = int(recipient.get("chat_id") or update.get("chat_id") or 0)
    text = (body.get("text") or "").strip()
    if chat_id and text:
        await handle_chat_command(chat_id, text, max_send, platform="max")
    return {"ok": True}
