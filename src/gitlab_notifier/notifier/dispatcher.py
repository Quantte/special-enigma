import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from gitlab_notifier.db.models import Repo, Subscription, User

from .events import EventKind
from .formatter import format_notification
from .notification import Notification

log = logging.getLogger(__name__)


async def dispatch(n: Notification, session: AsyncSession, bot: Bot) -> None:
    repo = (
        await session.execute(
            select(Repo).where(Repo.gitlab_project_id == n.gitlab_project_id)
        )
    ).scalar_one_or_none()
    if repo is None:
        log.warning("dispatch: unknown project_id=%s", n.gitlab_project_id)
        return

    bit = EventKind(n.kind).value
    rows = (
        await session.execute(
            select(Subscription, User)
            .join(User, Subscription.user_id == User.id)
            .where(
                Subscription.repo_id == repo.id,
                Subscription.active.is_(True),
                Subscription.event_mask.op("&")(bit) != 0,
            )
        )
    ).all()

    text = format_notification(n)
    for sub, user in rows:
        await _send_with_retry(bot, user.telegram_id, text, sub)
    await session.commit()


async def _send_with_retry(
    bot: Bot, chat_id: int, text: str, sub: Subscription
) -> None:
    delay = 1.0
    for _ in range(3):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True,
            )
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
