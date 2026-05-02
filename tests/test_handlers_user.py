from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gitlab_notifier.bot import handlers as h
from gitlab_notifier.bot.subscriptions import subscribe_user_to_project
from gitlab_notifier.db.models import Base, Repo, Subscription, User
from gitlab_notifier.gitlab.client import GitLabClient
from gitlab_notifier.notifier.events import EventKind, mask_has
from gitlab_notifier.security.crypto import TokenCipher


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def cipher():
    return TokenCipher(Fernet.generate_key())


def _ctx(maker, *, args=None, http=None, cipher=None):
    app = SimpleNamespace(bot_data={
        "session_maker": maker,
        "http": http,
        "cipher": cipher,
        "webhook_url": "https://bot.example/gitlab/webhook",
    })
    bot = SimpleNamespace(send_message=AsyncMock())
    return SimpleNamespace(application=app, args=args or [], bot=bot)


def _update(tg_id=10, username="alice"):
    msg = SimpleNamespace(reply_text=AsyncMock(), delete=AsyncMock())
    user = SimpleNamespace(id=tg_id, username=username)
    chat = SimpleNamespace(id=tg_id)
    return SimpleNamespace(effective_user=user, effective_chat=chat, message=msg)


# ---------- /login ----------

async def test_login_validates_and_stores_encrypted_token(maker, cipher):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["PRIVATE-TOKEN"] == "glpat-xyz"
        return httpx.Response(200, json={"id": 5, "username": "alice"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://gitlab.example.com") as http:
        upd = _update()
        ctx = _ctx(maker, args=["glpat-xyz"], http=http, cipher=cipher)
        await h.cmd_login(upd, ctx)

    async with maker() as s:
        u = (await s.execute(select(User))).scalar_one()
        assert u.encrypted_gitlab_token is not None
        assert cipher.decrypt(u.encrypted_gitlab_token) == "glpat-xyz"
    ctx.bot.send_message.assert_awaited()


async def test_login_rejects_bad_token(maker, cipher):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "401 Unauthorized"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://gitlab.example.com") as http:
        upd = _update()
        ctx = _ctx(maker, args=["bad"], http=http, cipher=cipher)
        await h.cmd_login(upd, ctx)

    async with maker() as s:
        rows = (await s.execute(select(User))).scalars().all()
        assert rows == [] or rows[0].encrypted_gitlab_token is None


# ---------- /logout ----------

async def test_logout_clears_token_keeps_subscriptions(maker, cipher):
    async with maker() as s:
        u = User(telegram_id=10, encrypted_gitlab_token=cipher.encrypt("t"))
        r = Repo(gitlab_project_id=1, path_with_namespace="a/b", webhook_secret="s")
        s.add_all([u, r])
        await s.flush()
        s.add(Subscription(user_id=u.id, repo_id=r.id, event_mask=1))
        await s.commit()

    upd = _update()
    await h.cmd_logout(upd, _ctx(maker, cipher=cipher))

    async with maker() as s:
        u = (await s.execute(select(User))).scalar_one()
        assert u.encrypted_gitlab_token is None
        subs = (await s.execute(select(Subscription))).scalars().all()
        assert len(subs) == 1


# ---------- subscribe service ----------

async def test_subscribe_creates_repo_and_webhook_on_first(maker, cipher):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        if request.method == "POST":
            return httpx.Response(201, json={"id": 99})
        return httpx.Response(200, json={"id": 7, "path_with_namespace": "team/api"})

    async with maker() as s:
        u = User(telegram_id=10, encrypted_gitlab_token=cipher.encrypt("t"))
        s.add(u)
        await s.commit()
        u_id = u.id

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://gitlab.example.com") as http:
        gl = GitLabClient(http, "t")
        async with maker() as s:
            user = (await s.execute(select(User).where(User.id == u_id))).scalar_one()
            await subscribe_user_to_project(
                session=s,
                user=user,
                project={"id": 7, "path_with_namespace": "team/api"},
                user_client=gl,
                webhook_url="https://bot/example",
            )
            await s.commit()

    async with maker() as s:
        repo = (await s.execute(select(Repo))).scalar_one()
        assert repo.webhook_id == 99
        assert repo.created_by_user_id == u_id
        sub = (await s.execute(select(Subscription))).scalar_one()
        assert sub.repo_id == repo.id


async def test_subscribe_skips_webhook_if_repo_exists(maker, cipher):
    create_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_calls
        if request.method == "POST":
            create_calls += 1
        return httpx.Response(201, json={"id": 99})

    async with maker() as s:
        u1 = User(telegram_id=10, encrypted_gitlab_token=cipher.encrypt("t"))
        u2 = User(telegram_id=20, encrypted_gitlab_token=cipher.encrypt("t"))
        r = Repo(gitlab_project_id=7, path_with_namespace="team/api",
                 webhook_secret="s", webhook_id=42, created_by_user_id=None)
        s.add_all([u1, u2, r])
        await s.flush()
        s.add(Subscription(user_id=u1.id, repo_id=r.id, event_mask=1))
        await s.commit()
        u2_id = u2.id

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://gitlab.example.com") as http:
        gl = GitLabClient(http, "t")
        async with maker() as s:
            user = (await s.execute(select(User).where(User.id == u2_id))).scalar_one()
            await subscribe_user_to_project(
                session=s,
                user=user,
                project={"id": 7, "path_with_namespace": "team/api"},
                user_client=gl,
                webhook_url="https://bot/example",
            )
            await s.commit()

    assert create_calls == 0
    async with maker() as s:
        subs = (await s.execute(select(Subscription))).scalars().all()
        assert len(subs) == 2


# ---------- /unsubscribe & /filter & /mine ----------

async def test_unsubscribe_removes(maker, cipher):
    async with maker() as s:
        u = User(telegram_id=10, encrypted_gitlab_token=cipher.encrypt("t"))
        r = Repo(gitlab_project_id=1, path_with_namespace="team/api", webhook_secret="s")
        s.add_all([u, r])
        await s.flush()
        s.add(Subscription(user_id=u.id, repo_id=r.id, event_mask=1))
        await s.commit()

    upd = _update()
    await h.cmd_unsubscribe(upd, _ctx(maker, args=["team/api"], cipher=cipher))
    async with maker() as s:
        assert (await s.execute(select(Subscription))).scalars().all() == []


async def test_filter_toggles_event(maker, cipher):
    from gitlab_notifier.notifier.events import ALL_EVENTS
    async with maker() as s:
        u = User(telegram_id=10, encrypted_gitlab_token=cipher.encrypt("t"))
        r = Repo(gitlab_project_id=1, path_with_namespace="team/api", webhook_secret="s")
        s.add_all([u, r])
        await s.flush()
        s.add(Subscription(user_id=u.id, repo_id=r.id, event_mask=ALL_EVENTS))
        await s.commit()

    upd = _update()
    await h.cmd_filter(upd, _ctx(maker, args=["team/api", "push", "off"], cipher=cipher))
    async with maker() as s:
        sub = (await s.execute(select(Subscription))).scalar_one()
        assert not mask_has(sub.event_mask, EventKind.PUSH)


async def test_mine_lists_subs(maker, cipher):
    async with maker() as s:
        u = User(telegram_id=10, encrypted_gitlab_token=cipher.encrypt("t"))
        r = Repo(gitlab_project_id=1, path_with_namespace="team/api", webhook_secret="s")
        s.add_all([u, r])
        await s.flush()
        s.add(Subscription(user_id=u.id, repo_id=r.id, event_mask=1))
        await s.commit()

    upd = _update()
    await h.cmd_mine(upd, _ctx(maker, cipher=cipher))
    upd.message.reply_text.assert_awaited()
