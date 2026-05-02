from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str
    gitlab_base_url: str
    webhook_public_url: str
    secret_key: str  # url-safe base64-encoded 32-byte Fernet key
    database_url: str
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080
