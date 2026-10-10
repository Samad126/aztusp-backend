from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from ...db import User, UserStore
from ...photos import MAX_PHOTO_BYTES, sniff_image_type
from ...schemas import (
    Detail,
    NoticeDetail,
    NoticesPage,
    OkResponse,
    PasswordChangeIn,
    PasswordChangeResult,
    ProfilePage,
    SchedulePage,
    ScoresPage,
)
from ...scraping import notices, password_form
from ...scraping.client import SiteScraper
from ...scraping.targets import TARGETS_BY_NAME
from ...timetable import service as timetable
from ...timetable.config import TimetableSource
from ..deps import current_scraper, current_user, get_store, get_timetable_source
from ..errors import SITE_DOWN, UNAUTHORIZED

router = APIRouter(prefix="/me", tags=["My data"])

PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp")

# URL name -> (scrape target name, summary, response model). The schedule has its own route below.
PAGES = {
    "profile": ("student", "Student profile", ProfilePage),
    "scores": ("scores", "Scores and semester results", ScoresPage),
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


@router.get(
    "/schedule",
    summary="Lecture timetable (one block per semester)",
    response_model=SchedulePage,
    responses={**UNAUTHORIZED, **SITE_DOWN},
)
def read_schedule(
    view: timetable.ScheduleView = Query(
        "list",
        description=(
            "How the channel's timetable is laid out, with both weeks in it. `list` fills `sections`: one row per session "
            "(`day`, `time`, `week`, `course`, `type`, `room`, `teacher`). `grid` fills `grids`: one per group, a row per "
            "weekday (`monday` to `friday`) and a cell per lesson time, the cards in each cell with `type` (`lecture` or `lab`), "
            "`week` (`upper` for üst həftə, `lower` for alt həftə, `both` when every week), `course`, `teacher` and `room`. "
            "The university's own timetable is returned in `sections` in both views."
        ),
    ),
    scraper: SiteScraper = Depends(current_scraper),
    source: TimetableSource | None = Depends(get_timetable_source),
):
    """The university's timetable, scraped on every request. When the university has no lessons, the student's groups
    are looked up in the timetable PDFs posted to the schedule channel. `url` is then the channel post the timetable came
    from. Without a channel configured, this is the university's answer alone."""
    return timetable.read_timetable(scraper, source, view)


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


@router.post(
    "/password",
    summary="Change my site password",
    response_model=PasswordChangeResult,
    responses={
        **UNAUTHORIZED,
        404: {"model": Detail, "description": "The site page has no password form."},
        **SITE_DOWN,
    },
)
def change_password(
    body: PasswordChangeIn,
    scraper: SiteScraper = Depends(current_scraper),
    store: UserStore = Depends(get_store),
):
    """Submit the password change form on the university SSO site with the new password.

    `changed` is true when the site answers with its sign-in page and no change form. Otherwise `messages` holds the
    alert and error texts from the page. Passwords that do not match are rejected before the site is contacted.

    After a change, the password saved at login is replaced with the new one, so change notifications keep signing in.
    """
    result = password_form.change_password(scraper, body.password)
    if result["changed"]:
        store.save_password(scraper.user.id, body.password)
    return result


@router.put(
    "/photo",
    summary="Upload or replace my profile photo",
    response_model=OkResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": f"The image file itself, not a form. JPEG, PNG or WebP, {MAX_PHOTO_BYTES // (1024 * 1024)} MB at most.",
            "content": {content_type: {"schema": {"type": "string", "format": "binary"}} for content_type in PHOTO_TYPES},
        }
    },
    responses={
        **UNAUTHORIZED,
        413: {"model": Detail, "description": "The photo is larger than the limit."},
        415: {"model": Detail, "description": "The file is not a JPEG, PNG or WebP image."},
    },
)
async def upload_photo(
    request: Request,
    user: User = Depends(current_user),
    store: UserStore = Depends(get_store),
):
    """Send the image as the raw request body. The format is read from the file itself, so the `Content-Type` header
    does not have to be right. Uploading again replaces the photo.

    The photo is stored on this service, not on the university site. It is kept until it is replaced or deleted, and
    logging out keeps it.
    """
    # Counted while the body arrives, so an oversized upload is refused without being read to the end.
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_PHOTO_BYTES:
            raise HTTPException(status_code=413, detail=f"The photo must be {MAX_PHOTO_BYTES // (1024 * 1024)} MB or smaller")
    photo = bytes(data)

    content_type = sniff_image_type(photo)
    if content_type is None:
        raise HTTPException(status_code=415, detail="Upload a JPEG, PNG or WebP image")
    await run_in_threadpool(store.save_photo, user.id, content_type, photo)
    return {"ok": True}


@router.get(
    "/photo",
    summary="My profile photo",
    response_class=Response,
    responses={
        200: {
            "description": "The photo, as uploaded.",
            "content": {content_type: {"schema": {"type": "string", "format": "binary"}} for content_type in PHOTO_TYPES},
        },
        **UNAUTHORIZED,
        404: {"model": Detail, "description": "No profile photo has been uploaded."},
    },
)
def read_photo(user: User = Depends(current_user), store: UserStore = Depends(get_store)):
    """The photo itself. Send the `Authorization` header, as with the other endpoints, then use the response as an image
    (in the browser, for example, make a blob URL from it)."""
    photo = store.get_photo(user.id)
    if photo is None:
        raise HTTPException(status_code=404, detail="No profile photo has been uploaded")
    content_type, data = photo
    return Response(
        data,
        media_type=content_type,
        headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"},
    )


@router.delete(
    "/photo",
    summary="Delete my profile photo",
    response_model=OkResponse,
    responses=UNAUTHORIZED,
)
def delete_photo(user: User = Depends(current_user), store: UserStore = Depends(get_store)):
    """Remove the profile photo. Does nothing if there is none."""
    store.delete_photo(user.id)
    return {"ok": True}
