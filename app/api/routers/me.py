from fastapi import APIRouter, Depends

from ...schemas import NoticesPage, ProfilePage, SchedulePage, ScoresPage
from ...scraping.client import SiteScraper
from ...scraping.targets import TARGETS_BY_NAME
from ..deps import current_scraper
from ..errors import SITE_DOWN, UNAUTHORIZED

router = APIRouter(prefix="/me", tags=["My data"])

# URL name -> (scrape target name, summary, response model)
PAGES = {
    "profile": ("student", "Student profile", ProfilePage),
    "scores": ("scores", "Scores and semester results", ScoresPage),
    "schedule": ("schedule", "Lecture timetable (one block per semester)", SchedulePage),
    "notices": ("notices", "Notices", NoticesPage),
}


def add_page(resource: str, target_name: str, summary: str, model: type) -> None:
    target = TARGETS_BY_NAME[target_name]

    def read_page(scraper: SiteScraper = Depends(current_scraper)):
        return scraper.scrape(target)

    router.add_api_route(
        f"/{resource}",
        read_page,
        methods=["GET"],
        summary=summary,
        description=f"{summary}, scraped from the university site on every request.",
        response_model=model,
        responses={**UNAUTHORIZED, **SITE_DOWN},
        name=f"read_{resource}",
    )


for _resource, (_target, _summary, _model) in PAGES.items():
    add_page(_resource, _target, _summary, _model)
