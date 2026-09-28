from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from app.scoring import extract_public_contacts, score_community
from app.services.web_discovery import USER_AGENT, WebCommunity, parse_human_count


CHANNEL_LINK_RE = re.compile(
    r"(?:https?://(?:[a-z]{2}\.)?search-t\.me)?/channel/([A-Za-z0-9_]{5,32})",
    re.IGNORECASE,
)


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


def compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_card(anchor, query: str) -> WebCommunity | None:
    href = str(anchor.get("href") or "")
    match = CHANNEL_LINK_RE.search(href)
    if not match:
        return None

    username = match.group(1)
    if username.casefold().endswith("bot"):
        return None

    raw = compact_text(anchor.get_text(" ", strip=True))
    handle = f"@{username}"
    before, sep, after = raw.partition(handle)
    title = compact_text(before) if sep else username
    remainder = compact_text(after) if sep else raw

    # The card ends with audience/interaction metadata and "Open".
    remainder = re.sub(r"\s+Open(?:\s+.*)?$", "", remainder, flags=re.IGNORECASE)
    count_matches = list(
        re.finditer(r"(\d+(?:[\s.,]\d+)*)\s*(K|M|К|М|тыс\.?|млн\.?)?", remainder, re.IGNORECASE)
    )
    subscribers = 0
    if count_matches:
        candidates = []
        for match in count_matches:
            value = parse_human_count(match.group(0))
            suffix = (match.group(2) or "").strip()
            # Ignore phone numbers, timestamps and other large numbers from descriptions.
            # Audience counts without a suffix above 200M are not credible Telegram channel counts.
            if value <= 0:
                continue
            if not suffix and value > 200_000_000:
                continue
            candidates.append(min(value, 2_000_000_000))
        subscribers = max(candidates, default=0)

    description = remainder
    scores = score_community(
        query,
        title,
        description,
        [],
        subscribers,
        0,
        0,
    )

    return WebCommunity(
        telegram_id=f"searchtme:{username.casefold()}",
        kind="channel",
        title=(title or username)[:500],
        username=username,
        url=f"https://t.me/{username}",
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


class SearchTMeDiscovery:
    """Credential-free discovery via server-rendered search-t.me catalogue cards."""

    def __init__(self) -> None:
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ru,en;q=0.8",
        }

    async def search(self, query: str, limit: int) -> list[WebCommunity]:
        limit = max(1, min(int(limit or 20), 50))
        category = pick_searchtme_category(query)

        urls: list[str] = []
        for host in ("https://search-t.me", "https://en.search-t.me"):
            for page in range(1, 4):
                suffix = "" if page == 1 else f"?page={page}"
                urls.append(f"{host}/catalog/{category}{suffix}")

        rows: dict[str, WebCommunity] = {}

        async with httpx.AsyncClient(
            headers=self.headers,
            timeout=15,
            follow_redirects=True,
        ) as client:
            for url in urls:
                try:
                    response = await client.get(url)
                    if response.status_code != 200 or not response.text:
                        continue
                except Exception:
                    continue

                soup = BeautifulSoup(response.text, "html.parser")
                for anchor in soup.find_all("a", href=True):
                    row = parse_card(anchor, query)
                    if row is None:
                        continue
                    key = row.username.casefold()
                    existing = rows.get(key)
                    if existing is None or row.total_score > existing.total_score:
                        rows[key] = row
                    if len(rows) >= max(100, limit * 8):
                        break

        out = list(rows.values())
        # Category membership is already a strong relevance signal; score breaks ties.
        out.sort(
            key=lambda item: (item.relevance_score, item.audience_score, item.subscribers),
            reverse=True,
        )
        return out[:limit]
