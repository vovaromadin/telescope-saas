from app.scoring import extract_public_contacts, max_referral_link, referral_link, score_community


def test_extracts_only_contextual_public_contacts():
    text = "Новости @random_feed. Реклама и сотрудничество: @Sales_Manager"
    assert extract_public_contacts(text, "random_feed") == ["@sales_manager"]


def test_score_rewards_query_match_and_activity():
    strong = score_community("футбол прогноз", "Футбол и прогнозы", "Прогнозы матчей", ["футбол прогноз дня"] * 5, 10000, 40, 3000)
    weak = score_community("футбол прогноз", "Кулинария", "Рецепты", ["торт"] * 5, 10000, 2, 10)
    assert strong.total > weak.total
    assert strong.relevance > 90


def test_deep_links_are_sanitized():
    assert referral_link("MyBot", "src", "some-channel") == "https://t.me/MyBot?start=src_some-channel"
    assert max_referral_link("MaxBot", "src", "some-channel") == "https://max.ru/MaxBot?start=src_some-channel"

