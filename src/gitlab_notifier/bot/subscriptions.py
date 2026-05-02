from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gitlab_notifier.db.models import Repo, Subscription, User
from gitlab_notifier.gitlab.client import GitLabClient
from gitlab_notifier.notifier.events import ALL_EVENTS
from gitlab_notifier.security.crypto import TokenCipher

log = logging.getLogger(__name__)


@dataclass
class SubscribeResult:
    repo: Repo
    created_repo: bool
    reactivated: bool


def make_user_client(http: httpx.AsyncClient, cipher: TokenCipher, user: User) -> GitLabClient:
    if user.encrypted_gitlab_token is None:
        raise PermissionError("user has no GitLab token")
    token = cipher.decrypt(user.encrypted_gitlab_token)
    return GitLabClient(http, token)


async def subscribe_user_to_project(
    *,
    session: AsyncSession,
    user: User,
    project: dict,
    user_client: GitLabClient,
    webhook_url: str,
) -> SubscribeResult:
    """Subscribe a user to a project, creating the Repo + webhook on first subscription."""
    project_id = project["id"]
    path = project["path_with_namespace"]

    repo = (
        await session.execute(select(Repo).where(Repo.gitlab_project_id == project_id))
    ).scalar_one_or_none()

    created_repo = False
    if repo is None:
        secret = secrets.token_urlsafe(32)
        hook = await user_client.create_webhook(project_id, url=webhook_url, secret=secret)
        repo = Repo(
            gitlab_project_id=project_id,
            path_with_namespace=path,
            webhook_secret=secret,
            webhook_id=hook["id"],
            created_by_user_id=user.id,
        )
        session.add(repo)
        await session.flush()
        created_repo = True
    elif repo.path_with_namespace != path:
        repo.path_with_namespace = path

    sub = (
        await session.execute(
            select(Subscription).where(
                Subscription.user_id == user.id, Subscription.repo_id == repo.id
            )
        )
    ).scalar_one_or_none()

    reactivated = False
    if sub is None:
        session.add(
            Subscription(user_id=user.id, repo_id=repo.id, event_mask=ALL_EVENTS)
        )
    elif not sub.active:
        sub.active = True
        sub.event_mask = ALL_EVENTS
        reactivated = True

    return SubscribeResult(repo=repo, created_repo=created_repo, reactivated=reactivated)
