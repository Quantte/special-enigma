FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
COPY entrypoint.sh ./

RUN uv pip install --system --no-cache .

RUN chmod +x entrypoint.sh

CMD ["./entrypoint.sh"]
