from __future__ import annotations

import asyncio
import re

import httpx

from app.services.web_discovery import USER_AGENT, WebCommunity, WebDiscovery


CARD_RE = re.compile(
    r'(?:https?://(?:www\.)?open\.tg)?/c/([A-Za-z0-9_]{5,32})',
    re.IGNORECASE,
)
VISIBLE_HANDLE_RE = re.compile(r'@([A-Za-z0-9_]{5,32})')


def pick_category(query: str) -> str:
    q = query.casefold()
    groups = [
        ("sports", ("спорт", "футбол", "хоккей", "баскет", "теннис", "ставк", "букмек", "прогноз", "bet")),
        ("crypto", ("крипт", "bitcoin", "btc", "ethereum", "defi", "airdrop")),
        ("business", ("бизнес", "предприним", "продаж", "маркет", "стартап", "деньг")),
        ("tech", ("технолог", "it", "айти", "python", "разработ", "ии", "ai", "нейросет")),
        ("news", ("новост", "сми", "полит", "событ")),
        ("education", ("образован", "обучен", "курс", "школ", "универс")),
        ("gaming", ("игр", "game", "steam", "cs2", "dota")),
        ("music", ("музык", "music", "трек", "рэп")),
        ("movies", ("кино", "фильм", "сериал")),
        ("health", ("здоров", "медицин", "фитнес", "питани")),
        ("jobs", ("работ", "ваканс", "карьер")),
    ]
    for category, terms in groups:
        if any(term in q for term in terms):
            return category
    return "other"


class OpenTGDiscovery:
    """Credential-free discovery via public open.tg catalogue pages."""

    def __init__(self) -> None:
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ru,en;q=0.8",
        }

    async def search(self, query: str, limit: int) -> list[WebCommunity]:
        limit = max(1, min(int(limit or 20), 50))
        category = pick_category(query)
        urls = [
            f"https://open.tg/?cat={category}&lang=ru&sort=online",
            f"https://open.tg/?cat={category}&lang=ru&size=small&sort=online",
            f"https://open.tg/?cat={category}&lang=ru&fresh=week&sort=online",
        ]

        async with httpx.AsyncClient(headers=self.headers, timeout=15, follow_redirects=True) as client:
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
                candidates = list(CARD_RE.findall(html))
                candidates.extend(VISIBLE_HANDLE_RE.findall(html))
                for raw in candidates:
                    key = raw.casefold()
                    if key in seen or key.endswith("bot"):
                        continue
                    seen.add(key)
                    usernames.append(raw)
                    if len(usernames) >= max(80, limit * 8):
                        break

            if not usernames:
                return []

            analyzer = WebDiscovery()
            semaphore = asyncio.Semaphore(5)

            async def analyze(username: str) -> WebCommunity | None:
                async with semaphore:
                    return await analyzer._analyze(client, username, query)

            rows = await asyncio.gather(*(analyze(username) for username in usernames[: max(80, limit * 8)]))

        out = [row for row in rows if row is not None]
        # Keep useful topic matches first, but do not discard broad category candidates entirely.
        out.sort(key=lambda item: (item.relevance_score, item.total_score, item.subscribers), reverse=True)
        return out[:limit]
