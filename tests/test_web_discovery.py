from app.services.web_discovery import (
    extract_usernames_from_html,
    parse_audience_count,
    parse_human_count,
)


def test_extract_public_telegram_usernames():
    html = """
    <a href="https://t.me/s/channel_one">one</a>
    <a href="https://t.me/channel_two">two</a>
    <a href="https://t.me/share/url?url=x">share</a>
    <a href="https://t.me/sample_bot">bot</a>
    """
    assert extract_usernames_from_html(html) == ["channel_one", "channel_two"]


def test_parse_human_counts():
    assert parse_human_count("12.5K") == 12_500
    assert parse_human_count("1,2 млн") == 1_200_000
    assert parse_human_count("987") == 987


def test_parse_audience_kind():
    assert parse_audience_count("12.5K subscribers") == (12_500, "channel")
    assert parse_audience_count("8 421 участников") == (8_421, "group")
