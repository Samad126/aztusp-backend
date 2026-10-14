"""What `GET /me/schedule` returns: the student's timetable, read from the schedule channel's PDFs.

The answer is a `SchedulePage`. The timetable is one block per group, with both weeks in it. It comes in two layouts,
chosen with `view`: `list` fills `sections` with one row per session, and `grid` fills `grids` with the university's
layout, a row per weekday and a cell per lesson time that lists its cards. `url` is the channel post the timetable came
from. When no timetable is found, the lists are empty and `url` is empty.
"""

import logging
from typing import Literal

from ..scraping import courses
from ..scraping.client import SiteScraper
from . import channel
from .config import TimetableSource

log = logging.getLogger(__name__)

ScheduleView = Literal["list", "grid"]


def read_timetable(scraper: SiteScraper, source: TimetableSource | None, view: ScheduleView = "list") -> dict:
    if source is None:
        return _empty()

    groups = courses.student_groups(scraper)
    if not groups:
        return _empty()

    try:
        found = channel.search(source, groups)
    except Exception:
        # The channel is the only source, so a failed search is logged and answered as an empty timetable.
        log.exception("Could not search the timetable channel for groups %s", groups)
        return _empty()
    if not found:
        return _empty()

    return {
        "name": "schedule",
        "url": source.post_url(found[0].post_id),
        "tables": {},
        "pairs": {},
        "totals": {},
        "sections": {"semesters": [] if view == "grid" else _blocks(found)},
        "grids": _grids(found) if view == "grid" else [],
        "fields": {},
    }


def _empty() -> dict:
    return {
        "name": "schedule",
        "url": "",
        "tables": {},
        "pairs": {},
        "totals": {},
        "sections": {"semesters": []},
        "grids": [],
        "fields": {},
    }


def _blocks(found: list[channel.FoundGroup]) -> list[dict]:
    """One block per group, with both weeks in its rows; each row says which week it is in."""
    return [{"title": f"{hit.group} Dərs cədvəli", "rows": hit.lessons.sessions} for hit in found]


def _grids(found: list[channel.FoundGroup]) -> list[dict]:
    """One grid per group, with both weeks in its cells; each card says which week it is in."""
    return [{"title": f"{hit.group} Dərs cədvəli", **hit.lessons.grid} for hit in found]
