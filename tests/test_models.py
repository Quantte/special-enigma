import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gitlab_notifier.db.models import Base, Repo, Subscription, User
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
