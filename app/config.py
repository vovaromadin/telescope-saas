from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TG Ракета"
    app_env: str = "development"
    public_base_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./telescope.db"
    admin_api_key: str = "change-me"
    telemetr_api_key: str = ""
    tgstat_api_token: str = ""
    tgstat_country: str = "ru"
    tgstat_language: str = "russian"

    tg_api_id: Optional[int] = None
    tg_api_hash: str = ""
    tg_session: str = ""
    tg_bot_token: str = ""
    tg_bot_username: str = ""
    tg_webhook_secret: str = ""
    tg_admin_chat_ids: str = ""
    referral_bot_username: str = ""
    max_bot_token: str = ""
    max_bot_username: str = ""
    max_webhook_secret: str = ""

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_pro: str = ""
    stripe_price_team: str = ""

    search_result_limit: int = 100
    message_scan_limit: int = 300
    retention_days: int = 90

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def telegram_ready(self) -> bool:
        return bool(self.tg_api_id and self.tg_api_hash and self.tg_session)

    @property
    def bot_admin_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.tg_admin_chat_ids.split(",") if x.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
