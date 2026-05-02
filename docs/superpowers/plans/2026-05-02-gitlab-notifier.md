# GitLab Notifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Telegram bot that delivers webhook-driven notifications from a self-hosted GitLab to per-user subscribers.

**Architecture:** Single Python process runs python-telegram-bot (long-polling) and FastAPI (webhook receiver) concurrently via asyncio. PostgreSQL stores users/repos/subscriptions. Events are parsed into a normalized `Notification`, formatted, and dispatched to subscribers based on a per-subscription event bitmask.

**Tech Stack:** Python 3.12, python-telegram-bot, FastAPI, uvicorn, SQLAlchemy 2.x async, Alembic, PostgreSQL, pydantic-settings, httpx, pytest, pytest-asyncio.

**Migration policy:** All Alembic migrations are produced via `alembic revision --autogenerate`. Never hand-author a migration file.

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.python-version`, `src/gitlab_notifier/__init__.py`, `tests/__init__.py`, `tests/conftest.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "gitlab-notifier"
version = "0.1.0"
description = "Telegram notifications for self-hosted GitLab"
requires-python = ">=3.12"
dependencies = [
  "python-telegram-bot[ext]>=21.0",
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "sqlalchemy[asyncio]>=2.0",
  "alembic>=1.13",
  "asyncpg>=0.29",
  "pydantic>=2.7",
  "pydantic-settings>=2.4",
  "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
  "pytest>=8",
  "pytest-asyncio>=0.23",
  "aiosqlite>=0.20",
  "httpx>=0.27",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/gitlab_notifier"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.py[cod]
.venv/
.env
.pytest_cache/
.mypy_cache/
*.egg-info/
dist/
build/
```

- [ ] **Step 3: Write `.python-version`**

```
3.12
```

- [ ] **Step 4: Create empty package files**

```bash
mkdir -p src/gitlab_notifier tests
touch src/gitlab_notifier/__init__.py tests/__init__.py
```

- [ ] **Step 5: Write minimal `tests/conftest.py`**

```python
import pytest
```

- [ ] **Step 6: Install deps and run pytest**

Run: `rtk python -m venv .venv && rtk .venv/bin/pip install -e '.[dev]' && rtk .venv/bin/pytest -q`
Expected: "no tests ran" exit 0 (or 5).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: scaffold project"
```

---

## Task 2: Configuration

**Files:**
- Create: `src/gitlab_notifier/config.py`, `tests/test_config.py`, `.env.example`

- [ ] **Step 1: Write `tests/test_config.py`**

```python
import os
from gitlab_notifier.config import Settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-token")
    monkeypatch.setenv("GITLAB_BASE_URL", "https://gitlab.example.com")
    monkeypatch.setenv("GITLAB_ADMIN_TOKEN", "gl-token")
    monkeypatch.setenv("WEBHOOK_PUBLIC_URL", "https://bot.example.com")
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "1,2,3")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    s = Settings()
    assert s.telegram_bot_token == "tg-token"
    assert s.gitlab_base_url == "https://gitlab.example.com"
    assert s.admin_telegram_ids == [1, 2, 3]
    assert s.listen_port == 8080  # default
```

- [ ] **Step 2: Run test**

Run: `rtk .venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: gitlab_notifier.config`.

- [ ] **Step 3: Write `src/gitlab_notifier/config.py`**

```python
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str
    gitlab_base_url: str
    gitlab_admin_token: str
    webhook_public_url: str
    admin_telegram_ids: list[int] = Field(default_factory=list)
    database_url: str
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080

    @field_validator("admin_telegram_ids", mode="before")
    @classmethod
    def _split_admins(cls, v):
        if isinstance(v, str):
            return [int(x) for x in v.split(",") if x.strip()]
        return v
```

- [ ] **Step 4: Run test**

Run: `rtk .venv/bin/pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Write `.env.example`**

```
TELEGRAM_BOT_TOKEN=
GITLAB_BASE_URL=https://gitlab.example.com
GITLAB_ADMIN_TOKEN=
WEBHOOK_PUBLIC_URL=https://bot.example.com
ADMIN_TELEGRAM_IDS=
DATABASE_URL=postgresql+asyncpg://gitlab_notifier:password@db:5432/gitlab_notifier
LISTEN_HOST=0.0.0.0
LISTEN_PORT=8080
```

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(config): add settings module"
```

---

## Task 3: Event kinds & bitmask

**Files:**
- Create: `src/gitlab_notifier/notifier/__init__.py`, `src/gitlab_notifier/notifier/events.py`, `tests/test_events.py`

- [ ] **Step 1: Write `tests/test_events.py`**

```python
from gitlab_notifier.notifier.events import EventKind, ALL_EVENTS, mask_has, mask_set


def test_eventkind_bitmask_unique():
    values = [e.value for e in EventKind]
    assert len(values) == len(set(values))


def test_all_events_covers_every_kind():
    expected = 0
    for e in EventKind:
        expected |= e.value
    assert ALL_EVENTS == expected


def test_mask_has_and_set():
    m = 0
    m = mask_set(m, EventKind.PUSH, True)
    assert mask_has(m, EventKind.PUSH)
    assert not mask_has(m, EventKind.MR_OPEN)
    m = mask_set(m, EventKind.PUSH, False)
    assert not mask_has(m, EventKind.PUSH)
```

- [ ] **Step 2: Run — expect import failure**

Run: `rtk .venv/bin/pytest tests/test_events.py -v`

- [ ] **Step 3: Write `src/gitlab_notifier/notifier/__init__.py`**

Empty file.

- [ ] **Step 4: Write `src/gitlab_notifier/notifier/events.py`**

```python
from enum import IntFlag, auto


class EventKind(IntFlag):
    PUSH = auto()
    MR_OPEN = auto()
    MR_UPDATE = auto()
    MR_MERGE = auto()
    MR_COMMENT = auto()
    MR_APPROVAL = auto()
    PIPELINE_FAIL = auto()
    ISSUE = auto()
    TAG = auto()


ALL_EVENTS: int = 0
for _e in EventKind:
    ALL_EVENTS |= _e.value


def mask_has(mask: int, kind: EventKind) -> bool:
    return bool(mask & kind.value)


def mask_set(mask: int, kind: EventKind, on: bool) -> int:
    return (mask | kind.value) if on else (mask & ~kind.value)


EVENT_NAMES: dict[str, EventKind] = {
    "push": EventKind.PUSH,
    "mr_open": EventKind.MR_OPEN,
    "mr_update": EventKind.MR_UPDATE,
    "mr_merge": EventKind.MR_MERGE,
    "mr_comment": EventKind.MR_COMMENT,
    "mr_approval": EventKind.MR_APPROVAL,
    "pipeline_fail": EventKind.PIPELINE_FAIL,
    "issue": EventKind.ISSUE,
    "tag": EventKind.TAG,
}
```

- [ ] **Step 5: Run tests — PASS**

Run: `rtk .venv/bin/pytest tests/test_events.py -v`

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(events): add EventKind bitmask and helpers"
```

---

## Task 4: Notification dataclass

**Files:**
- Create: `src/gitlab_notifier/notifier/notification.py`, `tests/test_notification.py`

- [ ] **Step 1: Write `tests/test_notification.py`**

```python
from gitlab_notifier.notifier.notification import Notification
from gitlab_notifier.notifier.events import EventKind


def test_notification_construct():
    n = Notification(
        kind=EventKind.PUSH,
        repo_path="team/api",
        gitlab_project_id=1,
        actor="alice",
        title="3 commits to main",
        body="abc...",
        url="https://gitlab.example.com/team/api/-/commits/main",
    )
    assert n.kind == EventKind.PUSH
    assert n.repo_path == "team/api"
```

- [ ] **Step 2: Write `src/gitlab_notifier/notifier/notification.py`**

```python
from dataclasses import dataclass
from .events import EventKind


@dataclass(frozen=True, slots=True)
class Notification:
    kind: EventKind
    repo_path: str
    gitlab_project_id: int
    actor: str
    title: str
    body: str
    url: str
```

- [ ] **Step 3: Run + commit**

Run: `rtk .venv/bin/pytest tests/test_notification.py -v`

```bash
git add -A && git commit -m "feat(notifier): add Notification dataclass"
```

---

## Task 5: Database models

**Files:**
- Create: `src/gitlab_notifier/db/__init__.py`, `src/gitlab_notifier/db/models.py`, `src/gitlab_notifier/db/session.py`, `tests/test_models.py`

- [ ] **Step 1: Write `tests/test_models.py`**

```python
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select

from gitlab_notifier.db.models import Base, User, Repo, Subscription
from gitlab_notifier.notifier.events import ALL_EVENTS, EventKind, mask_has


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


async def test_can_create_user_repo_subscription(session):
    u = User(telegram_id=42, username="alice")
    r = Repo(gitlab_project_id=1, path_with_namespace="team/api", webhook_secret="s")
    session.add_all([u, r])
    await session.flush()
    sub = Subscription(user_id=u.id, repo_id=r.id, event_mask=ALL_EVENTS)
    session.add(sub)
    await session.commit()

    found = (await session.execute(select(Subscription))).scalar_one()
    assert mask_has(found.event_mask, EventKind.PUSH)
    assert found.active is True
```

- [ ] **Step 2: Write `src/gitlab_notifier/db/__init__.py`** (empty)

- [ ] **Step 3: Write `src/gitlab_notifier/db/models.py`**

```python
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Repo(Base):
    __tablename__ = "repos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gitlab_project_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    path_with_namespace: Mapped[str] = mapped_column(String(512), unique=True, nullable=False, index=True)
    webhook_secret: Mapped[str] = mapped_column(String(128), nullable=False)
    webhook_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="repo", cascade="all, delete-orphan")


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "repo_id", name="uq_subscription_user_repo"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"), nullable=False)
    event_mask: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="subscriptions")
    repo: Mapped[Repo] = relationship(back_populates="subscriptions")
```

- [ ] **Step 4: Write `src/gitlab_notifier/db/session.py`**

```python
from collections.abc import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def make_engine(url: str):
    return create_async_engine(url, future=True)


def make_session_maker(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def session_scope(maker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with maker() as s:
        yield s
```

- [ ] **Step 5: Run + commit**

Run: `rtk .venv/bin/pytest tests/test_models.py -v`

```bash
git add -A && git commit -m "feat(db): add ORM models and session helpers"
```

---

## Task 6: Alembic setup + initial migration

**Files:**
- Create: `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/.gitkeep`

- [ ] **Step 1: Init alembic via the tool**

Run: `rtk .venv/bin/alembic init -t async alembic`
Expected: creates `alembic.ini`, `alembic/env.py`, etc.

- [ ] **Step 2: Set `sqlalchemy.url` to env-driven in `alembic.ini`**

Edit `alembic.ini` `sqlalchemy.url = ` (leave empty — env.py will set it).

- [ ] **Step 3: Replace `alembic/env.py` body**

```python
import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from gitlab_notifier.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])

target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


run_migrations_online()
```

- [ ] **Step 4: Generate initial migration via alembic**

Set DATABASE_URL to a temp sqlite-async DB and autogenerate:

Run:
```bash
rtk env DATABASE_URL=sqlite+aiosqlite:///./_alembic_tmp.db .venv/bin/alembic revision --autogenerate -m "initial schema"
rm -f _alembic_tmp.db
```

Expected: a new file under `alembic/versions/` describing `users`, `repos`, `subscriptions` tables.

- [ ] **Step 5: Verify migration applies**

```bash
rtk env DATABASE_URL=sqlite+aiosqlite:///./_alembic_tmp.db .venv/bin/alembic upgrade head
rm -f _alembic_tmp.db
```

Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(db): alembic setup + initial migration"
```

---

## Task 7: Webhook parsers — push & tag_push

**Files:**
- Create: `src/gitlab_notifier/webhook/__init__.py`, `src/gitlab_notifier/webhook/parsers.py`, `tests/fixtures/gitlab/push.json`, `tests/fixtures/gitlab/tag_push.json`, `tests/test_parsers_push.py`

GitLab push hook payload reference: https://docs.gitlab.com/user/project/integrations/webhook_events/#push-events

- [ ] **Step 1: Save `tests/fixtures/gitlab/push.json`** — minimal realistic payload:

```json
{
  "object_kind": "push",
  "ref": "refs/heads/main",
  "user_username": "alice",
  "user_name": "Alice",
  "project": {"id": 1, "path_with_namespace": "team/api", "web_url": "https://gitlab.example.com/team/api"},
  "commits": [
    {"id": "abc123", "message": "fix bug", "url": "https://gitlab.example.com/team/api/-/commit/abc123"},
    {"id": "def456", "message": "add feature", "url": "https://gitlab.example.com/team/api/-/commit/def456"}
  ],
  "total_commits_count": 2
}
```

- [ ] **Step 2: Save `tests/fixtures/gitlab/tag_push.json`**:

```json
{
  "object_kind": "tag_push",
  "ref": "refs/tags/v1.2.3",
  "user_username": "bob",
  "project": {"id": 1, "path_with_namespace": "team/api", "web_url": "https://gitlab.example.com/team/api"}
}
```

- [ ] **Step 3: Write `tests/test_parsers_push.py`**

```python
import json
from pathlib import Path
from gitlab_notifier.webhook.parsers import parse_push, parse_tag_push
from gitlab_notifier.notifier.events import EventKind

FIX = Path(__file__).parent / "fixtures" / "gitlab"


def test_parse_push():
    payload = json.loads((FIX / "push.json").read_text())
    n = parse_push(payload)
    assert n.kind == EventKind.PUSH
    assert n.repo_path == "team/api"
    assert n.gitlab_project_id == 1
    assert n.actor == "alice"
    assert "main" in n.title
    assert "2" in n.title
    assert "fix bug" in n.body
    assert n.url.endswith("/commits/main")


def test_parse_tag_push():
    payload = json.loads((FIX / "tag_push.json").read_text())
    n = parse_tag_push(payload)
    assert n.kind == EventKind.TAG
    assert "v1.2.3" in n.title
    assert n.actor == "bob"
```

- [ ] **Step 4: Write `src/gitlab_notifier/webhook/__init__.py`** (empty)

- [ ] **Step 5: Write initial `src/gitlab_notifier/webhook/parsers.py`** (just push + tag for now):

```python
from gitlab_notifier.notifier.events import EventKind
from gitlab_notifier.notifier.notification import Notification


def _project(payload: dict) -> tuple[int, str, str]:
    p = payload["project"]
    return p["id"], p["path_with_namespace"], p["web_url"]


def parse_push(payload: dict) -> Notification:
    pid, path, web_url = _project(payload)
    branch = payload["ref"].removeprefix("refs/heads/")
    count = payload.get("total_commits_count", len(payload.get("commits", [])))
    actor = payload.get("user_username") or payload.get("user_name") or "unknown"
    body_lines = []
    for c in payload.get("commits", [])[:5]:
        first = c["message"].splitlines()[0][:100]
        body_lines.append(f"- {c['id'][:8]} {first}")
    return Notification(
        kind=EventKind.PUSH,
        repo_path=path,
        gitlab_project_id=pid,
        actor=actor,
        title=f"{count} commit(s) to {branch}",
        body="\n".join(body_lines),
        url=f"{web_url}/-/commits/{branch}",
    )


def parse_tag_push(payload: dict) -> Notification:
    pid, path, web_url = _project(payload)
    tag = payload["ref"].removeprefix("refs/tags/")
    actor = payload.get("user_username") or payload.get("user_name") or "unknown"
    return Notification(
        kind=EventKind.TAG,
        repo_path=path,
        gitlab_project_id=pid,
        actor=actor,
        title=f"tag {tag} pushed",
        body="",
        url=f"{web_url}/-/tags/{tag}",
    )
```

- [ ] **Step 6: Run + commit**

Run: `rtk .venv/bin/pytest tests/test_parsers_push.py -v`

```bash
git add -A && git commit -m "feat(parsers): push and tag_push events"
```

---

## Task 8: Webhook parsers — merge_request

**Files:**
- Modify: `src/gitlab_notifier/webhook/parsers.py`
- Create: `tests/fixtures/gitlab/mr_open.json`, `mr_update.json`, `mr_merge.json`, `mr_approved.json`, `tests/test_parsers_mr.py`

- [ ] **Step 1: Save fixtures (minimal)**

`tests/fixtures/gitlab/mr_open.json`:
```json
{
  "object_kind": "merge_request",
  "user": {"username": "alice"},
  "project": {"id": 1, "path_with_namespace": "team/api", "web_url": "https://gitlab.example.com/team/api"},
  "object_attributes": {
    "iid": 7, "title": "Add login flow", "action": "open", "state": "opened",
    "source_branch": "feature/login", "target_branch": "main",
    "url": "https://gitlab.example.com/team/api/-/merge_requests/7"
  }
}
```

`tests/fixtures/gitlab/mr_update.json` — same shape with `"action": "update"`.
`tests/fixtures/gitlab/mr_merge.json` — `"action": "merge"`, `"state": "merged"`.
`tests/fixtures/gitlab/mr_approved.json` — `"action": "approved"`, `"state": "opened"`.

- [ ] **Step 2: Write `tests/test_parsers_mr.py`**

```python
import json
from pathlib import Path
import pytest
from gitlab_notifier.webhook.parsers import parse_merge_request
from gitlab_notifier.notifier.events import EventKind

FIX = Path(__file__).parent / "fixtures" / "gitlab"


@pytest.mark.parametrize("name,kind", [
    ("mr_open", EventKind.MR_OPEN),
    ("mr_update", EventKind.MR_UPDATE),
    ("mr_merge", EventKind.MR_MERGE),
    ("mr_approved", EventKind.MR_APPROVAL),
])
def test_parse_merge_request(name, kind):
    payload = json.loads((FIX / f"{name}.json").read_text())
    n = parse_merge_request(payload)
    assert n is not None
    assert n.kind == kind
    assert n.repo_path == "team/api"
    assert "!7" in n.title or "Add login flow" in n.title
    assert n.url.endswith("/merge_requests/7")


def test_parse_mr_close_returns_none():
    payload = json.loads((FIX / "mr_open.json").read_text())
    payload["object_attributes"]["action"] = "close"
    assert parse_merge_request(payload) is None
```

- [ ] **Step 3: Add `parse_merge_request` to `src/gitlab_notifier/webhook/parsers.py`**

```python
_MR_ACTION_TO_KIND = {
    "open": EventKind.MR_OPEN,
    "reopen": EventKind.MR_OPEN,
    "update": EventKind.MR_UPDATE,
    "merge": EventKind.MR_MERGE,
    "approved": EventKind.MR_APPROVAL,
}


def parse_merge_request(payload: dict) -> Notification | None:
    pid, path, _ = _project(payload)
    attrs = payload["object_attributes"]
    action = attrs.get("action")
    kind = _MR_ACTION_TO_KIND.get(action)
    if kind is None:
        return None
    actor = (payload.get("user") or {}).get("username") or "unknown"
    iid = attrs["iid"]
    title = attrs["title"]
    src = attrs.get("source_branch", "")
    tgt = attrs.get("target_branch", "")
    verb = {
        EventKind.MR_OPEN: "opened",
        EventKind.MR_UPDATE: "updated",
        EventKind.MR_MERGE: "merged",
        EventKind.MR_APPROVAL: "approved",
    }[kind]
    return Notification(
        kind=kind,
        repo_path=path,
        gitlab_project_id=pid,
        actor=actor,
        title=f"MR !{iid} {verb}: {title}",
        body=f"{src} → {tgt}" if src and tgt else "",
        url=attrs["url"],
    )
```

- [ ] **Step 4: Run + commit**

Run: `rtk .venv/bin/pytest tests/test_parsers_mr.py -v`

```bash
git add -A && git commit -m "feat(parsers): merge_request events"
```

---

## Task 9: Webhook parsers — note (MR comments), pipeline, issue

**Files:**
- Modify: `src/gitlab_notifier/webhook/parsers.py`
- Create: `tests/fixtures/gitlab/note_mr.json`, `note_commit.json`, `pipeline_failed.json`, `pipeline_success.json`, `issue_open.json`, `tests/test_parsers_misc.py`

- [ ] **Step 1: Save fixtures**

`note_mr.json`:
```json
{
  "object_kind": "note",
  "user": {"username": "carol"},
  "project": {"id": 1, "path_with_namespace": "team/api", "web_url": "https://gitlab.example.com/team/api"},
  "object_attributes": {"noteable_type": "MergeRequest", "note": "lgtm", "url": "https://gitlab.example.com/team/api/-/merge_requests/7#note_99"},
  "merge_request": {"iid": 7, "title": "Add login flow"}
}
```

`note_commit.json` — same with `"noteable_type": "Commit"` and no `merge_request`.

`pipeline_failed.json`:
```json
{
  "object_kind": "pipeline",
  "user": {"username": "alice"},
  "project": {"id": 1, "path_with_namespace": "team/api", "web_url": "https://gitlab.example.com/team/api"},
  "object_attributes": {"id": 4242, "ref": "main", "status": "failed"}
}
```

`pipeline_success.json` — same with `"status": "success"`.

`issue_open.json`:
```json
{
  "object_kind": "issue",
  "user": {"username": "dave"},
  "project": {"id": 1, "path_with_namespace": "team/api", "web_url": "https://gitlab.example.com/team/api"},
  "object_attributes": {"iid": 11, "title": "Bug in login", "action": "open", "state": "opened", "url": "https://gitlab.example.com/team/api/-/issues/11"}
}
```

- [ ] **Step 2: Write `tests/test_parsers_misc.py`**

```python
import json
from pathlib import Path
from gitlab_notifier.webhook.parsers import parse_note, parse_pipeline, parse_issue
from gitlab_notifier.notifier.events import EventKind

FIX = Path(__file__).parent / "fixtures" / "gitlab"


def _load(name): return json.loads((FIX / name).read_text())


def test_parse_note_mr():
    n = parse_note(_load("note_mr.json"))
    assert n is not None and n.kind == EventKind.MR_COMMENT
    assert "!7" in n.title
    assert n.actor == "carol"


def test_parse_note_commit_returns_none():
    assert parse_note(_load("note_commit.json")) is None


def test_parse_pipeline_failed():
    n = parse_pipeline(_load("pipeline_failed.json"))
    assert n is not None and n.kind == EventKind.PIPELINE_FAIL
    assert "failed" in n.title.lower()


def test_parse_pipeline_success_returns_none():
    assert parse_pipeline(_load("pipeline_success.json")) is None


def test_parse_issue_open():
    n = parse_issue(_load("issue_open.json"))
    assert n is not None and n.kind == EventKind.ISSUE
    assert "#11" in n.title or "Bug in login" in n.title
```

- [ ] **Step 3: Add parsers to `src/gitlab_notifier/webhook/parsers.py`**

```python
def parse_note(payload: dict) -> Notification | None:
    attrs = payload["object_attributes"]
    if attrs.get("noteable_type") != "MergeRequest":
        return None
    pid, path, _ = _project(payload)
    actor = (payload.get("user") or {}).get("username") or "unknown"
    mr = payload.get("merge_request") or {}
    iid = mr.get("iid", "?")
    mr_title = mr.get("title", "")
    snippet = (attrs.get("note") or "").splitlines()[0][:200]
    return Notification(
        kind=EventKind.MR_COMMENT,
        repo_path=path,
        gitlab_project_id=pid,
        actor=actor,
        title=f"comment on MR !{iid}: {mr_title}",
        body=snippet,
        url=attrs["url"],
    )


def parse_pipeline(payload: dict) -> Notification | None:
    attrs = payload["object_attributes"]
    if attrs.get("status") != "failed":
        return None
    pid, path, web_url = _project(payload)
    actor = (payload.get("user") or {}).get("username") or "unknown"
    pid_pl = attrs.get("id")
    ref = attrs.get("ref", "")
    return Notification(
        kind=EventKind.PIPELINE_FAIL,
        repo_path=path,
        gitlab_project_id=pid,
        actor=actor,
        title=f"pipeline #{pid_pl} failed on {ref}",
        body="",
        url=f"{web_url}/-/pipelines/{pid_pl}",
    )


def parse_issue(payload: dict) -> Notification | None:
    attrs = payload["object_attributes"]
    action = attrs.get("action")
    if action not in {"open", "reopen", "close"}:
        return None
    pid, path, _ = _project(payload)
    actor = (payload.get("user") or {}).get("username") or "unknown"
    iid = attrs["iid"]
    verb = {"open": "opened", "reopen": "reopened", "close": "closed"}[action]
    return Notification(
        kind=EventKind.ISSUE,
        repo_path=path,
        gitlab_project_id=pid,
        actor=actor,
        title=f"issue #{iid} {verb}: {attrs['title']}",
        body="",
        url=attrs["url"],
    )
```

- [ ] **Step 4: Run + commit**

Run: `rtk .venv/bin/pytest tests/test_parsers_misc.py -v`

```bash
git add -A && git commit -m "feat(parsers): note, pipeline, issue events"
```

---

## Task 10: Parser dispatch by event header

**Files:**
- Modify: `src/gitlab_notifier/webhook/parsers.py`
- Create: `tests/test_parser_dispatch.py`

- [ ] **Step 1: Write test**

```python
import json
from pathlib import Path
from gitlab_notifier.webhook.parsers import parse_event

FIX = Path(__file__).parent / "fixtures" / "gitlab"


def test_dispatch_unknown_returns_none():
    assert parse_event("Unknown Hook", {}) is None


def test_dispatch_push():
    p = json.loads((FIX / "push.json").read_text())
    assert parse_event("Push Hook", p) is not None


def test_dispatch_mr():
    p = json.loads((FIX / "mr_open.json").read_text())
    assert parse_event("Merge Request Hook", p) is not None
```

- [ ] **Step 2: Add `parse_event` to `src/gitlab_notifier/webhook/parsers.py`**

```python
_EVENT_DISPATCH = {
    "Push Hook": parse_push,
    "Tag Push Hook": parse_tag_push,
    "Merge Request Hook": parse_merge_request,
    "Note Hook": parse_note,
    "Pipeline Hook": parse_pipeline,
    "Issue Hook": parse_issue,
}


def parse_event(event_header: str, payload: dict) -> Notification | None:
    fn = _EVENT_DISPATCH.get(event_header)
    if fn is None:
        return None
    return fn(payload)
```

- [ ] **Step 3: Run + commit**

Run: `rtk .venv/bin/pytest tests/test_parser_dispatch.py -v`

```bash
git add -A && git commit -m "feat(parsers): event header dispatch"
```

---

## Task 11: Formatter

**Files:**
- Create: `src/gitlab_notifier/notifier/formatter.py`, `tests/test_formatter.py`

- [ ] **Step 1: Write `tests/test_formatter.py`**

```python
from gitlab_notifier.notifier.formatter import format_notification, escape_md
from gitlab_notifier.notifier.notification import Notification
from gitlab_notifier.notifier.events import EventKind


def test_escape_md():
    assert escape_md("a_b*c") == r"a\_b\*c"


def test_format_includes_repo_actor_link_title():
    n = Notification(
        kind=EventKind.PUSH, repo_path="team/api", gitlab_project_id=1,
        actor="alice", title="2 commits to main", body="- abc commit", url="https://x/y",
    )
    out = format_notification(n)
    assert "team/api" in out
    assert "alice" in out
    assert "2 commits to main" in out
    assert "https://x/y" in out
```

- [ ] **Step 2: Write `src/gitlab_notifier/notifier/formatter.py`**

```python
from .notification import Notification

_MD_SPECIALS = r"_*[]()~`>#+-=|{}.!\\"


def escape_md(text: str) -> str:
    out = []
    for ch in text:
        if ch in _MD_SPECIALS:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def format_notification(n: Notification) -> str:
    title = escape_md(n.title)
    repo = escape_md(n.repo_path)
    actor = escape_md(n.actor)
    body = escape_md(n.body) if n.body else ""
    url = n.url  # links not escaped for body, used inside ()
    parts = [
        f"*{title}*",
        f"📁 `{repo}`  👤 {actor}",
    ]
    if body:
        parts.append(body)
    parts.append(f"[open]({url})")
    return "\n".join(parts)
```

- [ ] **Step 3: Run + commit**

Run: `rtk .venv/bin/pytest tests/test_formatter.py -v`

```bash
git add -A && git commit -m "feat(formatter): markdown notification formatter"
```

---

## Task 12: Dispatcher

**Files:**
- Create: `src/gitlab_notifier/notifier/dispatcher.py`, `tests/test_dispatcher.py`

- [ ] **Step 1: Write `tests/test_dispatcher.py`**

```python
import pytest
from unittest.mock import AsyncMock
from telegram.error import Forbidden
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select

from gitlab_notifier.db.models import Base, User, Repo, Subscription
from gitlab_notifier.notifier.events import EventKind, ALL_EVENTS
from gitlab_notifier.notifier.notification import Notification
from gitlab_notifier.notifier.dispatcher import dispatch


@pytest.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(maker):
    async with maker() as s:
        u1 = User(telegram_id=100, username="a")
        u2 = User(telegram_id=200, username="b")
        u3 = User(telegram_id=300, username="c")  # no PUSH
        r = Repo(gitlab_project_id=1, path_with_namespace="team/api", webhook_secret="x")
        s.add_all([u1, u2, u3, r]); await s.flush()
        s.add_all([
            Subscription(user_id=u1.id, repo_id=r.id, event_mask=ALL_EVENTS),
            Subscription(user_id=u2.id, repo_id=r.id, event_mask=ALL_EVENTS),
            Subscription(user_id=u3.id, repo_id=r.id, event_mask=ALL_EVENTS & ~EventKind.PUSH.value),
        ])
        await s.commit()


async def test_dispatch_sends_to_matching_subscribers(session_maker):
    await _seed(session_maker)
    bot = AsyncMock()
    n = Notification(kind=EventKind.PUSH, repo_path="team/api", gitlab_project_id=1,
                     actor="alice", title="t", body="", url="u")
    async with session_maker() as s:
        await dispatch(n, s, bot)
    sent_ids = sorted([call.kwargs["chat_id"] for call in bot.send_message.call_args_list])
    assert sent_ids == [100, 200]


async def test_dispatch_marks_inactive_on_forbidden(session_maker):
    await _seed(session_maker)
    bot = AsyncMock()
    bot.send_message.side_effect = Forbidden("blocked")
    n = Notification(kind=EventKind.PUSH, repo_path="team/api", gitlab_project_id=1,
                     actor="alice", title="t", body="", url="u")
    async with session_maker() as s:
        await dispatch(n, s, bot)
    async with session_maker() as s:
        rows = (await s.execute(select(Subscription).order_by(Subscription.id))).scalars().all()
        actives = [r.active for r in rows]
        assert actives[0] is False and actives[1] is False
        assert actives[2] is True  # was not targeted
```

- [ ] **Step 2: Write `src/gitlab_notifier/notifier/dispatcher.py`**

```python
import asyncio
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut, NetworkError

from gitlab_notifier.db.models import Repo, Subscription, User
from .events import EventKind
from .formatter import format_notification
from .notification import Notification

log = logging.getLogger(__name__)


async def dispatch(n: Notification, session: AsyncSession, bot: Bot) -> None:
    repo = (await session.execute(
        select(Repo).where(Repo.gitlab_project_id == n.gitlab_project_id)
    )).scalar_one_or_none()
    if repo is None:
        log.warning("dispatch: unknown project_id=%s", n.gitlab_project_id)
        return

    bit = EventKind(n.kind).value
    rows = (await session.execute(
        select(Subscription, User)
        .join(User, Subscription.user_id == User.id)
        .where(
            Subscription.repo_id == repo.id,
            Subscription.active.is_(True),
            Subscription.event_mask.op("&")(bit) != 0,
        )
    )).all()

    text = format_notification(n)
    for sub, user in rows:
        await _send_with_retry(bot, user.telegram_id, text, sub, session)
    await session.commit()


async def _send_with_retry(bot: Bot, chat_id: int, text: str, sub: Subscription, session: AsyncSession) -> None:
    delay = 1.0
    for attempt in range(3):
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN_V2,
                                   disable_web_page_preview=True)
            return
        except (Forbidden, BadRequest) as e:
            log.info("disabling subscription %s: %s", sub.id, e)
            sub.active = False
            return
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except (TimedOut, NetworkError):
            await asyncio.sleep(delay)
            delay *= 2
    log.warning("send failed after retries chat_id=%s", chat_id)
```

- [ ] **Step 3: Run + commit**

Run: `rtk .venv/bin/pytest tests/test_dispatcher.py -v`

```bash
git add -A && git commit -m "feat(dispatcher): subscriber fan-out with retry/inactivation"
```

---

## Task 13: GitLab API client

**Files:**
- Create: `src/gitlab_notifier/gitlab/__init__.py`, `src/gitlab_notifier/gitlab/client.py`, `tests/test_gitlab_client.py`

- [ ] **Step 1: Write `tests/test_gitlab_client.py`**

```python
import httpx
import pytest
from gitlab_notifier.gitlab.client import GitLabClient


@pytest.fixture
def transport_factory():
    def make(handler):
        return httpx.MockTransport(handler)
    return make


async def test_get_project(transport_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v4/projects/team%2Fapi"
        assert request.headers["PRIVATE-TOKEN"] == "tok"
        return httpx.Response(200, json={"id": 7, "path_with_namespace": "team/api"})

    async with httpx.AsyncClient(transport=transport_factory(handler), base_url="https://gitlab.example.com") as http:
        c = GitLabClient(http, "tok")
        proj = await c.get_project("team/api")
        assert proj["id"] == 7


async def test_create_webhook(transport_factory):
    seen = {}
    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read()
        return httpx.Response(201, json={"id": 99})

    async with httpx.AsyncClient(transport=transport_factory(handler), base_url="https://gitlab.example.com") as http:
        c = GitLabClient(http, "tok")
        hook = await c.create_webhook(7, url="https://bot/example", secret="s")
        assert hook["id"] == 99
        assert seen["path"] == "/api/v4/projects/7/hooks"
```

- [ ] **Step 2: Write `src/gitlab_notifier/gitlab/__init__.py`** (empty)

- [ ] **Step 3: Write `src/gitlab_notifier/gitlab/client.py`**

```python
from urllib.parse import quote
import httpx


class GitLabClient:
    def __init__(self, http: httpx.AsyncClient, token: str):
        self._http = http
        self._headers = {"PRIVATE-TOKEN": token}

    async def get_project(self, path_with_namespace: str) -> dict:
        encoded = quote(path_with_namespace, safe="")
        r = await self._http.get(f"/api/v4/projects/{encoded}", headers=self._headers)
        r.raise_for_status()
        return r.json()

    async def create_webhook(self, project_id: int, *, url: str, secret: str) -> dict:
        body = {
            "url": url,
            "token": secret,
            "push_events": True,
            "tag_push_events": True,
            "merge_requests_events": True,
            "note_events": True,
            "pipeline_events": True,
            "issues_events": True,
            "enable_ssl_verification": True,
        }
        r = await self._http.post(f"/api/v4/projects/{project_id}/hooks", headers=self._headers, json=body)
        r.raise_for_status()
        return r.json()

    async def delete_webhook(self, project_id: int, hook_id: int) -> None:
        r = await self._http.delete(f"/api/v4/projects/{project_id}/hooks/{hook_id}", headers=self._headers)
        if r.status_code not in (204, 404):
            r.raise_for_status()
```

- [ ] **Step 4: Run + commit**

Run: `rtk .venv/bin/pytest tests/test_gitlab_client.py -v`

```bash
git add -A && git commit -m "feat(gitlab): API client for project lookup and webhook management"
```

---

## Task 14: FastAPI webhook server

**Files:**
- Create: `src/gitlab_notifier/webhook/server.py`, `tests/test_webhook_server.py`

- [ ] **Step 1: Write `tests/test_webhook_server.py`**

```python
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from gitlab_notifier.db.models import Base, Repo
from gitlab_notifier.webhook.server import build_app

FIX = Path(__file__).parent / "fixtures" / "gitlab"


@pytest.fixture
async def app_ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Maker = async_sessionmaker(engine, expire_on_commit=False)
    async with Maker() as s:
        s.add(Repo(gitlab_project_id=1, path_with_namespace="team/api", webhook_secret="topsecret"))
        await s.commit()
    bot = AsyncMock()
    dispatch_calls = []

    async def fake_dispatch(notification, session, bot_):
        dispatch_calls.append(notification)

    app = build_app(session_maker=Maker, bot=bot, dispatcher=fake_dispatch)
    yield app, dispatch_calls
    await engine.dispose()


async def test_healthz(app_ctx):
    app, _ = app_ctx
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200


async def test_webhook_rejects_bad_token(app_ctx):
    app, _ = app_ctx
    payload = json.loads((FIX / "push.json").read_text())
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        r = await c.post("/gitlab/webhook", json=payload,
                         headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "wrong"})
    assert r.status_code == 401


async def test_webhook_unknown_project_returns_404(app_ctx):
    app, _ = app_ctx
    payload = json.loads((FIX / "push.json").read_text())
    payload["project"]["id"] = 999
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        r = await c.post("/gitlab/webhook", json=payload,
                         headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "x"})
    assert r.status_code == 404


async def test_webhook_dispatches_on_valid(app_ctx):
    app, calls = app_ctx
    payload = json.loads((FIX / "push.json").read_text())
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        r = await c.post("/gitlab/webhook", json=payload,
                         headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "topsecret"})
    assert r.status_code == 200
    # background task ran in the same event loop before response on AsyncClient? Not guaranteed.
    # Allow a tick:
    import asyncio; await asyncio.sleep(0.05)
    assert len(calls) == 1
    assert calls[0].repo_path == "team/api"
```

- [ ] **Step 2: Write `src/gitlab_notifier/webhook/server.py`**

```python
import hmac
import logging
from typing import Awaitable, Callable

from fastapi import FastAPI, Header, HTTPException, Request, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from telegram import Bot

from gitlab_notifier.db.models import Repo
from gitlab_notifier.notifier.notification import Notification
from .parsers import parse_event

log = logging.getLogger(__name__)

DispatcherFn = Callable[[Notification, "AsyncSession", Bot], Awaitable[None]]


def build_app(*, session_maker: async_sessionmaker, bot: Bot, dispatcher: DispatcherFn) -> FastAPI:
    app = FastAPI()

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.post("/gitlab/webhook")
    async def webhook(
        request: Request,
        background: BackgroundTasks,
        x_gitlab_event: str = Header(default=""),
        x_gitlab_token: str = Header(default=""),
    ):
        payload = await request.json()
        project = payload.get("project") or {}
        project_id = project.get("id")
        if project_id is None:
            raise HTTPException(400, "missing project.id")

        async with session_maker() as session:
            repo = (await session.execute(
                select(Repo).where(Repo.gitlab_project_id == project_id)
            )).scalar_one_or_none()
            if repo is None:
                raise HTTPException(404, "unknown project")
            if not hmac.compare_digest(repo.webhook_secret, x_gitlab_token):
                raise HTTPException(401, "bad token")

        notification = parse_event(x_gitlab_event, payload)
        if notification is None:
            return {"ok": True, "skipped": True}

        async def _run(n: Notification):
            try:
                async with session_maker() as s:
                    await dispatcher(n, s, bot)
            except Exception:
                log.exception("dispatch failed")

        background.add_task(_run, notification)
        return {"ok": True}

    return app
```

- [ ] **Step 3: Run + commit**

Run: `rtk .venv/bin/pytest tests/test_webhook_server.py -v`

```bash
git add -A && git commit -m "feat(webhook): FastAPI webhook server"
```

---

## Task 15: Bot commands — user-facing

**Files:**
- Create: `src/gitlab_notifier/bot/__init__.py`, `src/gitlab_notifier/bot/handlers.py`, `tests/test_handlers_user.py`

- [ ] **Step 1: Write `src/gitlab_notifier/bot/__init__.py`** (empty)

- [ ] **Step 2: Write `src/gitlab_notifier/bot/handlers.py`** with helper plus user commands.

```python
from __future__ import annotations
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler

from gitlab_notifier.db.models import Repo, Subscription, User
from gitlab_notifier.notifier.events import ALL_EVENTS, EVENT_NAMES, EventKind, mask_has, mask_set

log = logging.getLogger(__name__)


async def _get_or_create_user(session: AsyncSession, update: Update) -> User:
    eu = update.effective_user
    user = (await session.execute(
        select(User).where(User.telegram_id == eu.id)
    )).scalar_one_or_none()
    if user is None:
        user = User(telegram_id=eu.id, username=eu.username)
        session.add(user)
        await session.flush()
    elif user.username != eu.username:
        user.username = eu.username
    return user


def _maker(context: ContextTypes.DEFAULT_TYPE) -> async_sessionmaker:
    return context.application.bot_data["session_maker"]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _maker(context)() as s:
        await _get_or_create_user(s, update)
        await s.commit()
    await update.message.reply_text(
        "👋 Hi! Use /list to see registered repos, /subscribe <group/project> to get notifications."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Commands:\n"
        "/list — registered repos\n"
        "/subscribe <path> — subscribe to a repo\n"
        "/unsubscribe <path> — remove a subscription\n"
        "/mine — your subscriptions\n"
        "/filter <path> <event> on|off — toggle event for a sub\n"
        "Events: " + ", ".join(EVENT_NAMES.keys())
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _maker(context)() as s:
        repos = (await s.execute(select(Repo).order_by(Repo.path_with_namespace))).scalars().all()
    if not repos:
        await update.message.reply_text("No repos registered. Ask an admin to /addrepo.")
        return
    lines = [f"• `{r.path_with_namespace}`" for r in repos]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /subscribe group/project")
        return
    path = context.args[0]
    async with _maker(context)() as s:
        user = await _get_or_create_user(s, update)
        repo = (await s.execute(select(Repo).where(Repo.path_with_namespace == path))).scalar_one_or_none()
        if repo is None:
            await update.message.reply_text(f"Repo `{path}` not registered.", parse_mode="Markdown")
            return
        existing = (await s.execute(
            select(Subscription).where(Subscription.user_id == user.id, Subscription.repo_id == repo.id)
        )).scalar_one_or_none()
        if existing:
            existing.active = True
            existing.event_mask = ALL_EVENTS
            msg = f"Re-enabled subscription to `{path}`."
        else:
            s.add(Subscription(user_id=user.id, repo_id=repo.id, event_mask=ALL_EVENTS))
            msg = f"Subscribed to `{path}`."
        await s.commit()
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /unsubscribe group/project")
        return
    path = context.args[0]
    async with _maker(context)() as s:
        user = await _get_or_create_user(s, update)
        repo = (await s.execute(select(Repo).where(Repo.path_with_namespace == path))).scalar_one_or_none()
        if repo is None:
            await update.message.reply_text("Not subscribed.")
            return
        sub = (await s.execute(
            select(Subscription).where(Subscription.user_id == user.id, Subscription.repo_id == repo.id)
        )).scalar_one_or_none()
        if sub is None:
            await update.message.reply_text("Not subscribed.")
            return
        await s.delete(sub)
        await s.commit()
    await update.message.reply_text(f"Unsubscribed from `{path}`.", parse_mode="Markdown")


async def cmd_mine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _maker(context)() as s:
        user = await _get_or_create_user(s, update)
        rows = (await s.execute(
            select(Subscription, Repo).join(Repo, Subscription.repo_id == Repo.id)
            .where(Subscription.user_id == user.id)
        )).all()
        await s.commit()
    if not rows:
        await update.message.reply_text("No subscriptions.")
        return
    lines = []
    for sub, repo in rows:
        enabled = [name for name, k in EVENT_NAMES.items() if mask_has(sub.event_mask, k)]
        flag = "" if sub.active else " (inactive)"
        lines.append(f"• `{repo.path_with_namespace}`{flag} — {', '.join(enabled) or 'none'}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 3:
        await update.message.reply_text("Usage: /filter group/project <event> on|off")
        return
    path, event, state = context.args
    if event not in EVENT_NAMES or state not in {"on", "off"}:
        await update.message.reply_text("Bad arguments. Events: " + ", ".join(EVENT_NAMES))
        return
    async with _maker(context)() as s:
        user = await _get_or_create_user(s, update)
        repo = (await s.execute(select(Repo).where(Repo.path_with_namespace == path))).scalar_one_or_none()
        if repo is None:
            await update.message.reply_text("Repo not registered.")
            return
        sub = (await s.execute(
            select(Subscription).where(Subscription.user_id == user.id, Subscription.repo_id == repo.id)
        )).scalar_one_or_none()
        if sub is None:
            await update.message.reply_text("Not subscribed.")
            return
        sub.event_mask = mask_set(sub.event_mask, EVENT_NAMES[event], state == "on")
        await s.commit()
    await update.message.reply_text(f"OK. {event} {state} for `{path}`.", parse_mode="Markdown")


def register_user_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("mine", cmd_mine))
    app.add_handler(CommandHandler("filter", cmd_filter))
```

- [ ] **Step 3: Write `tests/test_handlers_user.py` (direct invocation, not full PTB stack)**

```python
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from gitlab_notifier.db.models import Base, Repo, Subscription, User
from gitlab_notifier.bot import handlers as h


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    M = async_sessionmaker(engine, expire_on_commit=False)
    async with M() as s:
        s.add(Repo(gitlab_project_id=1, path_with_namespace="team/api", webhook_secret="x"))
        await s.commit()
    yield M
    await engine.dispose()


def _ctx(maker, args=None):
    app = SimpleNamespace(bot_data={"session_maker": maker})
    return SimpleNamespace(application=app, args=args or [])


def _update(tg_id=10, username="alice"):
    msg = SimpleNamespace(reply_text=AsyncMock())
    user = SimpleNamespace(id=tg_id, username=username)
    return SimpleNamespace(effective_user=user, message=msg)


async def test_subscribe_creates_row(maker):
    upd = _update()
    await h.cmd_subscribe(upd, _ctx(maker, args=["team/api"]))
    async with maker() as s:
        rows = (await s.execute(select(Subscription))).scalars().all()
    assert len(rows) == 1
    upd.message.reply_text.assert_awaited()


async def test_subscribe_unknown_repo(maker):
    upd = _update()
    await h.cmd_subscribe(upd, _ctx(maker, args=["nope/x"]))
    upd.message.reply_text.assert_awaited()
    async with maker() as s:
        assert (await s.execute(select(Subscription))).scalars().all() == []


async def test_unsubscribe_removes(maker):
    upd = _update()
    await h.cmd_subscribe(upd, _ctx(maker, args=["team/api"]))
    await h.cmd_unsubscribe(upd, _ctx(maker, args=["team/api"]))
    async with maker() as s:
        assert (await s.execute(select(Subscription))).scalars().all() == []


async def test_filter_toggles_event(maker):
    upd = _update()
    await h.cmd_subscribe(upd, _ctx(maker, args=["team/api"]))
    await h.cmd_filter(upd, _ctx(maker, args=["team/api", "push", "off"]))
    async with maker() as s:
        sub = (await s.execute(select(Subscription))).scalar_one()
        from gitlab_notifier.notifier.events import EventKind, mask_has
        assert not mask_has(sub.event_mask, EventKind.PUSH)
```

- [ ] **Step 4: Run + commit**

Run: `rtk .venv/bin/pytest tests/test_handlers_user.py -v`

```bash
git add -A && git commit -m "feat(bot): user-facing subscription commands"
```

---

## Task 16: Admin commands

**Files:**
- Create: `src/gitlab_notifier/bot/admin.py`, `tests/test_handlers_admin.py`

- [ ] **Step 1: Write `src/gitlab_notifier/bot/admin.py`**

```python
from __future__ import annotations
import secrets
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from gitlab_notifier.db.models import Repo
from gitlab_notifier.gitlab.client import GitLabClient


def _is_admin(context: ContextTypes.DEFAULT_TYPE, tg_id: int) -> bool:
    return tg_id in context.application.bot_data["admin_ids"]


def _maker(context) -> async_sessionmaker:
    return context.application.bot_data["session_maker"]


def _gitlab(context) -> GitLabClient:
    return context.application.bot_data["gitlab"]


def _webhook_url(context) -> str:
    return context.application.bot_data["webhook_url"]


async def cmd_addrepo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(context, update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /addrepo group/project")
        return
    path = context.args[0]
    gl = _gitlab(context)
    project = await gl.get_project(path)
    project_id = project["id"]
    secret = secrets.token_urlsafe(32)
    hook = await gl.create_webhook(project_id, url=_webhook_url(context), secret=secret)
    async with _maker(context)() as s:
        existing = (await s.execute(select(Repo).where(Repo.gitlab_project_id == project_id))).scalar_one_or_none()
        if existing:
            existing.webhook_secret = secret
            existing.webhook_id = hook["id"]
            existing.path_with_namespace = project["path_with_namespace"]
        else:
            s.add(Repo(
                gitlab_project_id=project_id,
                path_with_namespace=project["path_with_namespace"],
                webhook_secret=secret,
                webhook_id=hook["id"],
            ))
        await s.commit()
    await update.message.reply_text(f"Added `{project['path_with_namespace']}` (hook id {hook['id']}).",
                                     parse_mode="Markdown")


async def cmd_removerepo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(context, update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /removerepo group/project")
        return
    path = context.args[0]
    async with _maker(context)() as s:
        repo = (await s.execute(select(Repo).where(Repo.path_with_namespace == path))).scalar_one_or_none()
        if repo is None:
            await update.message.reply_text("Not found.")
            return
        if repo.webhook_id:
            try:
                await _gitlab(context).delete_webhook(repo.gitlab_project_id, repo.webhook_id)
            except Exception:
                pass
        await s.delete(repo)
        await s.commit()
    await update.message.reply_text(f"Removed `{path}`.", parse_mode="Markdown")


async def cmd_repos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(context, update.effective_user.id):
        return
    async with _maker(context)() as s:
        repos = (await s.execute(select(Repo).order_by(Repo.path_with_namespace))).scalars().all()
    if not repos:
        await update.message.reply_text("No repos.")
        return
    lines = [f"• `{r.path_with_namespace}` (project={r.gitlab_project_id}, hook={r.webhook_id})" for r in repos]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def register_admin_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("addrepo", cmd_addrepo))
    app.add_handler(CommandHandler("removerepo", cmd_removerepo))
    app.add_handler(CommandHandler("repos", cmd_repos))
```

- [ ] **Step 2: Write `tests/test_handlers_admin.py`**

```python
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from gitlab_notifier.db.models import Base, Repo
from gitlab_notifier.bot import admin as a


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _ctx(maker, args=None, admin_ids=None, gitlab_client=None):
    app = SimpleNamespace(bot_data={
        "session_maker": maker,
        "admin_ids": set(admin_ids or [1]),
        "gitlab": gitlab_client,
        "webhook_url": "https://bot/example",
    })
    return SimpleNamespace(application=app, args=args or [])


def _update(tg_id=1):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=tg_id, username="admin"),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )


async def test_addrepo_creates_repo(maker):
    gl = AsyncMock()
    gl.get_project.return_value = {"id": 1, "path_with_namespace": "team/api"}
    gl.create_webhook.return_value = {"id": 99}
    await a.cmd_addrepo(_update(1), _ctx(maker, args=["team/api"], gitlab_client=gl))
    async with maker() as s:
        repos = (await s.execute(select(Repo))).scalars().all()
    assert len(repos) == 1 and repos[0].webhook_id == 99


async def test_addrepo_non_admin_ignored(maker):
    gl = AsyncMock()
    upd = _update(2)
    await a.cmd_addrepo(upd, _ctx(maker, args=["team/api"], admin_ids={1}, gitlab_client=gl))
    gl.get_project.assert_not_called()
    upd.message.reply_text.assert_not_called()


async def test_removerepo_deletes(maker):
    gl = AsyncMock()
    async with maker() as s:
        s.add(Repo(gitlab_project_id=1, path_with_namespace="team/api",
                   webhook_secret="x", webhook_id=99))
        await s.commit()
    await a.cmd_removerepo(_update(1), _ctx(maker, args=["team/api"], gitlab_client=gl))
    gl.delete_webhook.assert_awaited_with(1, 99)
    async with maker() as s:
        assert (await s.execute(select(Repo))).scalars().all() == []
```

- [ ] **Step 3: Run + commit**

Run: `rtk .venv/bin/pytest tests/test_handlers_admin.py -v`

```bash
git add -A && git commit -m "feat(bot): admin commands for repo registration"
```

---

## Task 17: Application entry point

**Files:**
- Create: `src/gitlab_notifier/app.py`

No new tests for the entry point itself — it's a wiring file. We rely on prior unit/integration tests.

- [ ] **Step 1: Write `src/gitlab_notifier/app.py`**

```python
from __future__ import annotations
import asyncio
import logging
import signal

import httpx
import uvicorn
from telegram.ext import Application

from gitlab_notifier.bot.admin import register_admin_handlers
from gitlab_notifier.bot.handlers import register_user_handlers
from gitlab_notifier.config import Settings
from gitlab_notifier.db.session import make_engine, make_session_maker
from gitlab_notifier.gitlab.client import GitLabClient
from gitlab_notifier.notifier.dispatcher import dispatch
from gitlab_notifier.webhook.server import build_app

log = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()

    engine = make_engine(settings.database_url)
    session_maker = make_session_maker(engine)

    http = httpx.AsyncClient(base_url=settings.gitlab_base_url, timeout=10.0)
    gitlab = GitLabClient(http, settings.gitlab_admin_token)

    tg_app: Application = Application.builder().token(settings.telegram_bot_token).build()
    tg_app.bot_data["session_maker"] = session_maker
    tg_app.bot_data["admin_ids"] = set(settings.admin_telegram_ids)
    tg_app.bot_data["gitlab"] = gitlab
    tg_app.bot_data["webhook_url"] = f"{settings.webhook_public_url.rstrip('/')}/gitlab/webhook"
    register_user_handlers(tg_app)
    register_admin_handlers(tg_app)

    fastapi_app = build_app(session_maker=session_maker, bot=tg_app.bot, dispatcher=dispatch)
    uv_config = uvicorn.Config(fastapi_app, host=settings.listen_host, port=settings.listen_port, log_level="info")
    server = uvicorn.Server(uv_config)

    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling()
    log.info("bot polling started")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    serve_task = asyncio.create_task(server.serve())
    await stop.wait()

    log.info("shutting down")
    server.should_exit = True
    await serve_task
    await tg_app.updater.stop()
    await tg_app.stop()
    await tg_app.shutdown()
    await http.aclose()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Smoke check imports**

Run: `rtk .venv/bin/python -c "import gitlab_notifier.app"`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "feat(app): wire bot, webhook, dispatcher together"
```

---

## Task 18: Docker, Compose, README

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `README.md`, `entrypoint.sh`

- [ ] **Step 1: Write `Dockerfile`**

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
COPY entrypoint.sh ./

RUN uv pip install --system --no-cache .

RUN chmod +x entrypoint.sh

CMD ["./entrypoint.sh"]
```

- [ ] **Step 2: Write `entrypoint.sh`**

```bash
#!/usr/bin/env sh
set -e
alembic upgrade head
exec python -m gitlab_notifier.app
```

- [ ] **Step 3: Write `docker-compose.yml`**

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: gitlab_notifier
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme}
      POSTGRES_DB: gitlab_notifier
    volumes:
      - db-data:/var/lib/postgresql/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U gitlab_notifier"]
      interval: 5s
      timeout: 3s
      retries: 10

  app:
    build: .
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://gitlab_notifier:${POSTGRES_PASSWORD:-changeme}@db:5432/gitlab_notifier
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8080:8080"
    restart: unless-stopped

volumes:
  db-data:
```

- [ ] **Step 4: Write `README.md`**

```markdown
# GitLab Notifier

Telegram bot that delivers notifications from a self-hosted GitLab to per-user subscribers.

## Setup

1. Create a Telegram bot via @BotFather. Save the token.
2. Create a GitLab personal access token with the `api` scope.
3. Find your Telegram user id (e.g. via @userinfobot).
4. Copy `.env.example` to `.env` and fill in:
   - `TELEGRAM_BOT_TOKEN`
   - `GITLAB_BASE_URL` (e.g. `https://gitlab.example.com`)
   - `GITLAB_ADMIN_TOKEN`
   - `WEBHOOK_PUBLIC_URL` — the HTTPS URL GitLab will POST to (use a reverse proxy like Caddy/Traefik for TLS)
   - `ADMIN_TELEGRAM_IDS` — comma-separated admin Telegram user IDs
   - `POSTGRES_PASSWORD` (compose only)

## Run

```bash
docker compose up -d --build
```

## Use

- DM the bot `/start`.
- Admin: `/addrepo group/project` to register a repo and auto-create the webhook.
- Anyone: `/list` to see registered repos, `/subscribe group/project`, `/mine`, `/filter group/project push off`, `/unsubscribe group/project`.

## Events

Subscriptions cover: pushes, tag pushes, MR opened/updated/merged/approved, MR comments, issue opened/closed, pipeline failures.

## Migrations

Always generated:

```bash
alembic revision --autogenerate -m "..."
alembic upgrade head
```

Never hand-edit migration files.
```

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "chore: docker-compose deployment"
```

---

## Task 19: Final verification

- [ ] **Step 1: Run full test suite**

Run: `rtk .venv/bin/pytest -v`
Expected: all green.

- [ ] **Step 2: Verify alembic migration applies on a fresh sqlite DB**

```bash
rtk env DATABASE_URL=sqlite+aiosqlite:///./_check.db .venv/bin/alembic upgrade head
rm -f _check.db
```

- [ ] **Step 3: Verify Docker build**

Run: `rtk docker build -t gitlab-notifier:dev .`
Expected: success.

- [ ] **Step 4: Final commit if anything changed**

```bash
git add -A && git diff --cached --quiet || git commit -m "chore: final verification fixes"
```
