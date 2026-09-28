from __future__ import annotations

import httpx

from app.scoring import extract_public_contacts, score_community
from app.services.web_discovery import WebCommunity


class TelemetrDiscovery:
    """Search public Telegram channels through the Telemetr API."""

    BASE_URL = "https://api.telemetr.me"

    def __init__(self, api_key: str):
        self.api_key = (api_key or "").strip()

    async def search(self, query: str, limit: int) -> list[WebCommunity]:
        if not self.api_key:
            return []

        limit = max(1, min(int(limit or 20), 100))
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        params = {"query": query, "limit": limit}

        async with httpx.AsyncClient(headers=headers, timeout=15, follow_redirects=True) as client:
            response = await client.get(f"{self.BASE_URL}/v1/channels/search", params=params)

        if response.status_code in {401, 403, 429}:
            return []
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("items", [])
        else:
            items = []

        rows: list[WebCommunity] = []
        for item in items:
            if not isinstance(item, dict):
                continue

            username = str(item.get("username") or "").strip().lstrip("@")
            # Product boundary: only public communities with a public username.
            if not username:
                continue

            peer_id = str(item.get("peer_id") or username)
            title = str(item.get("title") or username).strip()
            description = str(item.get("about") or "").strip()
            peer_type = str(item.get("peer_type") or "channel").casefold()

            participants = item.get("participants")
            if isinstance(participants, dict):
                subscribers = int(participants.get("total") or participants.get("count") or 0)
            elif isinstance(participants, (int, float)):
                subscribers = int(participants)
            else:
                subscribers = int(item.get("participants_count") or item.get("subscribers") or 0)

            links = item.get("links") or {}
            url = ""
            if isinstance(links, dict):
                url = str(links.get("telegram") or "")
            if not url:
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
                    telegram_id=f"telemetr:{peer_id}",
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

        rows.sort(key=lambda item: item.total_score, reverse=True)
        return rows[:limit]
