from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.growth_models import Campaign, CampaignStatus, ContentItem, ContentStatus, GrowthEvent, Lead, LeadStatus
from app.miniapp import current_account, owned_project
from app.models import Account, CommunityResult, Project, SearchRun


router = APIRouter(prefix="/api/app/growth", tags=["growth"])


class LeadCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=220)
    source: str = Field(default="manual", max_length=40)
    source_url: str = Field(default="", max_length=500)
    username: str = Field(default="", max_length=100)
    public_contact: str = Field(default="", max_length=220)
    intent_score: float = Field(default=0, ge=0, le=100)
    note: str = Field(default="", max_length=5000)

    @field_validator("display_name", mode="before")
    @classmethod
    def clamp_display_name(cls, value):
        return str(value or "").strip()[:220]

    @field_validator("source", mode="before")
    @classmethod
    def clamp_source(cls, value):
        return str(value or "manual").strip()[:40] or "manual"

    @field_validator("source_url", mode="before")
    @classmethod
    def clamp_source_url(cls, value):
        return str(value or "").strip()[:500]

    @field_validator("username", mode="before")
    @classmethod
    def clamp_username(cls, value):
        return str(value or "").strip().lstrip("@")[:100]

    @field_validator("public_contact", mode="before")
    @classmethod
    def clamp_public_contact(cls, value):
        return str(value or "").strip()[:220]

    @field_validator("note", mode="before")
    @classmethod
    def clamp_note(cls, value):
        return str(value or "")[:5000]


class LeadPatch(BaseModel):
    status: Optional[LeadStatus] = None
    intent_score: Optional[float] = Field(default=None, ge=0, le=100)
    note: Optional[str] = Field(default=None, max_length=5000)


class ContentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=220)
    body: str = Field(default="", max_length=20000)
    format: str = Field(default="post", max_length=40)


class ContentPatch(BaseModel):
    status: Optional[ContentStatus] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=220)
    body: Optional[str] = Field(default=None, max_length=20000)
    scheduled_at: Optional[datetime] = None
    published_url: Optional[str] = Field(default=None, max_length=500)


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=220)
    goal: str = Field(default="", max_length=5000)
    channel: str = Field(default="telegram", max_length=60)
    budget_daily: float = Field(default=0, ge=0)


class CampaignPatch(BaseModel):
    status: Optional[CampaignStatus] = None
    goal: Optional[str] = Field(default=None, max_length=5000)
    budget_daily: Optional[float] = Field(default=None, ge=0)


class EventCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=60)
    value: int = Field(default=1, ge=1, le=1_000_000)
    detail: str = Field(default="", max_length=5000)


def _owned_row(db: Session, account: Account, model, row_id: int):
    row = db.scalar(
        select(model)
        .join(Project, Project.id == model.project_id)
        .where(model.id == row_id, Project.account_id == account.id)
    )
    if not row:
        raise HTTPException(404, "Item not found")
    return row


def _lead(row: Lead) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "source": row.source,
        "source_url": row.source_url,
        "display_name": row.display_name,
        "username": row.username,
        "public_contact": row.public_contact,
        "intent_score": row.intent_score,
        "note": row.note,
        "status": row.status.value,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _content(row: ContentItem) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "title": row.title,
        "body": row.body,
        "format": row.format,
        "status": row.status.value,
        "scheduled_at": row.scheduled_at.isoformat() if row.scheduled_at else None,
        "published_url": row.published_url,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _campaign(row: Campaign) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "name": row.name,
        "goal": row.goal,
        "channel": row.channel,
        "status": row.status.value,
        "budget_daily": row.budget_daily,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("/projects/{project_id}/overview")
def overview(
    project_id: int,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> dict:
    owned_project(db, account, project_id)

    def count(model, *where) -> int:
        return int(db.scalar(select(func.count(model.id)).where(model.project_id == project_id, *where)) or 0)

    discovered = int(
        db.scalar(
            select(func.count(CommunityResult.id))
            .join(SearchRun, SearchRun.id == CommunityResult.search_id)
            .where(SearchRun.project_id == project_id)
        )
        or 0
    )
    searches = int(db.scalar(select(func.count(SearchRun.id)).where(SearchRun.project_id == project_id)) or 0)

    return {
        "leads_total": count(Lead),
        "leads_new": count(Lead, Lead.status == LeadStatus.new),
        "leads_qualified": count(Lead, Lead.status == LeadStatus.qualified),
        "leads_won": count(Lead, Lead.status == LeadStatus.won),
        "content_draft": count(ContentItem, ContentItem.status == ContentStatus.draft),
        "content_scheduled": count(ContentItem, ContentItem.status == ContentStatus.scheduled),
        "campaigns_active": count(Campaign, Campaign.status == CampaignStatus.active),
        "searches": searches,
        "communities_discovered": discovered,
    }


@router.get("/projects/{project_id}/leads")
def list_leads(
    project_id: int,
    status: Optional[LeadStatus] = Query(default=None),
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> list[dict]:
    owned_project(db, account, project_id)
    stmt = select(Lead).where(Lead.project_id == project_id)
    if status:
        stmt = stmt.where(Lead.status == status)
    rows = db.scalars(stmt.order_by(Lead.intent_score.desc(), Lead.created_at.desc())).all()
    return [_lead(row) for row in rows]


@router.post("/projects/{project_id}/leads")
def create_lead(
    project_id: int,
    payload: LeadCreate,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> dict:
    owned_project(db, account, project_id)

    if payload.source == "radar":
        matchers = []
        if payload.username:
            matchers.append(Lead.username == payload.username)
        if payload.source_url:
            matchers.append(Lead.source_url == payload.source_url)
        if matchers:
            existing = db.scalar(
                select(Lead)
                .where(Lead.project_id == project_id, or_(*matchers))
                .order_by(Lead.id.desc())
                .limit(1)
            )
            if existing:
                if payload.intent_score > existing.intent_score:
                    existing.intent_score = payload.intent_score
                    db.commit()
                    db.refresh(existing)
                return _lead(existing)

    row = Lead(project_id=project_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _lead(row)


@router.get("/leads/{lead_id}/draft")
def lead_draft(
    lead_id: int,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> dict:
    row = _owned_row(db, account, Lead, lead_id)
    project = db.get(Project, row.project_id)
    project_name = project.name if project else "наш проект"
    channel_name = row.display_name or (f"@{row.username}" if row.username else "ваш канал")
    contact = row.public_contact or (f"@{row.username}" if row.username else "")
    text = (
        f"Здравствуйте! Нашёл {channel_name} и хотел обсудить возможное сотрудничество с проектом «{project_name}». "
        "По тематике аудитория выглядит релевантной. Подскажите, пожалуйста, актуальны ли сейчас рекламные размещения "
        "или партнёрские интеграции и какие у вас условия? Если удобно, пришлите медиакит или актуальную статистику охватов."
    )
    return {
        "lead_id": row.id,
        "contact": contact,
        "subject": f"Сотрудничество — {project_name}",
        "text": text,
        "note": "Черновик подготовлен автоматически. Перед отправкой проверь условия и персонализируй текст.",
    }


@router.patch("/leads/{lead_id}")
def patch_lead(
    lead_id: int,
    payload: LeadPatch,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> dict:
    row = _owned_row(db, account, Lead, lead_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return _lead(row)


@router.get("/projects/{project_id}/content")
def list_content(
    project_id: int,
    status: Optional[ContentStatus] = Query(default=None),
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> list[dict]:
    owned_project(db, account, project_id)
    stmt = select(ContentItem).where(ContentItem.project_id == project_id)
    if status:
        stmt = stmt.where(ContentItem.status == status)
    rows = db.scalars(stmt.order_by(ContentItem.created_at.desc())).all()
    return [_content(row) for row in rows]


@router.post("/projects/{project_id}/content")
def create_content(
    project_id: int,
    payload: ContentCreate,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> dict:
    owned_project(db, account, project_id)
    row = ContentItem(project_id=project_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _content(row)


@router.patch("/content/{content_id}")
def patch_content(
    content_id: int,
    payload: ContentPatch,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> dict:
    row = _owned_row(db, account, ContentItem, content_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return _content(row)


@router.get("/projects/{project_id}/campaigns")
def list_campaigns(
    project_id: int,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> list[dict]:
    owned_project(db, account, project_id)
    rows = db.scalars(
        select(Campaign).where(Campaign.project_id == project_id).order_by(Campaign.created_at.desc())
    ).all()
    return [_campaign(row) for row in rows]


@router.post("/projects/{project_id}/campaigns")
def create_campaign(
    project_id: int,
    payload: CampaignCreate,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> dict:
    owned_project(db, account, project_id)
    row = Campaign(project_id=project_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _campaign(row)


@router.patch("/campaigns/{campaign_id}")
def patch_campaign(
    campaign_id: int,
    payload: CampaignPatch,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> dict:
    row = _owned_row(db, account, Campaign, campaign_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return _campaign(row)


@router.post("/projects/{project_id}/events")
def create_event(
    project_id: int,
    payload: EventCreate,
    account: Account = Depends(current_account),
    db: Session = Depends(get_db),
) -> dict:
    owned_project(db, account, project_id)
    row = GrowthEvent(project_id=project_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "kind": row.kind, "value": row.value, "created_at": row.created_at.isoformat()}
