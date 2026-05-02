from __future__ import annotations

import hmac
import logging
from typing import Awaitable, Callable

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Bot

from gitlab_notifier.db.models import Repo
from gitlab_notifier.notifier.notification import Notification

from .parsers import parse_event

log = logging.getLogger(__name__)

DispatcherFn = Callable[[Notification, AsyncSession, Bot], Awaitable[None]]


def build_app(
    *,
    session_maker: async_sessionmaker,
    bot: Bot,
    dispatcher: DispatcherFn,
) -> FastAPI:
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
            repo = (
                await session.execute(
                    select(Repo).where(Repo.gitlab_project_id == project_id)
                )
            ).scalar_one_or_none()
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
