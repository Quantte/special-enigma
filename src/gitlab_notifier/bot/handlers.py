from __future__ import annotations

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from gitlab_notifier.db.models import Repo, Subscription, User
from gitlab_notifier.gitlab.client import GitLabClient
from gitlab_notifier.notifier.events import (
    EVENT_NAMES,
    mask_has,
    mask_set,
)
from gitlab_notifier.security.crypto import TokenCipher

from .subscriptions import make_user_client, subscribe_user_to_project

log = logging.getLogger(__name__)

PAGE_SIZE = 8


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


def _http(context) -> httpx.AsyncClient:
    return context.application.bot_data["http"]


def _cipher(context) -> TokenCipher:
    return context.application.bot_data["cipher"]


def _webhook_url(context) -> str:
    return context.application.bot_data["webhook_url"]


def _user_client(context, user: User) -> GitLabClient:
    return make_user_client(_http(context), _cipher(context), user)


# ---------- commands ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _maker(context)() as s:
        user = await _get_or_create_user(s, update)
        has_token = user.encrypted_gitlab_token is not None
        await s.commit()
    if has_token:
        await update.message.reply_text(
            "👋 You're set up. Use /projects to browse repos and subscribe."
        )
    else:
        await update.message.reply_text(
            "👋 Hi! To get started, send me a GitLab personal access token (scope: `api`) with:\n"
            "`/login <token>`\n"
            "Then use /projects to subscribe.",
            parse_mode="Markdown",
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Commands:\n"
        "/login <token> — store your GitLab token (scope: api)\n"
        "/logout — wipe your token\n"
        "/projects [query] — browse and subscribe to your projects\n"
        "/mine — your subscriptions\n"
        "/unsubscribe <path> — remove a subscription\n"
        "/filter <path> <event> on|off — toggle event for a sub\n"
        "Events: " + ", ".join(EVENT_NAMES.keys())
    )


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /login <gitlab_token>")
        return
    token = context.args[0]
    try:
        await update.message.delete()
    except Exception:
        pass

    gl = GitLabClient(_http(context), token)
    try:
        info = await gl.get_current_user()
    except httpx.HTTPStatusError as e:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Token rejected by GitLab: {e.response.status_code}",
        )
        return
    except httpx.HTTPError:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Could not reach GitLab.",
        )
        return

    async with _maker(context)() as s:
        user = await _get_or_create_user(s, update)
        user.encrypted_gitlab_token = _cipher(context).encrypt(token)
        await s.commit()

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"✅ Logged in as `{info.get('username', '?')}`. Use /projects to subscribe.",
        parse_mode="Markdown",
    )


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _maker(context)() as s:
        user = await _get_or_create_user(s, update)
        user.encrypted_gitlab_token = None
        await s.commit()
    await update.message.reply_text(
        "Logged out. Subscriptions are kept; /login again to manage projects."
    )


async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args) if context.args else None
    await _show_projects(update, context, query=query, page=1)


async def _show_projects(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    query: str | None,
    page: int,
    edit: bool = False,
) -> None:
    async with _maker(context)() as s:
        user = await _get_or_create_user(s, update)
        if user.encrypted_gitlab_token is None:
            target = update.callback_query.message if edit else update.message
            await target.reply_text("Send your token first with /login <token>.")
            return
        gl = _user_client(context, user)

        sub_pids = set(
            (
                await s.execute(
                    select(Repo.gitlab_project_id)
                    .join(Subscription, Subscription.repo_id == Repo.id)
                    .where(Subscription.user_id == user.id, Subscription.active.is_(True))
                )
            ).scalars().all()
        )
        await s.commit()

    try:
        projects = await gl.list_projects(search=query, page=page, per_page=PAGE_SIZE)
    except httpx.HTTPStatusError as e:
        msg = f"GitLab error: {e.response.status_code}. /login again?"
        if edit:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    if not projects and page == 1:
        text = "No projects found."
        if edit:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return

    buttons: list[list[InlineKeyboardButton]] = []
    for p in projects:
        marker = "✅" if p["id"] in sub_pids else "➕"
        label = f"{marker} {p['path_with_namespace']}"[:60]
        buttons.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"sub:{p['id']}:{page}:{query or ''}",
                )
            ]
        )

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("« prev", callback_data=f"page:{page - 1}:{query or ''}"))
    if len(projects) == PAGE_SIZE:
        nav.append(InlineKeyboardButton("next »", callback_data=f"page:{page + 1}:{query or ''}"))
    if nav:
        buttons.append(nav)

    header = f"Projects (page {page})"
    if query:
        header += f" — search: {query}"
    markup = InlineKeyboardMarkup(buttons)
    if edit:
        await update.callback_query.edit_message_text(header, reply_markup=markup)
    else:
        await update.message.reply_text(header, reply_markup=markup)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data.startswith("page:"):
        _, page_s, query = data.split(":", 2)
        await _show_projects(update, context, query=query or None, page=int(page_s), edit=True)
        return

    if data.startswith("sub:"):
        _, pid_s, page_s, query = data.split(":", 3)
        await _toggle_subscription(update, context, project_id=int(pid_s))
        await _show_projects(update, context, query=query or None, page=int(page_s), edit=True)
        return


async def _toggle_subscription(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, project_id: int
) -> None:
    async with _maker(context)() as s:
        user = await _get_or_create_user(s, update)
        if user.encrypted_gitlab_token is None:
            return
        gl = _user_client(context, user)

        existing_repo = (
            await s.execute(select(Repo).where(Repo.gitlab_project_id == project_id))
        ).scalar_one_or_none()

        if existing_repo is not None:
            sub = (
                await s.execute(
                    select(Subscription).where(
                        Subscription.user_id == user.id,
                        Subscription.repo_id == existing_repo.id,
                    )
                )
            ).scalar_one_or_none()
            if sub is not None and sub.active:
                await s.delete(sub)
                await s.commit()
                return

        try:
            project = await gl.get_project_by_id(project_id)
            await subscribe_user_to_project(
                session=s,
                user=user,
                project=project,
                user_client=gl,
                webhook_url=_webhook_url(context),
            )
            await s.commit()
        except Exception:
            log.exception("subscribe failed")
            await s.rollback()


# ---------- text commands for power users ----------

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
        await update.message.reply_text("No subscriptions. Use /projects to add some.")
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


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("mine", cmd_mine))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("filter", cmd_filter))
    app.add_handler(CallbackQueryHandler(on_callback))
