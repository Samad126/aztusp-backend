"""Run the grade watcher: `python -m app.watcher`.

Every WATCH_INTERVAL_MINUTES it signs in as each student who turned change notifications on, reads their
scores, and sends an email and/or Telegram message when a watched field changes. Students turn notifications
on through the API, and connect Telegram with a link (see /api/v1/me/telegram/link). While it runs, it also
answers the bot's /start messages so those links connect.

  --once                 check every student once and exit (does not listen for Telegram links)
  --test-email ADDRESS   send a test message to ADDRESS and exit
  --test-telegram CHAT   send a test message to the Telegram chat CHAT and exit
"""

import argparse
import logging
import threading
import time

from ..config import Settings
from ..db import UserStore
from .checker import Watcher
from .config import WatcherSettings
from .notify import notify
from .telegram import TelegramLinker


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Notify students by email and Telegram when a watched result changes.")
    parser.add_argument("--once", action="store_true", help="check every student once and exit")
    parser.add_argument("--test-email", metavar="ADDRESS", help="send a test message to this address and exit")
    parser.add_argument("--test-telegram", metavar="CHAT_ID", help="send a test message to this Telegram chat and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger(__name__)
    settings = Settings.from_env()
    watcher_settings = WatcherSettings.from_env()

    if args.test_email or args.test_telegram:
        if not notify(watcher_settings, args.test_email, args.test_telegram, "AZTUSP watcher test", "If you can read this, this channel works."):
            raise SystemExit(1)
        return

    store = UserStore(settings.database_url, settings.secret_key)
    watcher = Watcher(settings, watcher_settings, store)
    if args.once:
        watcher.check_all()
        return

    if watcher_settings.telegram_bot_token:
        linker = TelegramLinker(watcher_settings.telegram_bot_token, store)
        threading.Thread(target=linker.run_forever, name="telegram-links", daemon=True).start()
        log.info("Listening for Telegram link messages")

    minutes = watcher_settings.interval_seconds // 60
    log.info("Checking every %s minutes", minutes)
    while True:
        try:
            watcher.check_all()
        except Exception:
            # Keep running through database and portal trouble; the next run tries again.
            log.exception("Check run failed, trying again in %s minutes", minutes)
        time.sleep(watcher_settings.interval_seconds)


if __name__ == "__main__":
    main()
