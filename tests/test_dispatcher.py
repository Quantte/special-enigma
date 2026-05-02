from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from telegram.error import Forbidden

from gitlab_notifier.db.models import Base, Repo, Subscription, User
from gitlab_notifier.notifier.dispatcher import dispatch
from gitlab_notifier.notifier.events import ALL_EVENTS, EventKind
from gitlab_notifier.notifier.notification import Notification


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
        u3 = User(telegram_id=300, username="c")
        r = Repo(gitlab_project_id=1, path_with_namespace="team/api", webhook_secret="x")
        s.add_all([u1, u2, u3, r])
        await s.flush()
        s.add_all([
            Subscription(user_id=u1.id, repo_id=r.id, event_mask=ALL_EVENTS),
            Subscription(user_id=u2.id, repo_id=r.id, event_mask=ALL_EVENTS),
            Subscription(
                user_id=u3.id,
                repo_id=r.id,
                event_mask=ALL_EVENTS & ~EventKind.PUSH.value,
            ),
        ])
        await s.commit()


async def test_dispatch_sends_to_matching_subscribers(session_maker):
    await _seed(session_maker)
    bot = AsyncMock()
    n = Notification(
        kind=EventKind.PUSH, repo_path="team/api", gitlab_project_id=1,
        actor="alice", title="t", body="", url="u",
    )
    async with session_maker() as s:
        await dispatch(n, s, bot)
    sent_ids = sorted([call.kwargs["chat_id"] for call in bot.send_message.call_args_list])
    assert sent_ids == [100, 200]


async def test_dispatch_marks_inactive_on_forbidden(session_maker):
    await _seed(session_maker)
    bot = AsyncMock()
    bot.send_message.side_effect = Forbidden("blocked")
    n = Notification(
        kind=EventKind.PUSH, repo_path="team/api", gitlab_project_id=1,
        actor="alice", title="t", body="", url="u",
    )
    async with session_maker() as s:
        await dispatch(n, s, bot)
    async with session_maker() as s:
        rows = (await s.execute(select(Subscription).order_by(Subscription.id))).scalars().all()
        actives = [r.active for r in rows]
        assert actives[0] is False
        assert actives[1] is False
        assert actives[2] is True
