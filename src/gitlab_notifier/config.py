from typing import Annotated

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_admins(v):
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [int(x) for x in v.split(",") if x.strip()]
    return v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str
    gitlab_base_url: str
    gitlab_admin_token: str
    webhook_public_url: str
    admin_telegram_ids: Annotated[list[int], NoDecode, BeforeValidator(_split_admins)] = Field(
        default_factory=list
    )
    database_url: str
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080
