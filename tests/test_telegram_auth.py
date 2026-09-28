import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from app.security import validate_telegram_init_data


def make_init_data(token: str, user_id: int = 12345) -> str:
    values = {
        "auth_date": str(int(time.time())),
        "query_id": "AAEAA-test",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_valid_telegram_init_data():
    token = "123456:TEST_TOKEN"
    parsed = validate_telegram_init_data(make_init_data(token), token)
    assert parsed["user"]["id"] == 12345


def test_invalid_telegram_init_data():
    token = "123456:TEST_TOKEN"
    init_data = make_init_data(token).replace("12345", "99999")
    try:
        validate_telegram_init_data(init_data, token)
        assert False, "tampered initData must fail"
    except Exception:
        assert True
