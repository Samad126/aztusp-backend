"""What `GET /me/schedule` returns: the university's timetable, or the channel's when the university has no lessons.

Both answers are a `SchedulePage`. The channel's timetable is one block per group, with both weeks in it. It comes in two
views: `list` has one row per session, and `grid` has one row per lesson time with a column per weekday, as the university
shows it. Each session says which week it is in. `url` is the channel post the timetable came from. The university's own
timetable is returned as it is, whatever the view.
"""

import logging
from typing import Literal

from ..scraping import courses
from ..scraping.client import SiteScraper
from ..scraping.targets import TARGETS_BY_NAME
from . import channel
from .config import TimetableSource

log = logging.getLogger(__name__)

ScheduleView = Literal["list", "grid"]


def read_timetable(scraper: SiteScraper, source: TimetableSource | None, view: ScheduleView = "list") -> dict:
    page = scraper.scrape(TARGETS_BY_NAME["schedule"])
    if source is None or _has_lessons(page):
        return page

    groups = courses.student_groups(scraper)
    if not groups:
        return page

    try:
        found = channel.search(source, groups)
    except Exception:
        # The fallback is extra; if it fails the student still gets the (empty) university timetable.
        log.exception("Could not search the timetable channel for groups %s", groups)
        return page
    if not found:
        return page

    return {
        "name": page["name"],
        "url": source.post_url(found[0].post_id),
        "tables": {},
        "pairs": {},
        "totals": {},
        "sections": {"semesters": _blocks(found, view)},
        "fields": {},
    }


def _blocks(found: list[channel.FoundGroup], view: ScheduleView) -> list[dict]:
    """One block per group, with both weeks in its rows."""
    return [{"title": f"{hit.group} Dərs cədvəli", "rows": hit.lessons.rows(view)} for hit in found]


def _has_lessons(page: dict) -> bool:
    return any(block["rows"] for block in page["sections"].get("semesters", []))
