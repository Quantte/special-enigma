import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
        r = await c.post(
            "/gitlab/webhook",
            json=payload,
            headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "wrong"},
        )
    assert r.status_code == 401


async def test_webhook_unknown_project_returns_404(app_ctx):
    app, _ = app_ctx
    payload = json.loads((FIX / "push.json").read_text())
    payload["project"]["id"] = 999
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        r = await c.post(
            "/gitlab/webhook",
            json=payload,
            headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "x"},
        )
    assert r.status_code == 404


async def test_webhook_dispatches_on_valid(app_ctx):
    app, calls = app_ctx
    payload = json.loads((FIX / "push.json").read_text())
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        r = await c.post(
            "/gitlab/webhook",
            json=payload,
            headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "topsecret"},
        )
    assert r.status_code == 200
    await asyncio.sleep(0.05)
    assert len(calls) == 1
    assert calls[0].repo_path == "team/api"
