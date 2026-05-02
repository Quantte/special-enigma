from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gitlab_notifier.bot import admin as a
from gitlab_notifier.db.models import Base, Repo


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
    assert len(repos) == 1
    assert repos[0].webhook_id == 99


async def test_addrepo_non_admin_ignored(maker):
    gl = AsyncMock()
    upd = _update(2)
    await a.cmd_addrepo(upd, _ctx(maker, args=["team/api"], admin_ids={1}, gitlab_client=gl))
    gl.get_project.assert_not_called()
    upd.message.reply_text.assert_not_called()


async def test_removerepo_deletes(maker):
    gl = AsyncMock()
    async with maker() as s:
        s.add(Repo(
            gitlab_project_id=1,
            path_with_namespace="team/api",
            webhook_secret="x",
            webhook_id=99,
        ))
        await s.commit()
    await a.cmd_removerepo(_update(1), _ctx(maker, args=["team/api"], gitlab_client=gl))
    gl.delete_webhook.assert_awaited_with(1, 99)
    async with maker() as s:
        assert (await s.execute(select(Repo))).scalars().all() == []
