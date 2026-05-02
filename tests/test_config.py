from gitlab_notifier.config import Settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-token")
    monkeypatch.setenv("GITLAB_BASE_URL", "https://gitlab.example.com")
    monkeypatch.setenv("WEBHOOK_PUBLIC_URL", "https://bot.example.com")
    monkeypatch.setenv("SECRET_KEY", "x" * 44)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    s = Settings(_env_file=None)
    assert s.telegram_bot_token == "tg-token"
    assert s.gitlab_base_url == "https://gitlab.example.com"
    assert s.listen_port == 8080
