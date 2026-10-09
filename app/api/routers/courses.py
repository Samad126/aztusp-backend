from fastapi import APIRouter, Depends, Path

from ...schemas import Course, CourseAttendance, CourseItems, CourseScores, Detail, LecturePlan
from ...scraping import courses
from ...scraping.client import SiteScraper
from ..deps import current_scraper
from ..errors import SITE_DOWN, UNAUTHORIZED

router = APIRouter(prefix="/courses", tags=["Courses"])


@router.get(
    "",
    summary="List my courses",
    response_model=list[Course],
    responses={**UNAUTHORIZED, **SITE_DOWN},
)
def list_courses(scraper: SiteScraper = Depends(current_scraper)):
    """Courses linked from the dashboard, with the ids used by the other course endpoints."""
    return courses.list_courses(scraper)


@router.get(
    "/{lec_open_idx}/plan",
    summary="Get a course's lecture plan",
    response_model=LecturePlan,
    response_model_exclude_unset=True,
    responses={
        **UNAUTHORIZED,
        404: {"model": Detail, "description": "No course with that `lec_open_idx`."},
        **SITE_DOWN,
    },
)
def course_plan(
    lec_open_idx: str = Path(description="Course id from `GET /courses`."),
    scraper: SiteScraper = Depends(current_scraper),
):
    """Weekly lecture plan, textbooks and course info for one course."""
    return courses.lecture_plan(scraper, lec_open_idx)


COURSE_NOT_FOUND = {404: {"model": Detail, "description": "No course with that `lec_open_idx`."}}
LEC_OPEN_IDX = Path(description="Course id from `GET /courses`.")

LIST_PAGES = {
    "notices": "Course notices (Bildiriş)",
    "board": "Course forum (Forum)",
    "materials": "Course materials (Didaktik materiallar)",
    "tasks": "Course assessments and assignments (Qiymətləndirmə)",
}


def add_list_page(name: str, summary: str) -> None:
    def read_list_page(lec_open_idx: str = LEC_OPEN_IDX, scraper: SiteScraper = Depends(current_scraper)):
        return courses.course_items(scraper, lec_open_idx, name)

    router.add_api_route(
        f"/{{lec_open_idx}}/{name}",
        read_list_page,
        methods=["GET"],
        summary=summary,
        description=f"{summary}, scraped from the university site on every request. `items` is empty when nothing was posted.",
        response_model=CourseItems,
        responses={**UNAUTHORIZED, **COURSE_NOT_FOUND, **SITE_DOWN},
        name=f"course_{name}",
    )


for _name, _summary in LIST_PAGES.items():
    add_list_page(_name, _summary)


@router.get(
    "/{lec_open_idx}/scores",
    summary="Course scores (Cari müvəffəqiyyət)",
    response_model=CourseScores,
    responses={**UNAUTHORIZED, **COURSE_NOT_FOUND, **SITE_DOWN},
)
def course_scores(lec_open_idx: str = LEC_OPEN_IDX, scraper: SiteScraper = Depends(current_scraper)):
    """Current points per component (e.g. seminars, independent work) and the total."""
    return courses.course_scores(scraper, lec_open_idx)


@router.get(
    "/{lec_open_idx}/attendance",
    summary="Course attendance (Davamiyyət)",
    response_model=CourseAttendance,
    responses={**UNAUTHORIZED, **COURSE_NOT_FOUND, **SITE_DOWN},
)
def course_attendance(lec_open_idx: str = LEC_OPEN_IDX, scraper: SiteScraper = Depends(current_scraper)):
    """Attendance journal: one entry per class meeting, with the attendance score and percentage."""
    return courses.course_attendance(scraper, lec_open_idx)
