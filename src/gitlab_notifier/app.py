from __future__ import annotations

import asyncio
import logging
import signal

import httpx
import uvicorn
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application
from telegram.request import HTTPXRequest

from gitlab_notifier.bot.handlers import register_handlers
from gitlab_notifier.config import Settings
from gitlab_notifier.db.session import make_engine, make_session_maker
from gitlab_notifier.notifier.dispatcher import dispatch
from gitlab_notifier.security.crypto import TokenCipher
from gitlab_notifier.webhook.server import build_app

log = logging.getLogger(__name__)


async def _retry(coro_factory, *, what: str, attempts: int = 10, base_delay: float = 2.0) -> None:
    delay = base_delay
    for i in range(1, attempts + 1):
        try:
            await coro_factory()
            return
        except (TimedOut, NetworkError, httpx.HTTPError) as e:
            if i == attempts:
                raise
            log.warning("%s failed (attempt %d/%d): %s; retrying in %.1fs", what, i, attempts, e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings()

    engine = make_engine(settings.database_url)
    session_maker = make_session_maker(engine)

    http = httpx.AsyncClient(base_url=settings.gitlab_base_url, timeout=10.0)
    cipher = TokenCipher(settings.secret_key)

    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0, pool_timeout=30.0)
    get_updates_request = HTTPXRequest(connect_timeout=30.0, read_timeout=60.0, write_timeout=30.0, pool_timeout=30.0)
    tg_app: Application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .request(request)
        .get_updates_request(get_updates_request)
        .build()
    )
    tg_app.bot_data["session_maker"] = session_maker
    tg_app.bot_data["http"] = http
    tg_app.bot_data["cipher"] = cipher
    tg_app.bot_data["webhook_url"] = (
        f"{settings.webhook_public_url.rstrip('/')}/gitlab/webhook"
    )
    register_handlers(tg_app)

    fastapi_app = build_app(session_maker=session_maker, bot=tg_app.bot, dispatcher=dispatch)
    uv_config = uvicorn.Config(
        fastapi_app,
        host=settings.listen_host,
        port=settings.listen_port,
        log_level="info",
    )
    server = uvicorn.Server(uv_config)

    await _retry(tg_app.initialize, what="telegram initialize")
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
