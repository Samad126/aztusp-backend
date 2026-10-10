"""One pass of the watcher: sign in as each student who turned notifications on, read their scores, notify changes."""

import logging

from ..config import Settings
from ..db import Subscription, UserStore
from ..scraping.client import BadCredentials, SiteScraper
from ..scraping.targets import TARGETS_BY_NAME
from .config import WatcherSettings
from .notify import format_message, notify
from .results import find_changes, take_snapshot

log = logging.getLogger(__name__)

SCORES = TARGETS_BY_NAME["scores"]


class Watcher:
    def __init__(self, settings: Settings, watcher_settings: WatcherSettings, store: UserStore):
        self.settings = settings
        self.watcher_settings = watcher_settings
        self.store = store

    def check_all(self) -> None:
        subscriptions = self.store.subscribers()
        log.info("Checking %d student(s)", len(subscriptions))
        for subscription in subscriptions:
            if subscription.notifications.status == "wrong_password":
                # Retrying a rejected password could lock the student's account. Checks resume once they save it again.
                continue
            try:
                self.check(subscription)
            except Exception:
                # One student's failure must not stop the others.
                log.exception("Check failed for user %s", subscription.user_id)
                self.store.record_check(subscription.user_id, "error")

    def check(self, subscription: Subscription) -> None:
        user_id = subscription.user_id
        scraper = SiteScraper(self.settings, subscription.site_username)
        try:
            # Signs in on every check: the portal session lasts too short to reuse between checks.
            scraper.login(subscription.password)
        except BadCredentials:
            log.warning("User %s: the site rejected the saved password, checks are paused until it is saved again", user_id)
            self.store.record_check(user_id, "wrong_password")
            return

        scores = scraper.scrape(SCORES)
        self.store.record_check(user_id, "ok")

        fields = tuple(subscription.notifications.fields)
        current = take_snapshot(scores, fields)
        saved = self.store.load_snapshot(user_id)

        if saved is None or not set(fields) <= set(saved["fields"]):
            # First check, or a field was just added: record the results without announcing them.
            self.store.save_snapshot(user_id, {"fields": list(fields), "courses": current})
            log.info("User %s: saved %d courses as the baseline", user_id, len(current))
            return
        if not current and saved["courses"]:
            # An empty page is more likely a site glitch than every course vanishing; keep the old snapshot.
            log.warning("User %s: no courses on the scores page, keeping the last snapshot", user_id)
            return

        changes = find_changes(saved["courses"], current, fields)
        if changes:
            subject, body = format_message(changes)
            notify(self.watcher_settings, subscription.notifications.email, subscription.telegram_chat_id, subject, body)
        log.info("User %s: checked %d courses, %d changed", user_id, len(current), len(changes))
        # Saved even when a channel failed: the failure is logged, and a retry would re-send to the channels that worked.
        self.store.save_snapshot(user_id, {"fields": list(fields), "courses": current})
