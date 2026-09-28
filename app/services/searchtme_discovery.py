from __future__ import annotations

import asyncio
import re

import httpx
from bs4 import BeautifulSoup

from app.services.web_discovery import USER_AGENT, WebCommunity, WebDiscovery


CHANNEL_LINK_RE = re.compile(
    r"(?:https?://(?:[a-z]{2}\.)?search-t\.me)?/channel/([A-Za-z0-9_]{5,32})",
    re.IGNORECASE,
)
HANDLE_RE = re.compile(r"@([A-Za-z0-9_]{5,32})")


def pick_searchtme_category(query: str) -> str:
    q = query.casefold()
    groups = [
        ("sport", ("спорт", "футбол", "хоккей", "баскет", "теннис", "ставк", "букмек", "прогноз", "bet")),
        ("crypto", ("крипт", "bitcoin", "btc", "ethereum", "defi", "airdrop")),
        ("business", ("бизнес", "предприним", "продаж", "стартап")),
        ("marketing", ("маркет", "smm", "реклам", "продвиж")),
        ("technology", ("технолог", "гаджет", "tech")),
        ("programming-it", ("python", "разработ", "программ", "код", "it", "айти")),
        ("news", ("новост", "сми", "событ")),
        ("education", ("образован", "обучен", "курс", "школ", "универс")),
        ("games", ("игр", "game", "steam", "cs2", "dota")),
        ("music", ("музык", "music", "трек", "рэп")),
        ("movies", ("кино", "фильм", "сериал")),
        ("health-fitness", ("здоров", "медицин", "фитнес", "питани")),
        ("career", ("работ", "ваканс", "карьер")),
        ("real-estate", ("недвиж", "квартир", "риелтор", "риэлтор")),
        ("cars-transport", ("авто", "машин", "автомоб")),
        ("construction-renovation", ("строит", "ремонт", "покраск", "отделк")),
    ]
    for category, terms in groups:
        if any(term in q for term in terms):
            return category
    return "all"


class SearchTMeDiscovery:
    """Credential-free discovery via public search-t.me catalogue pages."""

    def __init__(self) -> None:
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ru,en;q=0.8",
        }

    async def search(self, query: str, limit: int) -> list[WebCommunity]:
        limit = max(1, min(int(limit or 20), 50))
        category = pick_searchtme_category(query)
        # English mirror currently exposes a larger catalogue and server-rendered cards.
        urls = []
        for page in range(1, 4):
            suffix = "" if page == 1 else f"?page={page}"
            urls.append(f"https://en.search-t.me/catalog/{category}{suffix}")
        # Russian mirror is useful for local-language niches.
        for page in range(1, 3):
            suffix = "" if page == 1 else f"?page={page}"
            urls.append(f"https://search-t.me/catalog/{category}{suffix}")

        async with httpx.AsyncClient(
            headers=self.headers,
            timeout=15,
            follow_redirects=True,
        ) as client:
            pages: list[str] = []
            for url in urls:
                try:
                    response = await client.get(url)
                    if response.status_code == 200 and response.text:
                        pages.append(response.text)
                except Exception:
                    continue

            usernames: list[str] = []
            seen: set[str] = set()
            for html in pages:
                soup = BeautifulSoup(html, "html.parser")
                candidates: list[str] = []
                for anchor in soup.find_all("a", href=True):
                    href = str(anchor.get("href") or "")
                    match = CHANNEL_LINK_RE.search(href)
                    if match:
                        candidates.append(match.group(1))
                # Cards render @username as visible text even if the link format changes.
                candidates.extend(HANDLE_RE.findall(soup.get_text(" ", strip=True)))

                for raw in candidates:
                    key = raw.casefold()
                    if key in seen or key.endswith("bot"):
                        continue
                    seen.add(key)
                    usernames.append(raw)
                    if len(usernames) >= max(100, limit * 10):
                        break

            if not usernames:
                return []

            analyzer = WebDiscovery()
            semaphore = asyncio.Semaphore(6)

            async def analyze(username: str) -> WebCommunity | None:
                async with semaphore:
                    return await analyzer._analyze(client, username, query)

            rows = await asyncio.gather(
                *(analyze(username) for username in usernames[: max(100, limit * 10)])
            )

        out = [row for row in rows if row is not None]
        out.sort(
            key=lambda item: (item.relevance_score, item.total_score, item.subscribers),
            reverse=True,
        )
        return out[:limit]
