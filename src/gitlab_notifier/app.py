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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings()

    engine = make_engine(settings.database_url)
    session_maker = make_session_maker(engine)

    http = httpx.AsyncClient(base_url=settings.gitlab_base_url, timeout=10.0)
    gitlab = GitLabClient(http, settings.gitlab_admin_token)

    tg_app: Application = Application.builder().token(settings.telegram_bot_token).build()
    tg_app.bot_data["session_maker"] = session_maker
    tg_app.bot_data["admin_ids"] = set(settings.admin_telegram_ids)
    tg_app.bot_data["gitlab"] = gitlab
    tg_app.bot_data["webhook_url"] = (
        f"{settings.webhook_public_url.rstrip('/')}/gitlab/webhook"
    )
    register_user_handlers(tg_app)
    register_admin_handlers(tg_app)

    fastapi_app = build_app(session_maker=session_maker, bot=tg_app.bot, dispatcher=dispatch)
    uv_config = uvicorn.Config(
        fastapi_app,
        host=settings.listen_host,
        port=settings.listen_port,
        log_level="info",
    )
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
