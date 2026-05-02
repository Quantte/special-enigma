#!/usr/bin/env sh
set -e
alembic upgrade head
exec python -m gitlab_notifier.app
