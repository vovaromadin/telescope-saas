from __future__ import annotations

import asyncio

from app.config import Settings
from app.services.discovery import TelegramDiscovery
from app.services.telemetr_discovery import TelemetrDiscovery
from app.services.tgstat_discovery import TGStatDiscovery
from app.services.tgstat_public_discovery import TGStatPublicDiscovery
from app.services.opentg_discovery import OpenTGDiscovery
from app.services.searchtme_discovery import SearchTMeDiscovery
from app.services.web_discovery import WebDiscovery, WebCommunity


class HybridDiscovery:
    """Combine TGStat, Telemetr, public web, and optional Telegram account search."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, limit: int) -> tuple[list[WebCommunity], list[str]]:
        limit = max(1, min(int(limit or 20), self.settings.search_result_limit))
        sources: list[str] = []
        batches: list[list] = []

        tasks = []
        labels = []

        if self.settings.tgstat_api_token:
            tasks.append(
                TGStatDiscovery(
                    self.settings.tgstat_api_token,
                    self.settings.tgstat_country,
                    self.settings.tgstat_language,
                ).search(query, limit)
            )
            labels.append("TGStat")

        if self.settings.telemetr_api_key:
            tasks.append(TelemetrDiscovery(self.settings.telemetr_api_key).search(query, limit))
            labels.append("Telemetr")

        tasks.append(SearchTMeDiscovery().search(query, limit))
        labels.append("search-t.me")

        tasks.append(OpenTGDiscovery().search(query, limit))
        labels.append("open.tg")

        tasks.append(TGStatPublicDiscovery().search(query, limit))
        labels.append("TGStat public")

        tasks.append(WebDiscovery().search(query, limit))
        labels.append("public web")

        if self.settings.telegram_ready:
            tasks.append(TelegramDiscovery(self.settings).search(query, limit))
            labels.append("Telegram API")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for label, result in zip(labels, results):
            if isinstance(result, Exception):
                continue
            if result:
                sources.append(label)
                batches.append(result)

        merged: dict[str, WebCommunity] = {}
        for batch in batches:
            for item in batch:
                key = (item.username or item.telegram_id).casefold()
                current = merged.get(key)
                if current is None or item.total_score > current.total_score:
                    merged[key] = item

        rows = list(merged.values())
        rows.sort(key=lambda item: (item.total_score, item.subscribers), reverse=True)

        # Enrich the strongest public candidates from their own t.me/s pages.
        # This adds recent posting activity, average views and matched public snippets
        # without accessing member lists or private data.
        enrich_count = min(len(rows), min(limit, 10))
        if enrich_count:
            analyzer = WebDiscovery()
            enriched_rows = await asyncio.gather(
                *(analyzer.analyze_channel(item.username, query) for item in rows[:enrich_count]),
                return_exceptions=True,
            )
            enriched_any = False
            for base, enriched in zip(rows[:enrich_count], enriched_rows):
                if isinstance(enriched, Exception) or enriched is None:
                    continue
                enriched_any = True
                base.description = enriched.description or base.description
                base.public_contacts = list(dict.fromkeys((base.public_contacts or []) + (enriched.public_contacts or [])))
                base.subscribers = enriched.subscribers or base.subscribers
                base.messages_scanned = enriched.messages_scanned
                base.messages_30d = enriched.messages_30d
                base.avg_views = enriched.avg_views
                base.snippets = enriched.snippets
                base.relevance_score = max(base.relevance_score, enriched.relevance_score)
                base.activity_score = enriched.activity_score
                base.audience_score = max(base.audience_score, enriched.audience_score)
                base.total_score = max(base.total_score, enriched.total_score)
            if enriched_any and "t.me public" not in sources:
                sources.append("t.me public")
            rows.sort(key=lambda item: (item.total_score, item.subscribers), reverse=True)

        return rows[:limit], sources
