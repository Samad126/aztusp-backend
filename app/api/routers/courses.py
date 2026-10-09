from fastapi import APIRouter, Depends, Path

from ...schemas import Course, CoursePage, Detail, LecturePlan
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


PAGE_SUMMARIES = {
    "notices": "Course notices",
    "board": "Course board",
    "materials": "Course materials",
    "tasks": "Course tasks",
    "scores": "Course scores",
    "attendance": "Course attendance",
}


def add_course_page(name: str, summary: str) -> None:
    def read_course_page(
        lec_open_idx: str = Path(description="Course id from `GET /courses`."),
        scraper: SiteScraper = Depends(current_scraper),
    ):
        return courses.course_page(scraper, lec_open_idx, name)

    router.add_api_route(
        f"/{{lec_open_idx}}/{name}",
        read_course_page,
        methods=["GET"],
        summary=summary,
        description=f"{summary}, scraped from the university site on every request. Tables are returned as records.",
        response_model=CoursePage,
        responses={
            **UNAUTHORIZED,
            404: {"model": Detail, "description": "No course with that `lec_open_idx`."},
            **SITE_DOWN,
        },
        name=f"course_{name}",
    )


for _name, _summary in PAGE_SUMMARIES.items():
    add_course_page(_name, _summary)
