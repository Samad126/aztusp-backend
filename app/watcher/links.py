"""The links at the end of a notification: the page in AZTUSP, and the same page on the university site.

Telegram and email show the addresses as they are, so the student can tap them.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CourseLink:
    course_id: str  # the course's lec_open_idx, which its page in AZTUSP is keyed by
    site_url: str | None  # the same tab on the university site; None for a snapshot saved before the links were kept


def course_links(frontend_url: str, link: CourseLink, tab: str) -> str:
    """The course's tab in AZTUSP (`tab` is its name there: attendance, tasks or scores), then the university's."""
    lines = [f"Open in AZTUSP: {frontend_url}/courses/{link.course_id}?tab={tab}"]
    if link.site_url:
        lines.append(f"Open on the university site: {link.site_url}")
    return "\n".join(lines)


def grades_links(frontend_url: str, site_url: str) -> str:
    """The grades page in AZTUSP, then the scores page on the university site."""
    return f"Open in AZTUSP: {frontend_url}/grades\nOpen on the university site: {site_url}"
