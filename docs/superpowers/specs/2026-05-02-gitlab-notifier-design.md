# GitLab Notifier — Telegram Bot Design

## Purpose
A Telegram bot that delivers real-time notifications from a self-hosted GitLab instance to subscribed users. Admins register repositories; users self-subscribe via DM and receive messages for the events they opt into.

## Scope
- Self-hosted GitLab only (configurable base URL).
- Per-user subscriptions over DM (not group routing).
- Webhook-driven event ingestion (no polling).
- Single VPS deployment via Docker Compose.

## Stack
- Python 3.12
- `python-telegram-bot` (async) — Telegram interactions, long-polling
- `FastAPI` + `uvicorn` — webhook receiver
- `SQLAlchemy` 2.x + `Alembic` — ORM and migrations (migrations generated via `alembic revision --autogenerate`, never hand-written)
- `pydantic-settings` — config from env
- PostgreSQL 16
- `httpx` — GitLab API client
- `pytest` + `pytest-asyncio` — tests

## Architecture
```
GitLab repo ──webhook──▶ FastAPI /gitlab/webhook ─┐
                                                   ├─▶ dispatcher ─▶ formatter ─▶ Telegram API
Telegram user ──DM/cmds──▶ python-telegram-bot ──┘                       │
                                                                          ▼
                                                                    PostgreSQL
```
Single process runs the bot's polling loop and the FastAPI app concurrently via `asyncio.gather`.

## Components

### `config.py`
Pydantic-settings loaded from env:
- `TELEGRAM_BOT_TOKEN`
- `GITLAB_BASE_URL` (e.g. `https://gitlab.example.com`)
- `GITLAB_ADMIN_TOKEN` (PAT with `api` scope, used to manage webhooks)
- `WEBHOOK_PUBLIC_URL` (the HTTPS URL GitLab will POST to)
- `ADMIN_TELEGRAM_IDS` (comma-separated list of authorized admin user IDs)
- `DATABASE_URL`
- `LISTEN_HOST`, `LISTEN_PORT` (default `0.0.0.0:8080`)

### `db/models.py`
- **User**: `id`, `telegram_id` (unique), `username`, `created_at`
- **Repo**: `id`, `gitlab_project_id` (unique), `path_with_namespace` (unique), `webhook_secret`, `created_at`
- **Subscription**: `id`, `user_id` (fk), `repo_id` (fk), `event_mask` (int bitmask), `active` (bool), `created_at`; unique on `(user_id, repo_id)`

Event kinds (bitmask): `PUSH=1, MR_OPEN=2, MR_UPDATE=4, MR_MERGE=8, MR_COMMENT=16, MR_APPROVAL=32, PIPELINE_FAIL=64, ISSUE=128, TAG=256`. Default mask = all bits set.

### `db/session.py`
Async engine + session maker. `get_session()` async-context dependency for FastAPI and bot handlers.

### `gitlab/client.py`
Thin async httpx wrapper. Methods:
- `get_project(path)` — resolve a path to project ID
- `create_webhook(project_id, url, secret, events)` — register hook; events selected: push, tag_push, merge_request, note, pipeline, issue
- `delete_webhook(project_id, hook_id)`
- `list_webhooks(project_id)` — for cleanup/idempotency

### `webhook/server.py`
FastAPI app with single `POST /gitlab/webhook`:
1. Reads `X-Gitlab-Token` header and `X-Gitlab-Event`
2. Looks up repo by payload's `project.id`
3. Verifies `X-Gitlab-Token == repo.webhook_secret` (constant-time compare)
4. Schedules a background task to parse + dispatch
5. Returns `200` immediately

Also `GET /healthz` returning `{"ok": true}`.

### `webhook/parsers.py`
One pure function per `X-Gitlab-Event` value:
- `parse_push(payload) -> Notification`
- `parse_tag_push(payload) -> Notification`
- `parse_merge_request(payload) -> Notification` (kind depends on `object_attributes.action`)
- `parse_note(payload) -> Notification` (only when `noteable_type == MergeRequest`)
- `parse_pipeline(payload) -> Notification | None` (only on failure)
- `parse_issue(payload) -> Notification`
- (approvals delivered via merge_request hook with action `approved`)

`Notification` dataclass: `kind: EventKind`, `repo_path: str`, `actor: str`, `title: str`, `body: str`, `url: str`, `gitlab_project_id: int`.

### `notifier/formatter.py`
`format(notification) -> str` returns Telegram-friendly Markdown (V2 escape) with: bold title, repo path, actor, link, summary lines.

### `notifier/dispatcher.py`
`async dispatch(notification, session, telegram_bot)`:
1. Find subscriptions where `repo_id` matches and `event_mask & notification.kind` and `active=True`
2. Format once; for each subscriber, `bot.send_message(chat_id=user.telegram_id, ...)`
3. On `Forbidden` (user blocked bot) or `BadRequest: chat not found`: set subscription `active=False`
4. On transient errors: retry with exponential backoff up to 3 times (1s, 2s, 4s)

### `bot/handlers.py`
Commands:
- `/start` — register user, show help
- `/help` — list commands
- `/list` — list all registered repos
- `/subscribe <path>` — subscribe to repo by `group/project`
- `/unsubscribe <path>` — remove subscription
- `/mine` — list current user's subscriptions and event filters
- `/filter <path> <event> on|off` — toggle a single event in the mask
- Admin-only:
  - `/addrepo <path>` — resolve via GitLab API, generate secret, create webhook, persist Repo
  - `/removerepo <path>` — delete webhook, remove Repo (cascades subscriptions)
  - `/repos` — admin view with project IDs and webhook IDs

Admin guard: check `update.effective_user.id in settings.admin_telegram_ids`.

### `app.py`
Entry point. Loads config, creates async engine, runs alembic upgrade head, starts bot polling and uvicorn server via `asyncio.gather`. Graceful shutdown on SIGTERM.

## Data Flow — Push Event Example
1. Developer pushes to `team/api`.
2. GitLab POSTs JSON to `https://bot.example.com/gitlab/webhook` with header `X-Gitlab-Token: <secret>` and `X-Gitlab-Event: Push Hook`.
3. Server matches repo by `project.id`, verifies secret.
4. Schedules background task; returns 200 to GitLab.
5. Background: `parse_push` builds `Notification(kind=PUSH, repo_path="team/api", actor="alice", title="3 commits to main", body="...", url="https://gitlab.../commits/main")`.
6. Dispatcher queries subscriptions with `event_mask & PUSH != 0`, sends to each user.

## Error Handling
- Webhook endpoint always returns 200 once token is verified, even if processing fails — prevents GitLab retry storms. Failures logged with payload reference.
- Invalid token → 401.
- Unknown project → 404 (logged, not retried).
- Telegram permanent failures (`Forbidden`, `chat not found`) → mark subscription inactive; user can re-`/start` to reactivate.
- DB unreachable at request time → 503 (GitLab will retry).
- Alembic migration failure on startup → process exits non-zero (compose restarts loop visible).

## Security
- Webhook secret per repo, stored plaintext in DB (acceptable: DB is private; alternative would be env-only which doesn't scale).
- Constant-time secret comparison.
- Admin commands gated by `ADMIN_TELEGRAM_IDS`.
- `GITLAB_ADMIN_TOKEN` only used by admin command paths; never exposed in messages.
- HTTPS termination assumed at reverse proxy (Caddy/Traefik) in front of uvicorn.

## Testing
- **Unit:** each parser fed a fixture payload (sampled from real GitLab docs), asserts `Notification` fields. Formatter snapshot tests per event kind.
- **Unit:** dispatcher with in-memory DB and mocked Telegram bot, asserts correct recipients and inactivation behavior.
- **Integration:** FastAPI `TestClient` posts a fixture to `/gitlab/webhook`; assert background task invokes Telegram mock with expected message.
- **Bot:** `python-telegram-bot` test helpers for `/subscribe`, `/addrepo`, admin guard.
- Fixtures stored under `tests/fixtures/gitlab/` as JSON files.

## Deployment
- `Dockerfile` — `python:3.12-slim`, `uv` for dependency install
- `docker-compose.yml` — services: `app`, `db` (postgres:16); volume for db data; `app` runs `alembic upgrade head && python -m gitlab_notifier.app`
- `.env.example` listing all env vars
- README with:
  - Required GitLab PAT scopes (`api`)
  - Setting `WEBHOOK_PUBLIC_URL` and putting Caddy/Traefik in front for HTTPS
  - Finding your Telegram user ID for `ADMIN_TELEGRAM_IDS`
  - First-run flow: `/start` the bot, admin `/addrepo team/project`, users `/subscribe team/project`

## Out of Scope (v1)
- Group/topic routing
- Per-user GitLab tokens
- Web UI
- Issue comments (only MR comments are notified)
- Polling fallback
- Multi-tenant (multiple GitLab instances)

## Project Layout
```
gitlab-notifier/
  pyproject.toml
  alembic.ini
  alembic/
    env.py
    versions/
  src/gitlab_notifier/
    __init__.py
    app.py
    config.py
    db/
      __init__.py
      models.py
      session.py
    gitlab/
      __init__.py
      client.py
    webhook/
      __init__.py
      server.py
      parsers.py
    notifier/
      __init__.py
      formatter.py
      dispatcher.py
      events.py        # EventKind enum + bitmask helpers
    bot/
      __init__.py
      handlers.py
      admin.py
  tests/
    conftest.py
    fixtures/gitlab/*.json
    test_parsers.py
    test_formatter.py
    test_dispatcher.py
    test_webhook.py
    test_handlers.py
  Dockerfile
  docker-compose.yml
  .env.example
  README.md
```
