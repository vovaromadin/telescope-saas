# TeleScope

SaaS для поиска и оценки **публичных Telegram-каналов и групп** по произвольным запросам. TeleScope не собирает участников, профили авторов сообщений и не отправляет холодные личные сообщения.

## Возможности

- проекты и история поисков;
- глобальный поиск публичных сообщений через отдельную Telegram MTProto-сессию;
- строгий фильтр: только публичные каналы и супергруппы с username;
- релевантность, активность, размер аудитории и итоговый скоринг;
- только явно опубликованные контакты рекламы/админов из описания сообщества;
- CSV и JSON;
- Telegram и MAX deep links для атрибуции источников;
- Telegram Bot и MAX Bot webhooks как интерфейсы к одному SaaS;
- web/admin панель;
- тарифы, месячные лимиты и опциональная Stripe-подписка;
- PostgreSQL в production, SQLite для локального запуска.

## Важное ограничение MAX

Официальный MAX Bot API не предоставляет глобальный поиск по публичным каналам. Он позволяет читать сообщения только в чатах/каналах, где бот имеет нужный доступ (для истории сообщений — является администратором). Поэтому MAX здесь является интерфейсом к TeleScope и получателем deep links. Источник глобального поиска в текущей версии — Telegram. MAX-каталог можно добавить отдельным провайдером, если появится официальный Search API или будет заключено партнёрское соглашение с лицензированным каталогом.

## Быстрый старт

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Откройте `http://localhost:8000`, введите значение `ADMIN_API_KEY`, создайте проект и поиск.

Без `TG_API_ID`, `TG_API_HASH` и `TG_SESSION` интерфейс работает, а поиск завершается пустым диагностическим результатом. Session String создавайте для отдельного служебного Telegram-аккаунта; не используйте личную сессию владельца.

## Telegram Bot webhook

После деплоя установите webhook:

```text
https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<DOMAIN>/webhooks/telegram/<TG_WEBHOOK_SECRET>
```

Команды: `/start`, `/projects`, `/new Название`, `/search ID запрос`.

## MAX Bot webhook

Создайте подписку `POST https://platform-api2.max.ru/subscriptions` с `Authorization: <MAX_BOT_TOKEN>`:

```json
{
  "url": "https://<DOMAIN>/webhooks/max",
  "update_types": ["message_created", "bot_started"],
  "secret": "<MAX_WEBHOOK_SECRET>"
}
```

MAX требует HTTPS на порту 443 и передаёт секрет в `X-Max-Bot-Api-Secret`. Команды совпадают с Telegram. Для production официальный MAX рекомендует webhook, не long polling.

## Railway

Создайте **отдельный** Railway project, подключите этот репозиторий и добавьте PostgreSQL. В web service задайте переменные из `.env.example`; `DATABASE_URL` Railway подставит автоматически. Dockerfile и `/health` уже настроены в `railway.toml`.

Минимальный production-набор:

- `DATABASE_URL`
- `ADMIN_API_KEY`
- `PUBLIC_BASE_URL`
- `TG_API_ID`, `TG_API_HASH`, `TG_SESSION`
- `TG_BOT_TOKEN`, `TG_BOT_USERNAME`, `TG_WEBHOOK_SECRET`
- `REFERRAL_BOT_USERNAME`
- `MAX_BOT_TOKEN`, `MAX_BOT_USERNAME`, `MAX_WEBHOOK_SECRET` — если нужен MAX

Для оплаты добавьте Stripe-переменные. Для российского рынка Stripe может быть недоступен юридически/операционно; платежный слой изолирован и может быть заменён на ЮKassa/CloudPayments без изменения поиска.

## API

- `GET /health`
- `GET/POST /api/projects`
- `POST /api/projects/{id}/searches`
- `GET /api/searches/{id}`
- `GET /api/searches/{id}/results`
- `GET /api/searches/{id}/export.csv`
- `GET /api/searches/{id}/export.json`
- `POST /api/billing/checkout?plan=pro`

Все admin endpoints требуют `X-API-Key`.

## Privacy boundary

Коллектор никогда не вызывает методы участников, не сохраняет sender/user ID из найденных сообщений и отбрасывает приватные сущности. Контактом считается только `@handle`, который опубликован в описании рядом со словами «реклама», «админ», «сотрудничество», `advertising`, `contact` или `partner`.

Срок хранения задаётся `RETENTION_DAYS`; автоматическую очистку рекомендуется запускать отдельной Railway Cron Job после добавления политики удаления для конкретной юрисдикции.

## Тесты

```bash
pytest -q
```

## Дальнейшее усиление

Для высокой нагрузки вынесите `run_search_background` в отдельный worker (Redis + Dramatiq/Celery). Модель данных и статусная машина `queued/running/completed/failed` уже готовы к этому разделению.
