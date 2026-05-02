from __future__ import annotations

import logging
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from gitlab_notifier.db.models import Repo
from gitlab_notifier.gitlab.client import GitLabClient

log = logging.getLogger(__name__)


def _is_admin(context: ContextTypes.DEFAULT_TYPE, tg_id: int) -> bool:
    return tg_id in context.application.bot_data["admin_ids"]


def _maker(context) -> async_sessionmaker:
    return context.application.bot_data["session_maker"]


def _gitlab(context) -> GitLabClient:
    return context.application.bot_data["gitlab"]


def _webhook_url(context) -> str:
    return context.application.bot_data["webhook_url"]


async def cmd_addrepo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(context, update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /addrepo group/project")
        return
    path = context.args[0]
    gl = _gitlab(context)
    project = await gl.get_project(path)
    project_id = project["id"]
    secret = secrets.token_urlsafe(32)
    hook = await gl.create_webhook(project_id, url=_webhook_url(context), secret=secret)
    async with _maker(context)() as s:
        existing = (
            await s.execute(select(Repo).where(Repo.gitlab_project_id == project_id))
        ).scalar_one_or_none()
        if existing:
            existing.webhook_secret = secret
            existing.webhook_id = hook["id"]
            existing.path_with_namespace = project["path_with_namespace"]
        else:
            s.add(Repo(
                gitlab_project_id=project_id,
                path_with_namespace=project["path_with_namespace"],
                webhook_secret=secret,
                webhook_id=hook["id"],
            ))
        await s.commit()
    await update.message.reply_text(
        f"Added `{project['path_with_namespace']}` (hook id {hook['id']}).",
        parse_mode="Markdown",
    )


async def cmd_removerepo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(context, update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /removerepo group/project")
        return
    path = context.args[0]
    async with _maker(context)() as s:
        repo = (
            await s.execute(select(Repo).where(Repo.path_with_namespace == path))
        ).scalar_one_or_none()
        if repo is None:
            await update.message.reply_text("Not found.")
            return
        if repo.webhook_id:
            try:
                await _gitlab(context).delete_webhook(repo.gitlab_project_id, repo.webhook_id)
            except Exception:
                log.exception("delete webhook failed")
        await s.delete(repo)
        await s.commit()
    await update.message.reply_text(f"Removed `{path}`.", parse_mode="Markdown")


async def cmd_repos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(context, update.effective_user.id):
        return
    async with _maker(context)() as s:
        repos = (
            await s.execute(select(Repo).order_by(Repo.path_with_namespace))
        ).scalars().all()
    if not repos:
        await update.message.reply_text("No repos.")
        return
    lines = [
        f"• `{r.path_with_namespace}` (project={r.gitlab_project_id}, hook={r.webhook_id})"
        for r in repos
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def register_admin_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("addrepo", cmd_addrepo))
    app.add_handler(CommandHandler("removerepo", cmd_removerepo))
    app.add_handler(CommandHandler("repos", cmd_repos))
