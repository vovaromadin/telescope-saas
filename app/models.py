from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Plan(str, enum.Enum):
    free = "free"
    pro = "pro"
    team = "team"


class SearchStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), default="Workspace")
    telegram_id: Mapped[Optional[str]] = mapped_column(String(32), unique=True, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(320), unique=True, nullable=True)
    plan: Mapped[Plan] = mapped_column(Enum(Plan), default=Plan.free)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    referral_prefix: Mapped[str] = mapped_column(String(64), default="src")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    searches: Mapped[list["SearchRun"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class SearchRun(Base):
    __tablename__ = "search_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(String(300), index=True)
    status: Mapped[SearchStatus] = mapped_column(Enum(SearchStatus), default=SearchStatus.queued)
    error: Mapped[str] = mapped_column(Text, default="")
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    project: Mapped[Project] = relationship(back_populates="searches")
    results: Mapped[list["CommunityResult"]] = relationship(back_populates="search", cascade="all, delete-orphan")


class CommunityResult(Base):
    __tablename__ = "community_results"
    __table_args__ = (UniqueConstraint("search_id", "telegram_id", name="uq_search_community"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    search_id: Mapped[int] = mapped_column(ForeignKey("search_runs.id", ondelete="CASCADE"), index=True)
    telegram_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(300))
    username: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    public_contacts: Mapped[str] = mapped_column(Text, default="")
    subscribers: Mapped[int] = mapped_column(Integer, default=0)
    messages_scanned: Mapped[int] = mapped_column(Integer, default=0)
    messages_30d: Mapped[int] = mapped_column(Integer, default=0)
    avg_views: Mapped[float] = mapped_column(Float, default=0)
    relevance_score: Mapped[float] = mapped_column(Float, default=0)
    activity_score: Mapped[float] = mapped_column(Float, default=0)
    audience_score: Mapped[float] = mapped_column(Float, default=0)
    total_score: Mapped[float] = mapped_column(Float, default=0, index=True)
    matched_snippets: Mapped[str] = mapped_column(Text, default="")
    referral_url: Mapped[str] = mapped_column(String(500), default="")
    max_referral_url: Mapped[str] = mapped_column(String(500), default="")
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    search: Mapped[SearchRun] = relationship(back_populates="results")


class TelegramConnection(Base):
    __tablename__ = "telegram_connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="disconnected")
    session_encrypted: Mapped[str] = mapped_column(Text, default="")
    pending_session_encrypted: Mapped[str] = mapped_column(Text, default="")
    pending_phone_encrypted: Mapped[str] = mapped_column(Text, default="")
    pending_code_hash_encrypted: Mapped[str] = mapped_column(Text, default="")
    display_name: Mapped[str] = mapped_column(String(220), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
