import hmac

from fastapi import Header, HTTPException, status

from app.config import get_settings


def require_admin(x_api_key: str = Header(default="")) -> None:
    expected = get_settings().admin_api_key
    if not expected or expected == "change-me" or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

