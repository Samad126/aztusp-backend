"""Pick the watched fields out of the scores page and find what changed since the last check."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Change:
    semester: str | None
    course: str
    field: str
    old: str | None
    new: str


def take_snapshot(scores: dict, fields: tuple[str, ...]) -> list[dict]:
    """Every course on the scores page with its watched fields. Blank cells become None."""
    snapshot = []
    for block in scores["sections"].get("semester_courses", []):
        for row in block["rows"]:
            if course := row.get("course"):
                values = {field: row.get(field) or None for field in fields}
                snapshot.append({"semester": block["title"], "course": course, "values": values})
    return snapshot


def find_changes(previous: list[dict], current: list[dict], fields: tuple[str, ...]) -> list[Change]:
    """Watched values that now show a result which is new or different. A result that disappears is not reported."""
    before = {(entry["semester"], entry["course"]): entry["values"] for entry in previous}
    changes = []
    for entry in current:
        old_values = before.get((entry["semester"], entry["course"]), {})
        for field in fields:
            new = entry["values"][field]
            old = old_values.get(field)
            if new is not None and new != old:
                changes.append(Change(entry["semester"], entry["course"], field, old, new))
    return changes
