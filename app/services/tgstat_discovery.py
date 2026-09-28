from __future__ import annotations

import httpx

from app.scoring import extract_public_contacts, score_community
from app.services.web_discovery import WebCommunity


class TGStatDiscovery:
    """Search public Telegram channels/chats through TGStat API."""

    BASE_URL = "https://api.tgstat.ru"

    def __init__(self, token: str, country: str = "ru", language: str = "russian"):
        self.token = (token or "").strip()
        self.country = (country or "ru").strip()
        self.language = (language or "russian").strip()

    async def search(self, query: str, limit: int) -> list[WebCommunity]:
        if not self.token:
            return []

        limit = max(1, min(int(limit or 20), 100))
        params = {
            "token": self.token,
            "q": query,
            "search_by_description": 1,
            "peer_type": "all",
            "country": self.country,
            "language": self.language,
            "limit": limit,
        }

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(f"{self.BASE_URL}/channels/search", params=params)

        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            return []

        response_data = payload.get("response") or {}
        items = response_data.get("items") or []
        rows: list[WebCommunity] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            username = str(item.get("username") or "").strip().lstrip("@")
            if not username:
                continue

            title = str(item.get("title") or username).strip()
            description = str(item.get("about") or "").strip()
            peer_type = str(item.get("peer_type") or "channel").casefold()
            subscribers = int(item.get("participants_count") or 0)
            tgstat_id = str(item.get("id") or item.get("tg_id") or username)

            link = str(item.get("link") or "").strip()
            if link.startswith("http://") or link.startswith("https://"):
                url = link
            elif link:
                url = "https://" + link.lstrip("/")
            else:
                url = f"https://t.me/{username}"

            scores = score_community(
                query,
                title,
                description,
                [],
                subscribers,
                0,
                0,
            )

            rows.append(
                WebCommunity(
                    telegram_id=f"tgstat:{tgstat_id}",
                    kind="group" if peer_type in {"chat", "group"} else "channel",
                    title=title[:500],
                    username=username,
                    url=url,
                    description=description[:4000],
                    public_contacts=extract_public_contacts(description, username),
                    subscribers=subscribers,
                    messages_scanned=0,
                    messages_30d=0,
                    avg_views=0,
                    relevance_score=scores.relevance,
                    activity_score=scores.activity,
                    audience_score=scores.audience,
                    total_score=scores.total,
                    snippets=[],
                )
            )

        rows.sort(key=lambda item: (item.total_score, item.subscribers), reverse=True)
        return rows[:limit]
