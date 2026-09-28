from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from app.scoring import extract_public_contacts, score_community


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
USERNAME_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]{5,32})(?:[/?#&\s\"'<>]|$)",
    re.IGNORECASE,
)
TGSTAT_USERNAME_RE = re.compile(
    r"(?:https?://)?(?:www\.)?tgstat\.(?:ru|com)/channel/(?:%40|@)?([A-Za-z0-9_]{5,32})(?:[/?#&\s\"'<>]|$)",
    re.IGNORECASE,
)
TGSTAT_RELATIVE_RE = re.compile(
    r"(?:^|[\\\"'\s=])/(?:ru/)?channel/(?:%40|@)?([A-Za-z0-9_]{5,32})(?:[/?#&\\\"'\s<>]|$)",
    re.IGNORECASE,
)
SKIP_USERNAMES = {
    "share", "iv", "addstickers", "proxy", "joinchat", "boost", "addemoji",
    "login", "blog", "apps", "addlist", "confirmphone", "setlanguage", "bg",
}
COUNT_RE = re.compile(r"(\d+(?:[\s.,]\d+)*)\s*(k|m|к|м|тыс\.?|млн\.?)?", re.IGNORECASE)
AUDIENCE_RE = re.compile(
    r"(\d+(?:[\s.,]\d+)*)\s*(k|m|к|м|тыс\.?|млн\.?)?\s*"
    r"(subscribers?|members?|подписчик\w*|участник\w*)",
    re.IGNORECASE,
)


@dataclass
class WebCommunity:
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


def normalize_username(value: str) -> str:
    value = value.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", value):
        return ""
    lowered = value.casefold()
    if lowered in SKIP_USERNAMES or lowered.endswith("bot"):
        return ""
    return value


def extract_usernames_from_html(html: str) -> list[str]:
    if not html:
        return []
    decoded = unquote(html)
    candidates: list[str] = [match.group(1) for match in USERNAME_RE.finditer(decoded)]
    candidates.extend(match.group(1) for match in TGSTAT_USERNAME_RE.finditer(decoded))
    candidates.extend(match.group(1) for match in TGSTAT_RELATIVE_RE.finditer(decoded))

    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        parsed = urlparse(href)
        if parsed.netloc.endswith("duckduckgo.com"):
            href = parse_qs(parsed.query).get("uddg", [href])[0]
        href = unquote(href)
        candidates.extend(match.group(1) for match in USERNAME_RE.finditer(href))
        candidates.extend(match.group(1) for match in TGSTAT_USERNAME_RE.finditer(href))
        candidates.extend(match.group(1) for match in TGSTAT_RELATIVE_RE.finditer(href))

    out: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        username = normalize_username(raw)
        if not username:
            continue
        key = username.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(username)
    return out


def parse_human_count(value: str) -> int:
    match = COUNT_RE.search((value or "").replace("\xa0", " "))
    if not match:
        return 0
    raw = match.group(1).replace(" ", "").replace(",", ".")
    try:
        number = float(raw)
    except ValueError:
        return 0
    suffix = (match.group(2) or "").casefold().rstrip(".")
    if suffix in {"k", "к", "тыс"}:
        number *= 1_000
    elif suffix in {"m", "м", "млн"}:
        number *= 1_000_000
    return int(number)


def parse_audience_count(text: str) -> tuple[int, str]:
    match = AUDIENCE_RE.search((text or "").replace("\xa0", " "))
    if not match:
        return 0, "channel"
    count = parse_human_count(" ".join(part for part in match.groups()[:2] if part))
    label = (match.group(3) or "").casefold()
    kind = "group" if ("member" in label or "участ" in label) else "channel"
    return count, kind


def parse_view_count(value: str) -> int:
    return parse_human_count(value)


def query_terms(query: str) -> list[str]:
    return [token.casefold() for token in re.findall(r"[\w-]{2,}", query, flags=re.UNICODE)]


def stable_web_id(username: str) -> str:
    digest = hashlib.sha1(username.casefold().encode("utf-8")).hexdigest()[:16]
    return f"web:{digest}"


class WebDiscovery:
    """Credential-free discovery over public web pages only.

    No member lists, sender identities or private content are requested or stored.
    """

    def __init__(self) -> None:
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ru,en;q=0.8",
        }

    async def _get(self, client: httpx.AsyncClient, url: str, retries: int = 2) -> str:
        last_error: Exception | None = None
        for attempt in range(max(1, retries)):
            try:
                response = await client.get(url, timeout=10)
                response.raise_for_status()
                return response.text
            except Exception as exc:
                last_error = exc
                if attempt + 1 < retries:
                    await asyncio.sleep(0.4 * (attempt + 1))
        if last_error:
            return ""
        return ""

    async def _search_engine_candidates(
        self,
        client: httpx.AsyncClient,
        query: str,
        cap: int,
    ) -> list[str]:
        compact = " ".join(query_terms(query)[:8]) or query
        search_queries = [
            f"site:t.me/s {compact}",
            f"site:t.me {compact}",
            f"site:tgstat.ru/channel {compact}",
            f"Telegram {compact} t.me",
            f"телеграм канал {compact}",
        ]
        folded = compact.casefold()
        if any(term in folded for term in ("ставк", "прогноз", "букмек", "bet", "football", "футбол")):
            search_queries.extend([
                f"site:tgstat.ru/gambling {compact}",
                f"site:t.me/s ставки прогнозы спорт футбол",
                f"site:tgstat.ru/channel ставки прогнозы спорт футбол",
            ])
        urls: list[str] = []
        for search_query in search_queries:
            encoded = quote_plus(search_query)
            urls.append(f"https://html.duckduckgo.com/html/?q={encoded}")
            urls.append(f"https://www.bing.com/search?q={encoded}")

        pages = await asyncio.gather(*(self._get(client, url, retries=1) for url in urls))
        usernames: list[str] = []
        seen: set[str] = set()
        for html in pages:
            for username in extract_usernames_from_html(html):
                key = username.casefold()
                if key in seen:
                    continue
                seen.add(key)
                usernames.append(username)
                if len(usernames) >= cap:
                    return usernames
        return usernames

    async def _expand_mentions(
        self,
        client: httpx.AsyncClient,
        usernames: list[str],
        cap: int,
    ) -> list[str]:
        pages = await asyncio.gather(
            *(self._get(client, f"https://t.me/s/{username}", retries=1) for username in usernames[:12])
        )
        discovered: list[str] = []
        seen = {username.casefold() for username in usernames}
        for html in pages:
            for username in extract_usernames_from_html(html):
                key = username.casefold()
                if key in seen:
                    continue
                seen.add(key)
                discovered.append(username)
                if len(discovered) >= cap:
                    return discovered
        return discovered

    async def _analyze(
        self,
        client: httpx.AsyncClient,
        username: str,
        query: str,
    ) -> WebCommunity | None:
        profile_html, posts_html = await asyncio.gather(
            self._get(client, f"https://t.me/{username}"),
            self._get(client, f"https://t.me/s/{username}"),
        )
        if not profile_html and not posts_html:
            return None

        profile = BeautifulSoup(profile_html, "html.parser")
        posts = BeautifulSoup(posts_html, "html.parser")

        title_node = (
            profile.select_one(".tgme_page_title span")
            or profile.select_one(".tgme_page_title")
            or posts.select_one(".tgme_channel_info_header_title span")
            or posts.select_one(".tgme_channel_info_header_title")
        )
        desc_node = (
            profile.select_one(".tgme_page_description")
            or posts.select_one(".tgme_channel_info_description")
        )
        extra_node = profile.select_one(".tgme_page_extra")
        title = re.sub(r"\s+", " ", title_node.get_text(" ", strip=True)).strip() if title_node else username
        description = (
            re.sub(r"\s+", " ", desc_node.get_text(" ", strip=True)).strip()
            if desc_node
            else ""
        )

        counter_parts: list[str] = []
        if extra_node:
            counter_parts.append(extra_node.get_text(" ", strip=True))
        for counter in posts.select(".tgme_channel_info_counter"):
            counter_parts.append(counter.get_text(" ", strip=True))
        audience_text = " ".join(counter_parts)
        subscribers, kind = parse_audience_count(audience_text)

        message_nodes = posts.select(".tgme_widget_message")
        if subscribers <= 0 and not message_nodes and "tgme_channel_info" not in posts_html:
            # Usually a user profile, bot or unavailable handle rather than a public community.
            return None

        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        terms = query_terms(query)
        recent_count = 0
        views: list[int] = []
        snippets: list[str] = []

        for node in message_nodes[:80]:
            text_node = node.select_one(".tgme_widget_message_text")
            time_node = node.select_one("time")
            views_node = node.select_one(".tgme_widget_message_views")
            text = re.sub(r"\s+", " ", text_node.get_text(" ", strip=True)).strip() if text_node else ""

            if time_node:
                raw_date = time_node.get("datetime")
                if isinstance(raw_date, str):
                    try:
                        date = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                        if date >= cutoff:
                            recent_count += 1
                    except ValueError:
                        pass

            if views_node:
                value = parse_view_count(views_node.get_text(" ", strip=True))
                if value:
                    views.append(value)

            folded = text.casefold()
            if text and (not terms or any(term in folded for term in terms)):
                snippets.append(text[:280])
                if len(snippets) >= 10:
                    break

        avg_views = (sum(views) / len(views)) if views else 0.0
        scores = score_community(
            query,
            title,
            description,
            snippets,
            subscribers,
            recent_count,
            avg_views,
        )
        return WebCommunity(
            telegram_id=stable_web_id(username),
            kind=kind,
            title=title[:500],
            username=username,
            url=f"https://t.me/{username}",
            description=description[:4000],
            public_contacts=extract_public_contacts(description, username),
            subscribers=subscribers,
            messages_scanned=min(len(message_nodes), 80),
            messages_30d=recent_count,
            avg_views=round(avg_views, 1),
            relevance_score=scores.relevance,
            activity_score=scores.activity,
            audience_score=scores.audience,
            total_score=scores.total,
            snippets=snippets,
        )

    async def search(self, query: str, limit: int) -> list[WebCommunity]:
        limit = max(1, min(limit, 100))
        candidate_cap = max(30, min(180, limit * 4))
        limits = httpx.Limits(max_connections=12, max_keepalive_connections=6)

        async with httpx.AsyncClient(
            headers=self.headers,
            follow_redirects=True,
            limits=limits,
        ) as client:
            seeds = await self._search_engine_candidates(client, query, candidate_cap)
            if not seeds:
                return []

            extra = await self._expand_mentions(
                client,
                seeds,
                max(0, candidate_cap - len(seeds)),
            )
            candidates = (seeds + extra)[:candidate_cap]

            semaphore = asyncio.Semaphore(6)

            async def analyze_one(username: str) -> WebCommunity | None:
                async with semaphore:
                    return await self._analyze(client, username, query)

            analyzed = await asyncio.gather(*(analyze_one(username) for username in candidates))

        rows = [row for row in analyzed if row is not None]
        rows.sort(key=lambda item: item.total_score, reverse=True)
        return rows[:limit]
