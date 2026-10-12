"""One pass of the watcher: sign in as each student who turned notifications on, read what they watch, notify changes."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin

from ..config import Settings
from ..db import Subscription, UserStore
from ..scraping.client import BadCredentials, SiteScraper
from ..scraping.targets import TARGETS_BY_NAME
from .absences import find_absence_changes, format_absence_message, take_absence_snapshot
from .config import WatcherSettings, watched_fields
from .notify import format_message, notify
from .results import find_changes, take_snapshot
from .tabs import (
    find_score_changes,
    find_work_changes,
    format_score_message,
    format_work_message,
    take_score_snapshot,
    take_work_snapshot,
)

log = logging.getLogger(__name__)

SCORES = TARGETS_BY_NAME["scores"]


@dataclass(frozen=True)
class CourseWatch:
    """A watch over the tab of every course: how it reads the tab, compares it with the last check, and words the message."""

    name: str  # the key of its snapshot in the store
    flag: str  # the notification setting that turns it on
    take: Callable[[SiteScraper, list[dict]], list[dict]]
    changes: Callable[[list[dict], list[dict]], list]
    message: Callable[[list, str], tuple[str, str]]  # (changes, frontend_url) -> subject, body


COURSE_WATCHES = (
    CourseWatch("absences", "absences", take_absence_snapshot, find_absence_changes, format_absence_message),
    CourseWatch("work", "individual_work", take_work_snapshot, find_work_changes, format_work_message),
    CourseWatch("scores", "course_scores", take_score_snapshot, find_score_changes, format_score_message),
)


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
                # Retrying a rejected password could lock the student's account. Checks resume once they log in again.
                continue
            try:
                self.check(subscription)
            except Exception:
                # One student's failure must not stop the others.
                log.exception("Check failed for user %s", subscription.user_id)
                self.store.record_check(subscription.user_id, "error")

    def check(self, subscription: Subscription) -> None:
        user_id = subscription.user_id
        notifications = subscription.notifications
        fields = watched_fields(notifications.fields)
        watches = [watch for watch in COURSE_WATCHES if getattr(notifications, watch.flag)]
        if not fields and not watches:
            # Nothing is watched any more (its fields were removed): don't sign in just to read pages nobody needs.
            return

        scraper = SiteScraper(self.settings, subscription.site_username)
        try:
            # Signs in on every check: the portal session lasts too short to reuse between checks.
            scraper.login(subscription.password)
        except BadCredentials:
            log.warning("User %s: the site rejected the saved password, checks are paused until they log in again", user_id)
            self.store.record_check(user_id, "wrong_password")
            return

        if fields:
            self._check_results(subscription, fields, scraper.scrape(SCORES))
        for watch in watches:
            self._check_course_watch(subscription, scraper, watch)
        self.store.record_check(user_id, "ok")

    def _check_results(self, subscription: Subscription, fields: list[str], scores: dict) -> None:
        user_id = subscription.user_id
        fields = tuple(fields)
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
            site_url = urljoin(self.settings.dashboard_url, SCORES.path)
            subject, body = format_message(changes, self.watcher_settings.frontend_url, site_url)
            notify(self.watcher_settings, subscription.notifications.email, subscription.telegram_chat_id, subject, body)
        log.info("User %s: checked %d courses, %d changed", user_id, len(current), len(changes))
        # Saved even when a channel failed: the failure is logged, and a retry would re-send to the channels that worked.
        self.store.save_snapshot(user_id, {"fields": list(fields), "courses": current})

    def _check_course_watch(self, subscription: Subscription, scraper: SiteScraper, watch: CourseWatch) -> None:
        user_id = subscription.user_id
        saved = self.store.load_course_snapshot(user_id, watch.name)
        previous = saved["courses"] if saved else []
        current = watch.take(scraper, previous)

        if saved is None:
            # First check: record what is there without announcing it.
            self.store.save_course_snapshot(user_id, watch.name, {"courses": current})
            log.info("User %s: saved the %s of %d courses as the baseline", user_id, watch.name, len(current))
            return
        if not current and previous:
            # No courses on the dashboard is more likely a site glitch than every course ending; keep the old snapshot.
            log.warning("User %s: no courses on the dashboard, keeping the last %s", user_id, watch.name)
            return

        changes = watch.changes(previous, current)
        if changes:
            subject, body = watch.message(changes, self.watcher_settings.frontend_url)
            notify(self.watcher_settings, subscription.notifications.email, subscription.telegram_chat_id, subject, body)
        log.info("User %s: checked %s in %d courses, %d changed", user_id, watch.name, len(current), len(changes))
        self.store.save_course_snapshot(user_id, watch.name, {"courses": current})
