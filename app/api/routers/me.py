from fastapi import APIRouter, Depends

from ...schemas import ScrapeResult
from ...scraping.client import SiteScraper
from ...scraping.targets import TARGETS_BY_NAME
from ..deps import current_scraper
from ..errors import SITE_DOWN, UNAUTHORIZED

router = APIRouter(prefix="/me", tags=["My data"])

# URL name -> (scrape target name, summary)
PAGES = {
    "profile": ("student", "Student profile"),
    "scores": ("scores", "Scores and semester results"),
    "schedule": ("schedule", "Lecture timetable"),
    "notices": ("notices", "Notices"),
}


def add_page(resource: str, target_name: str, summary: str) -> None:
    target = TARGETS_BY_NAME[target_name]

    def read_page(scraper: SiteScraper = Depends(current_scraper)):
        return scraper.scrape(target)

    router.add_api_route(
        f"/{resource}",
        read_page,
        methods=["GET"],
        summary=summary,
        description=f"{summary}, scraped from the university site on every request.",
        response_model=ScrapeResult,
        responses={**UNAUTHORIZED, **SITE_DOWN},
        name=f"read_{resource}",
    )


for _resource, (_target, _summary) in PAGES.items():
    add_page(_resource, _target, _summary)
