"""Settings for the fallback timetable: the channel that posts the PDFs, and the Telegram account that reads it.

Off unless SCHEDULE_CHANNEL is set. The account is a normal user session (a bot cannot read a channel's older posts),
so the session string is as sensitive as a password. `python -m app.timetable.login` makes it.
"""

import os
import re
from dataclasses import dataclass, field

from telethon.sessions import StringSession

from ..config import ConfigError

SESSION_VARS = ("SCHEDULE_TELEGRAM_API_ID", "SCHEDULE_TELEGRAM_API_HASH", "SCHEDULE_TELEGRAM_SESSION")
DEFAULT_SEARCH_LIMIT = 5  # newest documents in the channel to look through
PRIVATE_CHANNEL = re.compile(r"^-100\d+$")  # a private channel's id as its link shows it, e.g. -1004368645921


@dataclass(frozen=True)
class TimetableSource:
    channel: str  # a public username without the @, or a private channel's -100 id
    api_id: int
    api_hash: str = field(repr=False)
    session: str = field(repr=False)
    search_limit: int = DEFAULT_SEARCH_LIMIT

    @property
    def peer(self) -> str | int:
        """The channel as Telethon takes it: the username, or the numeric id of a private channel."""
        return int(self.channel) if self.channel.startswith("-") else self.channel

    def post_url(self, post_id: int) -> str:
        if self.channel.startswith("-"):
            return f"https://t.me/c/{self.channel[4:]}/{post_id}"  # private channels link by the id without -100
        return f"https://t.me/{self.channel}/{post_id}"

    @classmethod
    def from_env(cls) -> "TimetableSource | None":
        channel = _env("SCHEDULE_CHANNEL").lstrip("@")
        values = {name: _env(name) for name in SESSION_VARS}
        if not channel and not any(values.values()):
            return None

        missing = [name for name, value in values.items() if not value]
        if not channel:
            missing.insert(0, "SCHEDULE_CHANNEL")
        if missing:
            raise ConfigError(f"The timetable fallback is only partly configured, missing: {', '.join(missing)}")
        if channel.lstrip("-").isdigit() and not PRIVATE_CHANNEL.match(channel):
            raise ConfigError("SCHEDULE_CHANNEL must be a username, or the -100... id of a private channel from its link")
        try:
            api_id = int(values["SCHEDULE_TELEGRAM_API_ID"])
        except ValueError:
            raise ConfigError("SCHEDULE_TELEGRAM_API_ID must be a number") from None
        try:
            StringSession(values["SCHEDULE_TELEGRAM_SESSION"])
        except ValueError:
            raise ConfigError("SCHEDULE_TELEGRAM_SESSION is not a session string; create it with python -m app.timetable.login") from None

        return cls(
            channel=channel,
            api_id=api_id,
            api_hash=values["SCHEDULE_TELEGRAM_API_HASH"],
            session=values["SCHEDULE_TELEGRAM_SESSION"],
        )


def _env(key: str) -> str:
    return os.getenv(key, "").strip()
