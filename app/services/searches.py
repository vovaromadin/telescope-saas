from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CommunityResult, SearchRun, SearchStatus
from app.scoring import max_referral_link, referral_link
from app.services.hybrid_discovery import HybridDiscovery


def safe_int32(value: object) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(number, 2_147_483_647))


def safe_text(value: object, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit]


async def execute_search(db: Session, run: SearchRun, referral_prefix: str, limit: Optional[int] = None, telegram_session: Optional[str] = None) -> None:
    settings = get_settings()
    search_settings = settings.model_copy(update={"tg_session": telegram_session}) if telegram_session else settings
    run.status = SearchStatus.running
    db.commit()
    try:
        communities, sources = await HybridDiscovery(search_settings).search(
            run.query,
            min(limit or search_settings.search_result_limit, search_settings.search_result_limit),
        )
        for item in communities:
            db.add(
                CommunityResult(
                    search_id=run.id,
                    telegram_id=safe_text(item.telegram_id, 64),
                    kind=safe_text(item.kind, 20),
                    title=safe_text(item.title, 300),
                    username=safe_text(item.username, 64),
                    url=safe_text(item.url, 255),
                    description=item.description,
                    public_contacts=json.dumps(item.public_contacts, ensure_ascii=False),
                    subscribers=safe_int32(item.subscribers),
                    messages_scanned=safe_int32(item.messages_scanned),
                    messages_30d=safe_int32(item.messages_30d),
                    avg_views=item.avg_views,
                    relevance_score=item.relevance_score,
                    activity_score=item.activity_score,
                    audience_score=item.audience_score,
                    total_score=item.total_score,
                    matched_snippets=json.dumps(item.snippets, ensure_ascii=False),
                    referral_url=safe_text(referral_link(settings.referral_bot_username, referral_prefix, item.username), 500),
                    max_referral_url=safe_text(max_referral_link(settings.max_bot_username, referral_prefix, item.username), 500),
                )
            )
        run.result_count = len(communities)
        run.status = SearchStatus.completed
        run.completed_at = datetime.now(timezone.utc)
        if sources:
            run.error = "Источники Radar: " + ", ".join(sources)
        else:
            run.error = "Источники Radar не вернули результатов."
        db.commit()
    except Exception as exc:
        print(f"Radar search {run.id} failed: {type(exc).__name__}: {exc}", flush=True)
        db.rollback()
        failed_run = db.get(SearchRun, run.id)
        if failed_run:
            failed_run.status = SearchStatus.failed
            failed_run.error = "Не удалось сохранить результаты Radar. Повтори поиск после обновления."
            failed_run.completed_at = datetime.now(timezone.utc)
            db.commit()
