from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel

from app.config import Settings
from app.scoring import extract_public_contacts, score_community


@dataclass
class Community:
    telegram_id: str
    kind: str
    title: str
    username: str
    url: str
    description: str = ""
    public_contacts: list[str] = field(default_factory=list)
    subscribers: int = 0
    messages_scanned: int = 0
    messages_30d: int = 0
    avg_views: float = 0
    snippets: list[str] = field(default_factory=list)
    relevance_score: float = 0
    activity_score: float = 0
    audience_score: float = 0
    total_score: float = 0


class TelegramDiscovery:
    """Telegram discovery with a strict community-only data boundary.

    It never requests participants and never stores sender IDs, names or usernames.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, limit: int) -> list[Community]:
        if not self.settings.telegram_ready:
            return []
        client = TelegramClient(
            StringSession(self.settings.tg_session),
            self.settings.tg_api_id,
            self.settings.tg_api_hash,
        )
        communities: dict[int, tuple[Channel, list]] = {}
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError("TG_SESSION is not authorized")
            async for message in client.iter_messages(None, search=query, limit=self.settings.message_scan_limit):
                chat = await message.get_chat()
                if not isinstance(chat, Channel) or not chat.username:
                    continue
                # Broadcast channels and public supergroups only; private entities have no public username.
                if not (getattr(chat, "broadcast", False) or getattr(chat, "megagroup", False)):
                    continue
                bucket = communities.setdefault(chat.id, (chat, []))[1]
                if message.message:
                    bucket.append(message)
                if len(communities) >= limit and all(len(value[1]) >= 2 for value in communities.values()):
                    break

            results = []
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            for chat, matched_messages in list(communities.values())[:limit]:
                try:
                    full = await client(GetFullChannelRequest(chat))
                    description = full.full_chat.about or ""
                    subscribers = int(full.full_chat.participants_count or 0)
                except Exception:
                    description, subscribers = "", int(getattr(chat, "participants_count", 0) or 0)
                recent_messages = [message async for message in client.iter_messages(chat, limit=100)]
                snippets = [re.sub(r"\s+", " ", message.message)[:280] for message in matched_messages[:10]]
                recent = [message for message in recent_messages if message.date and message.date >= cutoff]
                views = [message.views for message in recent_messages if getattr(message, "views", None)]
                avg_views = sum(views) / len(views) if views else 0
                scores = score_community(query, chat.title or "", description, snippets, subscribers, len(recent), avg_views)
                results.append(
                    Community(
                        telegram_id=str(chat.id),
                        kind="group" if chat.megagroup else "channel",
                        title=chat.title or chat.username,
                        username=chat.username,
                        url=f"https://t.me/{chat.username}",
                        description=description[:4000],
                        public_contacts=extract_public_contacts(description, chat.username),
                        subscribers=subscribers,
                        messages_scanned=len(recent_messages),
                        messages_30d=len(recent),
                        avg_views=round(avg_views, 1),
                        relevance_score=scores.relevance,
                        activity_score=scores.activity,
                        audience_score=scores.audience,
                        total_score=scores.total,
                        snippets=snippets,
                    )
                )
            return sorted(results, key=lambda item: item.total_score, reverse=True)
        finally:
            await client.disconnect()
