import math
import re
from dataclasses import dataclass


HANDLE_RE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{4,31})")
CONTACT_CONTEXT_RE = re.compile(
    r"(?:реклам\w*|сотруднич\w*|админ\w*|менеджер\w*|advertis\w*|contact\w*|partner\w*)"
    r"[^@\n]{0,80}@([A-Za-z][A-Za-z0-9_]{4,31})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Scores:
    relevance: float
    activity: float
    audience: float
    total: float


def terms(query: str) -> list[str]:
    return [token.casefold() for token in re.findall(r"[\w-]{2,}", query, re.UNICODE)]


def extract_public_contacts(text: str, community_username: str = "") -> list[str]:
    """Extract only handles explicitly presented near contact/admin/ads language."""
    found = {match.casefold() for match in CONTACT_CONTEXT_RE.findall(text or "")}
    found.discard(community_username.casefold().lstrip("@"))
    return [f"@{handle}" for handle in sorted(found)]


def score_community(
    query: str,
    title: str,
    description: str,
    snippets: list[str],
    subscribers: int,
    messages_30d: int,
    avg_views: float,
) -> Scores:
    needles = terms(query)
    title_text = title.casefold()
    body = f"{description} {' '.join(snippets)}".casefold()
    if needles:
        title_hits = sum(1 for word in needles if word in title_text) / len(needles)
        body_hits = sum(1 for word in needles if word in body) / len(needles)
        snippet_hits = sum(any(word in item.casefold() for word in needles) for item in snippets)
        density = min(1.0, snippet_hits / max(3, len(snippets)))
        relevance = 100 * (0.40 * title_hits + 0.40 * body_hits + 0.20 * density)
    else:
        relevance = 0.0

    post_component = min(1.0, messages_30d / 60)
    view_component = min(1.0, avg_views / max(100.0, subscribers * 0.25)) if subscribers else 0.0
    activity = 100 * (0.7 * post_component + 0.3 * view_component)
    audience = min(100.0, 100 * math.log10(max(1, subscribers)) / 6)
    total = 0.55 * relevance + 0.25 * activity + 0.20 * audience
    return Scores(*(round(value, 1) for value in (relevance, activity, audience, total)))


def referral_link(bot_username: str, prefix: str, community_username: str) -> str:
    if not bot_username:
        return ""
    safe_source = re.sub(r"[^A-Za-z0-9_-]", "_", community_username)[:40]
    safe_prefix = re.sub(r"[^A-Za-z0-9_-]", "_", prefix)[:20]
    return f"https://t.me/{bot_username.lstrip('@')}?start={safe_prefix}_{safe_source}"


def max_referral_link(bot_username: str, prefix: str, community_username: str) -> str:
    if not bot_username:
        return ""
    safe_source = re.sub(r"[^A-Za-z0-9_-]", "_", community_username)[:60]
    safe_prefix = re.sub(r"[^A-Za-z0-9_-]", "_", prefix)[:30]
    return f"https://max.ru/{bot_username.lstrip('@')}?start={safe_prefix}_{safe_source}"[:300]
