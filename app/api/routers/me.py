from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from ...schemas import Detail, NoticeDetail, NoticesPage, ProfilePage, SchedulePage, ScoresPage
from ...scraping import notices
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


NOTICE_ID = Path(pattern=r"^\d{1,12}$", description="Notice `id` from `GET /me/notices`.")


@router.get(
    "/notices/{notice_id}",
    summary="Read one notice",
    response_model=NoticeDetail,
    responses={**UNAUTHORIZED, 404: {"model": Detail, "description": "No notice with that id."}, **SITE_DOWN},
)
def read_notice(
    request: Request,
    notice_id: str = NOTICE_ID,
    scraper: SiteScraper = Depends(current_scraper),
):
    """The notice as the site shows it when a row of the notices list is opened: subject, author, date, views, files and text."""
    notice = notices.notice_detail(scraper, notice_id)
    for attachment in notice["attachments"]:
        if attachment["file_no"].isdigit():
            attachment["download"] = request.app.url_path_for(
                "download_notice_file", notice_id=notice_id, file_no=attachment["file_no"]
            )
    return notice


@router.get(
    "/notices/{notice_id}/files/{file_no}",
    summary="Download a notice attachment (Qoşma fayl)",
    name="download_notice_file",
    response_class=StreamingResponse,
    responses={
        200: {"description": "The file.", "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}},
        **UNAUTHORIZED,
        404: {"model": Detail, "description": "The notice has no such file."},
        **SITE_DOWN,
    },
)
def download_notice_file(
    notice_id: str = NOTICE_ID,
    file_no: str = Path(pattern=r"^[1-9]\d{0,2}$", description="`file_no` from the notice's `attachments`."),
    scraper: SiteScraper = Depends(current_scraper),
):
    """Streams the attachment through the API, so the frontend does not need the site session."""
    upstream, filename = notices.open_notice_file(scraper, notice_id, file_no)
    headers = {"Content-Disposition": notices.content_disposition(filename)}
    if "Content-Length" in upstream.headers:
        headers["Content-Length"] = upstream.headers["Content-Length"]
    return StreamingResponse(
        upstream.iter_content(chunk_size=65536),
        media_type=upstream.headers.get("Content-Type", "application/octet-stream"),
        headers=headers,
        background=BackgroundTask(upstream.close),
    )
