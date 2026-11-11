"""The grades table: each course's quizzes, midterm, individual work and final exam, put together from the course pages."""

import logging
import re

import requests

from . import courses
from .client import SiteScraper
from .targets import TARGETS_BY_NAME

log = logging.getLogger(__name__)

UNREADABLE = (requests.RequestException, courses.CourseNotFound)

# Columns in the order the university grades them.
COLUMNS = ("quiz1", "midterm", "quiz2", "individual_work", "final")

QUIZ = re.compile(r"quiz\s*[-_#№]?\s*(\d)|kviz\s*[-_#№]?\s*(\d)", re.I)
MIDTERM = re.compile(r"midterm|mid-term|kollokvium|aralıq|araliq", re.I)


def _number(value: str | None) -> float | None:
    try:
        return float((value or "").replace(",", ".").strip())
    except ValueError:
        return None


def _fmt(value: float | None) -> str | None:
    if value is None:
        return None
    return str(int(value)) if value == int(value) else f"{value:.1f}"


def classify(subject: str, quizzes_seen: int) -> str | None:
    """`quiz1`, `quiz2` or `midterm` from a seminar task's topic; a quiz with no number takes the next free quiz slot."""
    if match := QUIZ.search(subject):
        number = match.group(1) or match.group(2)
        return "quiz1" if number == "1" else "quiz2" if number == "2" else None
    if MIDTERM.search(subject):
        return "midterm"
    if re.search(r"quiz|kviz", subject, re.I):
        return "quiz1" if quizzes_seen == 0 else "quiz2"
    return None


def _final_scores(scraper: SiteScraper) -> dict[str, str]:
    """Course name (lower case) -> final score, from the scores page; the later semester wins."""
    page = scraper.scrape(TARGETS_BY_NAME["scores"])
    finals: dict[str, str] = {}
    for block in page["sections"].get("semester_courses", []):
        for row in block["rows"]:
            if row.get("final_score"):
                finals[row["course"].strip().lower()] = row["final_score"]
    return finals


def _course_row(scraper: SiteScraper, course: dict, listing: list[dict], finals: dict[str, str]) -> dict:
    idx = course["lec_open_idx"]
    name = re.sub(r"\s*\[[^\]]*\]\s*$", "", course["name"]).strip()
    row: dict = {"lec_open_idx": idx, "code": "", "course": name, **{key: None for key in COLUMNS}, "average": None, "max": {}}

    try:
        scores = courses.course_scores(scraper, idx, listing=listing)
        row["code"] = scores["params"].get("lecture_code", "")
        row["course"] = scores["course"] or name
        for component in scores["components"]:
            if re.search(r"sərbəst|serbest|müstəqil", component["name"], re.I):
                row["individual_work"] = component["score"]
                row["max"]["individual_work"] = component["max"]
    except UNREADABLE as exc:
        log.warning("Course %s: scores not read for grades (%s)", idx, type(exc).__name__)

    try:
        listed = [item for item in courses.course_items(scraper, idx, "tasks", listing=listing)["items"] if item.get("id")]
        points = courses.task_scores(scraper, idx, [item["id"] for item in listed])
        quizzes_seen = 0
        for item in listed:
            kind = (item.get("task_type") or "").lower()
            if "məşğələ" not in kind and "seminar" not in kind:
                continue
            slot = classify(item.get("subject", ""), quizzes_seen)
            if slot is None or row[slot] is not None:
                continue
            quizzes_seen += slot.startswith("quiz")
            row[slot] = points.get(item["id"])
    except UNREADABLE as exc:
        log.warning("Course %s: tasks not read for grades (%s)", idx, type(exc).__name__)

    row["final"] = finals.get(row["course"].strip().lower()) or finals.get(name.lower())
    parts = [_number(row[key]) for key in COLUMNS]
    row["average"] = _fmt(sum(p for p in parts if p is not None)) if any(p is not None for p in parts) else None
    return row


def current_grades(scraper: SiteScraper) -> dict:
    listing = courses.list_courses(scraper)
    try:
        finals = _final_scores(scraper)
    except UNREADABLE:
        finals = {}
    rows = [_course_row(scraper, course, listing, finals) for course in listing]
    return {"courses": rows}
