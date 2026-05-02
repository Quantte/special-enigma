from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from gitlab_notifier.db.models import Repo, Subscription, User
from gitlab_notifier.notifier.events import (
    ALL_EVENTS,
    EVENT_NAMES,
    mask_has,
    mask_set,
)

log = logging.getLogger(__name__)


async def _get_or_create_user(session: AsyncSession, update: Update) -> User:
    eu = update.effective_user
    user = (
        await session.execute(select(User).where(User.telegram_id == eu.id))
    ).scalar_one_or_none()
    if user is None:
        user = User(telegram_id=eu.id, username=eu.username)
        session.add(user)
        await session.flush()
    elif user.username != eu.username:
        user.username = eu.username
    return user


def _maker(context: ContextTypes.DEFAULT_TYPE) -> async_sessionmaker:
    return context.application.bot_data["session_maker"]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _maker(context)() as s:
        await _get_or_create_user(s, update)
        await s.commit()
    await update.message.reply_text(
        "👋 Hi! Use /list to see registered repos, "
        "/subscribe <group/project> to get notifications."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Commands:\n"
        "/list — registered repos\n"
        "/subscribe <path> — subscribe to a repo\n"
        "/unsubscribe <path> — remove a subscription\n"
        "/mine — your subscriptions\n"
        "/filter <path> <event> on|off — toggle event for a sub\n"
        "Events: " + ", ".join(EVENT_NAMES.keys())
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _maker(context)() as s:
        repos = (
            await s.execute(select(Repo).order_by(Repo.path_with_namespace))
        ).scalars().all()
    if not repos:
        await update.message.reply_text("No repos registered. Ask an admin to /addrepo.")
        return
    lines = [f"• `{r.path_with_namespace}`" for r in repos]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /subscribe group/project")
        return
    path = context.args[0]
    async with _maker(context)() as s:
        user = await _get_or_create_user(s, update)
        repo = (
            await s.execute(select(Repo).where(Repo.path_with_namespace == path))
        ).scalar_one_or_none()
        if repo is None:
            await update.message.reply_text(
                f"Repo `{path}` not registered.", parse_mode="Markdown"
            )
            return
        existing = (
            await s.execute(
                select(Subscription).where(
                    Subscription.user_id == user.id,
                    Subscription.repo_id == repo.id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            existing.active = True
            existing.event_mask = ALL_EVENTS
            msg = f"Re-enabled subscription to `{path}`."
        else:
            s.add(Subscription(user_id=user.id, repo_id=repo.id, event_mask=ALL_EVENTS))
            msg = f"Subscribed to `{path}`."
        await s.commit()
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /unsubscribe group/project")
        return
    path = context.args[0]
    async with _maker(context)() as s:
        user = await _get_or_create_user(s, update)
        repo = (
            await s.execute(select(Repo).where(Repo.path_with_namespace == path))
        ).scalar_one_or_none()
        if repo is None:
            await update.message.reply_text("Not subscribed.")
            return
        sub = (
            await s.execute(
                select(Subscription).where(
                    Subscription.user_id == user.id,
                    Subscription.repo_id == repo.id,
                )
            )
        ).scalar_one_or_none()
        if sub is None:
            await update.message.reply_text("Not subscribed.")
            return
        await s.delete(sub)
        await s.commit()
    await update.message.reply_text(f"Unsubscribed from `{path}`.", parse_mode="Markdown")


async def cmd_mine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _maker(context)() as s:
        user = await _get_or_create_user(s, update)
        rows = (
            await s.execute(
                select(Subscription, Repo)
                .join(Repo, Subscription.repo_id == Repo.id)
                .where(Subscription.user_id == user.id)
            )
        ).all()
        await s.commit()
    if not rows:
        await update.message.reply_text("No subscriptions.")
        return
    lines = []
    for sub, repo in rows:
        enabled = [
            name for name, k in EVENT_NAMES.items() if mask_has(sub.event_mask, k)
        ]
        flag = "" if sub.active else " (inactive)"
        lines.append(
            f"• `{repo.path_with_namespace}`{flag} — {', '.join(enabled) or 'none'}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 3:
        await update.message.reply_text("Usage: /filter group/project <event> on|off")
        return
    path, event, state = context.args
    if event not in EVENT_NAMES or state not in {"on", "off"}:
        await update.message.reply_text(
            "Bad arguments. Events: " + ", ".join(EVENT_NAMES)
        )
        return
    async with _maker(context)() as s:
        user = await _get_or_create_user(s, update)
        repo = (
            await s.execute(select(Repo).where(Repo.path_with_namespace == path))
        ).scalar_one_or_none()
        if repo is None:
            await update.message.reply_text("Repo not registered.")
            return
        sub = (
            await s.execute(
                select(Subscription).where(
                    Subscription.user_id == user.id,
                    Subscription.repo_id == repo.id,
                )
            )
        ).scalar_one_or_none()
        if sub is None:
            await update.message.reply_text("Not subscribed.")
            return
        sub.event_mask = mask_set(sub.event_mask, EVENT_NAMES[event], state == "on")
        await s.commit()
    await update.message.reply_text(
        f"OK. {event} {state} for `{path}`.", parse_mode="Markdown"
    )


def register_user_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("mine", cmd_mine))
    app.add_handler(CommandHandler("filter", cmd_filter))
