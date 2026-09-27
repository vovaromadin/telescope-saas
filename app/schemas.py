from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=2000)
    referral_prefix: str = Field(default="src", pattern=r"^[a-zA-Z0-9_-]{1,32}$")


class SearchCreate(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    limit: int = Field(default=30, ge=1, le=100)


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str
    referral_prefix: str
    created_at: datetime
    model_config = {"from_attributes": True}


class SearchOut(BaseModel):
    id: int
    project_id: int
    query: str
    status: str
    result_count: int
    error: str
    created_at: datetime
    completed_at: Optional[datetime]
    model_config = {"from_attributes": True}
