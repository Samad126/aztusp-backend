"""Connects students' Telegram chats to their accounts, without them typing anything.

The frontend shows a button that opens `t.me/<bot>?start=<code>`. Pressing Start sends `/start <code>` to the
bot. This watches for those messages, and the one-time code says which account the chat belongs to.
"""

import logging
import time

import requests

from ..db import UserStore
from .notify import NotifyError, send_telegram

log = logging.getLogger(__name__)

POLL_SECONDS = 25  # how long Telegram holds a request open waiting for new messages
RETRY_SECONDS = 10
CONNECTED = "Connected. You will get a message here when a watched result changes."
NO_CODE = "Open AZTUSP and tap Connect Telegram to get a fresh link."


class TelegramLinker:
    def __init__(self, token: str, store: UserStore):
        self.token = token
        self.store = store
        self.offset: int | None = None

    def run_forever(self) -> None:
        while True:
            try:
                self.poll_once()
            except requests.RequestException as exc:
                # The request URL holds the bot token, so only the error type is logged.
                log.error("Telegram request failed (%s), retrying", type(exc).__name__)
                time.sleep(RETRY_SECONDS)
            except Exception:
                log.exception("Telegram linking failed, retrying")
                time.sleep(RETRY_SECONDS)

    def poll_once(self) -> None:
        params: dict = {"timeout": POLL_SECONDS}
        if self.offset is not None:
            params["offset"] = self.offset
        response = requests.get(
            f"https://api.telegram.org/bot{self.token}/getUpdates", params=params, timeout=POLL_SECONDS + 10
        )
        if not response.ok:
            raise NotifyError(f"Telegram getUpdates failed: HTTP {response.status_code}")

        for update in response.json().get("result", []):
            self.offset = update["update_id"] + 1
            message = update.get("message") or {}
            text = message.get("text", "")
            if message.get("chat", {}).get("type") == "private" and text.split(maxsplit=1)[:1] == ["/start"]:
                self.handle_start(str(message["chat"]["id"]), text)

    def handle_start(self, chat_id: str, text: str) -> None:
        parts = text.split(maxsplit=1)
        user_id = self.store.redeem_link_code(parts[1].strip()) if len(parts) == 2 else None
        if user_id is None:
            reply = NO_CODE
        else:
            self.store.set_telegram_chat(user_id, chat_id)
            log.info("Linked a Telegram chat to user %s", user_id)
            reply = CONNECTED
        try:
            send_telegram(self.token, chat_id, reply)
        except NotifyError as exc:
            log.error("%s", exc)
