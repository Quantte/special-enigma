from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gitlab_notifier.bot import handlers as h
from gitlab_notifier.db.models import Base, Repo, Subscription
from gitlab_notifier.notifier.events import EventKind, mask_has


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
        assert not mask_has(sub.event_mask, EventKind.PUSH)


async def test_list_empty(maker):
    async with maker() as s:
        for r in (await s.execute(select(Repo))).scalars().all():
            await s.delete(r)
        await s.commit()
    upd = _update()
    await h.cmd_list(upd, _ctx(maker))
    upd.message.reply_text.assert_awaited()


async def test_mine_lists_subs(maker):
    upd = _update()
    await h.cmd_subscribe(upd, _ctx(maker, args=["team/api"]))
    upd2 = _update()
    await h.cmd_mine(upd2, _ctx(maker))
    upd2.message.reply_text.assert_awaited()
