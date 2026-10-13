"""The course tabs the watcher reads besides attendance: individual work (Qiymətləndirmə) and course scores (Cari müvəffəqiyyət).

Each tab is read for every course on the dashboard, the same way as the attendance marks (see attendance.py). A course seen
for the first time is recorded without announcing what it already holds, so turning a watch on starts from a baseline.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import requests

from ..scraping import courses
from ..scraping.client import SiteScraper
from .links import CourseLink, course_links

log = logging.getLogger(__name__)

UNREADABLE = (requests.RequestException, courses.CourseNotFound)

# The columns of an individual work that are compared and shown, by the key the course tab gives them.
WORK_LABELS = {"task_type": "Type", "evaluation": "Evaluation", "subject": "Topic", "start_date": "Start", "end_date": "End"}

# Reads one course: its entry for the snapshot, or None when its page cannot be read.
ReadCourse = Callable[[SiteScraper, dict, list[dict]], dict | None]


def take_course_snapshot(scraper: SiteScraper, previous: list[dict], read: ReadCourse) -> list[dict]:
    """One entry per course on the dashboard that could be read, as `read` returns it.

    A course that cannot be read this time keeps its entry from `previous`, so a failing page is not reported as its
    contents going away, nor as all of them being new when it comes back.
    """
    listing = list({c["lec_open_idx"]: c for c in courses.list_courses(scraper)}.values())
    kept = {entry["lec_open_idx"]: entry for entry in previous}
    snapshot = []
    for course in listing:
        entry = read(scraper, course, listing)
        if entry is not None:
            snapshot.append(entry)
        elif course["lec_open_idx"] in kept:
            snapshot.append(kept[course["lec_open_idx"]])
    return snapshot


def _by_course(changes: list) -> dict[str, list]:
    by_course: dict[str, list] = {}
    for change in changes:
        by_course.setdefault(change.course, []).append(change)
    return by_course


def _subject(noun: str, by_course: dict) -> str:
    if len(by_course) == 1:
        return f"AZTUSP: {noun} updated for {next(iter(by_course))}"
    return f"AZTUSP: {noun} updated for {len(by_course)} courses"


# Individual work

def take_work_snapshot(scraper: SiteScraper, previous: list[dict]) -> list[dict]:
    """Every individual work listed in each course, as the tab shows it."""
    return take_course_snapshot(scraper, previous, _read_work)


def _read_work(scraper: SiteScraper, course: dict, listing: list[dict]) -> dict | None:
    idx = course["lec_open_idx"]
    try:
        page = courses.course_items(scraper, idx, "tasks", listing=listing)
    except UNREADABLE as exc:
        log.warning("Course %s: individual work not read (%s)", idx, type(exc).__name__)
        return None
    return {"lec_open_idx": idx, "course": page["course"] or course["name"], "works": page["items"], "site_url": page["url"]}


@dataclass(frozen=True)
class WorkChange:
    course: str
    work: dict  # the work as it is now
    changed: dict[str, tuple[str | None, str]] | None  # column -> (was, now) for a work that changed; None when it is new
    link: CourseLink


def find_work_changes(previous: list[dict], current: list[dict]) -> list[WorkChange]:
    """Works that are new, and works whose shown columns changed, since the last check. A course seen for the first time is skipped."""
    before = {entry["lec_open_idx"]: {_work_key(w): w for w in entry["works"]} for entry in previous}
    changes = []
    for entry in current:
        known = before.get(entry["lec_open_idx"])
        if known is None:
            continue
        link = CourseLink(entry["lec_open_idx"], entry.get("site_url"))
        for work in entry["works"]:
            old = known.get(_work_key(work))
            if old is None:
                changes.append(WorkChange(entry["course"], work, None, link))
                continue
            changed = {key: (old.get(key) or None, work[key]) for key in WORK_LABELS if work.get(key) and work[key] != old.get(key)}
            if changed:
                changes.append(WorkChange(entry["course"], work, changed, link))
    return changes


def _work_key(work: dict) -> str:
    """Tells one work from another across checks: its detail id when the row has one, else its content.

    The row number is left out of the content, because adding a work above it renumbers the rest.
    """
    if work.get("id"):
        return f"id:{work['id']}"
    content = {key: value for key, value in work.items() if key not in ("number", "link")}
    return "content:" + json.dumps(content, sort_keys=True, ensure_ascii=False)


def format_work_message(changes: list[WorkChange], frontend_url: str) -> tuple[str, str]:
    """Subject and body naming each course and the works that are new or changed, with links to the tasks tab."""
    by_course = _by_course(changes)
    blocks = []
    for course, items in by_course.items():
        lines = [f"Course: {course}"]
        for change in items:
            if change.changed is None:
                lines.append("New work")
                lines += [f"  {label}: {change.work[key]}" for key, label in WORK_LABELS.items() if change.work.get(key)]
            else:
                lines.append("Changed work")
                lines += [f"  {WORK_LABELS[key]}: {now}" + (f" (was {was})" if was else "") for key, (was, now) in change.changed.items()]
        lines.append(course_links(frontend_url, items[0].link, "tasks"))
        blocks.append("\n".join(lines))
    return _subject("individual work", by_course), "\n\n".join(blocks)


# Course scores

def take_score_snapshot(scraper: SiteScraper, previous: list[dict]) -> list[dict]:
    """The score of each component and the total, for each course."""
    return take_course_snapshot(scraper, previous, _read_scores)


def _read_scores(scraper: SiteScraper, course: dict, listing: list[dict]) -> dict | None:
    idx = course["lec_open_idx"]
    try:
        page = courses.course_scores(scraper, idx, listing=listing)
    except UNREADABLE as exc:
        log.warning("Course %s: course scores not read (%s)", idx, type(exc).__name__)
        return None
    return {
        "lec_open_idx": idx,
        "course": page["course"] or course["name"],
        "components": page["components"],
        "total": page["total"],
        "site_url": page["url"],
    }


@dataclass(frozen=True)
class ScoreChange:
    course: str
    name: str  # the component's name, or "Total"
    maximum: str | None
    old: str | None  # None when the score was not shown at the last check
    new: str
    link: CourseLink


def find_score_changes(previous: list[dict], current: list[dict]) -> list[ScoreChange]:
    """Scores that are shown now and differ from the last check, for each component and the total.

    A score that disappears is not reported. A course seen for the first time is skipped.
    """
    before = {entry["lec_open_idx"]: entry for entry in previous}
    changes = []
    for entry in current:
        known = before.get(entry["lec_open_idx"])
        if known is None:
            continue
        link = CourseLink(entry["lec_open_idx"], entry.get("site_url"))
        old_scores = {component["name"]: component["score"] for component in known["components"]}
        for component in entry["components"]:
            old, new = old_scores.get(component["name"]), component["score"]
            if new is not None and new != old:
                changes.append(ScoreChange(entry["course"], component["name"], component["max"], old, new, link))
        if entry["total"] is not None and entry["total"] != known["total"]:
            changes.append(ScoreChange(entry["course"], "Total", None, known["total"], entry["total"], link))
    return changes


def format_score_message(changes: list[ScoreChange], frontend_url: str) -> tuple[str, str]:
    """Subject and body naming each course and the scores that changed, with links to the scores tab."""
    by_course = _by_course(changes)
    blocks = []
    for course, items in by_course.items():
        lines = [f"Course: {course}"]
        for change in items:
            name = f"{change.name} (of {change.maximum})" if change.maximum else change.name
            line = f"{name}: {change.new}"
            if change.old is not None:
                line += f" (was {change.old})"
            lines.append(line)
        lines.append(course_links(frontend_url, items[0].link, "scores"))
        blocks.append("\n".join(lines))
    return _subject("course scores", by_course), "\n\n".join(blocks)
