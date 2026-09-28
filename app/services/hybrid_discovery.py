from __future__ import annotations

import asyncio

from app.config import Settings
from app.services.discovery import TelegramDiscovery
from app.services.telemetr_discovery import TelemetrDiscovery
from app.services.tgstat_discovery import TGStatDiscovery
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
        return rows[:limit], sources
