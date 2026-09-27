# Architecture

```text
Telegram Bot ─┐
MAX Bot ──────┼──> FastAPI / auth / plan limits ──> PostgreSQL
Web Admin ────┘              │                         │
                             └── search job ───────────┘
                                    │
                              Telethon adapter
                                    │
                         public channels/groups only
```

## Data boundary

`TelegramDiscovery` is the enforcement point. It accepts only `Channel` entities that are broadcast channels or megagroups and have a public username. Message sender objects are never serialized. Participant methods are never called.

## Scoring

- relevance 55%: query terms in title, description and matching snippets;
- activity 25%: recent matched posts plus views relative to audience;
- audience 20%: logarithmic subscriber scale to prevent huge channels dominating every result.

The components are exported separately so customers can understand the ranking.

## Services

MVP runs as one container and uses FastAPI background tasks. Production scale target:

1. web/API service;
2. worker service consuming Redis jobs;
3. managed PostgreSQL;
4. optional scheduler for refresh and retention cleanup.

Search status and database boundaries already support this split.

## Monetization

- Free: 5 searches/month, small exports, branded referrals.
- Pro: 100 searches/month, full export, scheduled refresh.
- Team: 500 searches/month, workspaces, API, shared projects.

The MVP enforces search limits and contains Stripe checkout/webhook wiring. Before launch, add entitlement checks per feature and choose a payment provider appropriate for the seller's jurisdiction.

