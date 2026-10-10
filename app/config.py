import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    secret_key: str
    database_url: str
    base_domain: str
    login_url: str
    dashboard_url: str
    username_field: str
    password_field: str
    timeout: float
    telegram_bot_username: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        def required(key: str) -> str:
            value = os.getenv(key, "").strip()
            if not value:
                raise ConfigError(f"Missing required environment variable: {key}")
            return value

        settings = cls(
            secret_key=required("APP_SECRET_KEY"),
            database_url=required("DATABASE_URL"),
            base_domain=required("BASE_DOMAIN").lower().lstrip("."),
            login_url=required("LOGIN_URL"),
            dashboard_url=required("DASHBOARD_URL"),
            username_field=os.getenv("USERNAME_FIELD", "username").strip(),
            password_field=os.getenv("PASSWORD_FIELD", "password").strip(),
            timeout=float(os.getenv("REQUEST_TIMEOUT", "20")),
            telegram_bot_username=os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@") or None,
        )

        for url in (settings.login_url, settings.dashboard_url):
            host = urlparse(url).hostname or ""
            if host != settings.base_domain and not host.endswith("." + settings.base_domain):
                raise ConfigError(f"{url} is not on BASE_DOMAIN ({settings.base_domain})")

        return settings
