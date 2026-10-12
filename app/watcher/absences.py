"""Read the student's own marks in the attendance journals, and find the absences that appeared since the last check."""

import logging
from dataclasses import dataclass

import requests

from ..scraping import courses
from ..scraping.client import SiteScraper

log = logging.getLogger(__name__)

PRESENT, ABSENT, NOT_ENTERED = "present", "absent", "not_entered"
MARK_LABELS = {PRESENT: "present", ABSENT: "absent", NOT_ENTERED: "not entered"}


@dataclass(frozen=True)
class AbsenceChange:
    course: str
    session: str  # class meeting number
    date: str | None
    old: str | None  # the mark at the last check; None when that check had no mark for the meeting
    new: str


def take_absence_snapshot(scraper: SiteScraper, previous: list[dict]) -> list[dict]:
    """The student's mark for every class meeting, in each course on the dashboard.

    A course that cannot be read this time keeps its marks from `previous`, so a failing page is not
    reported as the marks disappearing, nor as every absence being new when it comes back.
    """
    listing = list({c["lec_open_idx"]: c for c in courses.list_courses(scraper)}.values())
    kept = {entry["lec_open_idx"]: entry for entry in previous}
    snapshot = []
    for course in listing:
        marks = _own_marks(scraper, course, listing)
        if marks is not None:
            snapshot.append(marks)
        elif course["lec_open_idx"] in kept:
            snapshot.append(kept[course["lec_open_idx"]])
    return snapshot


def _own_marks(scraper: SiteScraper, course: dict, listing: list[dict]) -> dict | None:
    """This student's marks in one course, or None when the page could not be read or has no row for them."""
    idx = course["lec_open_idx"]
    try:
        page = courses.course_attendance(scraper, idx, listing=listing)
    except (requests.RequestException, courses.CourseNotFound) as exc:
        log.warning("Course %s: attendance not read (%s)", idx, type(exc).__name__)
        return None

    own = next((s for s in page["students"] if s["student_id"].strip().lower() == scraper.username.strip().lower()), None)
    if own is None:
        log.warning("Course %s: no journal row for %s among %d students", idx, scraper.username, len(page["students"]))
        return None

    dates = {session["number"]: session["date"] for session in page["sessions"]}
    return {
        "lec_open_idx": idx,
        "course": page["course"] or course["name"],
        "sessions": [{"number": m["session"], "date": dates.get(m["session"]), "mark": m["mark"]} for m in own["marks"]],
    }


def find_absence_changes(previous: list[dict], current: list[dict]) -> list[AbsenceChange]:
    """Meetings now marked absent that were not before, and absences the site has changed to present.

    Marks that are not absences (a meeting entered as present, a blank cell) are not reported.
    """
    before = {entry["lec_open_idx"]: {s["number"]: s["mark"] for s in entry["sessions"]} for entry in previous}
    changes = []
    for entry in current:
        old_marks = before.get(entry["lec_open_idx"], {})
        for session in entry["sessions"]:
            old, new = old_marks.get(session["number"]), session["mark"]
            if (new == ABSENT and old != ABSENT) or (old == ABSENT and new == PRESENT):
                changes.append(AbsenceChange(entry["course"], session["number"], session["date"], old, new))
    return changes


def format_absence_message(changes: list[AbsenceChange]) -> tuple[str, str]:
    """Subject and body naming each course and the meetings whose mark changed."""
    by_course: dict[str, list[AbsenceChange]] = {}
    for change in changes:
        by_course.setdefault(change.course, []).append(change)

    if len(by_course) == 1:
        subject = f"AZTUSP: attendance updated for {next(iter(by_course))}"
    else:
        subject = f"AZTUSP: attendance updated for {len(by_course)} courses"

    blocks = []
    for course, items in by_course.items():
        lines = [f"Course: {course}"]
        for change in items:
            line = f"Session {change.session}"
            if change.date:
                line += f" ({change.date})"
            line += f": {MARK_LABELS[change.new]}"
            if change.old is not None:
                line += f" (was {MARK_LABELS[change.old]})"
            lines.append(line)
        blocks.append("\n".join(lines))
    return subject, "\n\n".join(blocks)
