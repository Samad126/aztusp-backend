"""Settings of the grade watcher: how often it checks, and the mail and Telegram accounts that send the messages.

Students' own emails and Telegram chat ids are not here; each student saves them through the API.
"""

import os
from dataclasses import dataclass, field

from ..config import ConfigError

WATCHABLE_FIELDS = ("final_score", "grade")
DEFAULT_FIELDS = ("final_score", "grade")
# Watched as one: choosing either watches both, and a change to either is sent with both.
RESULT_PAIR = ("final_score", "grade")
DEFAULT_INTERVAL_MINUTES = 30
DEFAULT_FRONTEND_URL = "https://aztu.alakbaroff.com"


def watched_fields(fields) -> list[str]:
    """The result fields to watch, in WATCHABLE_FIELDS order. Final score and grade always come together, and names that
    are no longer offered (course type, credits and retake, saved before they were removed) are dropped."""
    chosen = set(fields)
    if chosen & set(RESULT_PAIR):
        chosen |= set(RESULT_PAIR)
    return [name for name in WATCHABLE_FIELDS if name in chosen]


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    username: str = field(repr=False)
    password: str = field(repr=False)
    sender: str


@dataclass(frozen=True)
class WatcherSettings:
    interval_seconds: int
    smtp: SmtpSettings | None
    telegram_bot_token: str | None = field(default=None, repr=False)
    frontend_url: str = DEFAULT_FRONTEND_URL  # AZTUSP, where the links in a message open; no trailing slash

    @classmethod
    def from_env(cls) -> "WatcherSettings":
        minutes = int(_env("WATCH_INTERVAL_MINUTES", str(DEFAULT_INTERVAL_MINUTES)))
        if minutes < 1:
            raise ConfigError("WATCH_INTERVAL_MINUTES must be at least 1")

        smtp = _smtp_from_env()
        token = _env("TELEGRAM_BOT_TOKEN") or None
        if smtp is None and token is None:
            raise ConfigError(
                "No delivery channel configured: set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD for email, "
                "TELEGRAM_BOT_TOKEN for Telegram, or both"
            )
        frontend_url = (_env("FRONTEND_URL") or DEFAULT_FRONTEND_URL).rstrip("/")
        return cls(interval_seconds=minutes * 60, smtp=smtp, telegram_bot_token=token, frontend_url=frontend_url)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _smtp_from_env() -> SmtpSettings | None:
    names = ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD")
    values = {name: _env(name) for name in names}
    if not any(values.values()):
        return None
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError(f"Email is only partly configured, missing: {', '.join(missing)}")
    return SmtpSettings(
        host=values["SMTP_HOST"],
        port=int(_env("SMTP_PORT", "587")),
        username=values["SMTP_USERNAME"],
        password=values["SMTP_PASSWORD"],
        sender=_env("MAIL_FROM") or values["SMTP_USERNAME"],
    )
