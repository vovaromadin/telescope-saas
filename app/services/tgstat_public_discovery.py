from __future__ import annotations

import asyncio
from urllib.parse import quote_plus

import httpx

from app.services.web_discovery import (
    USER_AGENT,
    WebCommunity,
    WebDiscovery,
    extract_usernames_from_html,
)


class TGStatPublicDiscovery:
    """Credential-free discovery from public TGStat search pages."""

    def __init__(self) -> None:
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ru,en;q=0.8",
        }

    async def search(self, query: str, limit: int) -> list[WebCommunity]:
        limit = max(1, min(int(limit or 20), 50))
        encoded = quote_plus(query)
        urls = [
            f"https://tgstat.ru/channels/search?q={encoded}",
            f"https://tgstat.ru/search?q={encoded}",
            f"https://tgstat.com/search?q={encoded}",
        ]

        async with httpx.AsyncClient(
            headers=self.headers,
            timeout=15,
            follow_redirects=True,
        ) as client:
            pages = []
            for url in urls:
                try:
                    response = await client.get(url)
                    if response.status_code == 200:
                        pages.append(response.text)
                except Exception:
                    continue

            usernames: list[str] = []
            seen: set[str] = set()
            for html in pages:
                for username in extract_usernames_from_html(html):
                    key = username.casefold()
                    if key in seen:
                        continue
                    seen.add(key)
                    usernames.append(username)
                    if len(usernames) >= max(30, limit * 4):
                        break

            if not usernames:
                return []

            analyzer = WebDiscovery()
            semaphore = asyncio.Semaphore(5)

            async def analyze(username: str) -> WebCommunity | None:
                async with semaphore:
                    return await analyzer._analyze(client, username, query)

            rows = await asyncio.gather(*(analyze(username) for username in usernames[: max(30, limit * 4)]))

        out = [row for row in rows if row is not None]
        out.sort(key=lambda item: (item.total_score, item.subscribers), reverse=True)
        return out[:limit]
