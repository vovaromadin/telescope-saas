import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException, status

from app.config import get_settings


def require_admin(x_api_key: str = Header(default="")) -> None:
    expected = get_settings().admin_api_key
    if not expected or expected == "change-me" or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def validate_telegram_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict:
    """Validate Telegram Mini App initData using Telegram's documented HMAC scheme."""
    if not init_data or not bot_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Telegram authentication required")

    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Telegram initData") from exc

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Telegram signature")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Telegram auth date") from exc

    now = int(time.time())
    if auth_date <= 0 or now - auth_date > max_age_seconds or auth_date - now > 60:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Telegram session expired")

    try:
        user = json.loads(pairs.get("user") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Telegram user data") from exc

    if not user.get("id"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Telegram user missing")

    return {"user": user, "auth_date": auth_date, "start_param": pairs.get("start_param", "")}
