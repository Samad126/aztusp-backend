import logging

from fastapi import Depends, FastAPI, HTTPException, Path
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from requests import RequestException

from . import courses
from .config import Settings
from .db import UserStore
from .scraper import LoginError, SiteScraper, dump_cookies
from .targets import TARGETS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = Settings.from_env()
store = UserStore(settings.database_url, settings.secret_key)
targets_by_name = {target.name: target for target in TARGETS}

DESCRIPTION = """
Lets each student sign in with **their own** university account and read their data
(profile, scores, timetable, notices, courses and lecture plans) as JSON.

## How to use

1. `POST /auth/login` with your site username and password. The response contains an API **token**.
2. Click **Authorize** (top right) and paste the token, or send `Authorization: Bearer <token>`.
3. Call any endpoint under *Scraping* and *Courses*.

## Privacy

* Your site password is used once to sign in and is **never stored**.
* Only the resulting site session cookies are kept, **encrypted** in the database.
* Your API token is stored as a SHA-256 hash, so it cannot be recovered. Logging in again issues a new token and invalidates the old one.
* If the site session expires, scraping endpoints answer `401` and you need to log in again.
"""

TAGS = [
    {"name": "Auth", "description": "Sign in with a site account and manage the API token."},
    {"name": "Scraping", "description": "Pages of the student dashboard, parsed into JSON."},
    {"name": "Courses", "description": "Courses linked from the dashboard and their lecture plans."},
    {"name": "System", "description": "Service status and metadata."},
]

app = FastAPI(
    title="AZTUSP Backend",
    summary="Per-user scraping API for the university student portal.",
    description=DESCRIPTION,
    version="1.0.0",
    license_info={"name": "MIT", "identifier": "MIT"},
    openapi_tags=TAGS,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Detail(BaseModel):
    detail: str = Field(description="Human-readable error message.")


class Credentials(BaseModel):
    username: str = Field(description="Site username (the same one used on the student portal).", examples=["M0000000000"])
    password: str = Field(description="Site password. Used once to sign in; never stored.")


class TokenResponse(BaseModel):
    token: str = Field(description="API token. Send it as `Authorization: Bearer <token>`. Shown only once.")


class OkResponse(BaseModel):
    ok: bool = True


class TargetsResponse(BaseModel):
    targets: list[str] = Field(description="Names accepted by `GET /scrape/{name}`.", examples=[["student", "scores"]])


class Section(BaseModel):
    title: str | None = Field(description="Heading of the block, e.g. the semester name.")
    rows: list[dict[str, str]]


class ScrapeResult(BaseModel):
    name: str = Field(description="Target name.")
    url: str = Field(description="Page that was scraped.")
    tables: dict[str, list[dict[str, str]]] = Field(
        description="Tables parsed into records, keyed by table name. Column names are translated to English where known."
    )
    pairs: dict[str, dict[str, str]] = Field(description="Two-column label/value tables, keyed by table name.")
    totals: dict[str, dict[str, str] | None] = Field(
        description="Total row split out of a table (`null` if the table has none)."
    )
    sections: dict[str, list[Section]] = Field(description="Repeating titled tables, e.g. one per semester.")
    fields: dict[str, str | None] = Field(description="Single text values picked out of the page.")


class Course(BaseModel):
    lec_open_idx: str = Field(description="Course id; use it with `/courses/{lec_open_idx}/plan`.")
    sem_code: str | None = Field(description="Semester code the course belongs to.")
    name: str
    path: str = Field(description="Dashboard path of the course page.")


class PlanBlock(BaseModel):
    title: str
    rows: list[dict[str, str]] | None = Field(default=None, description="Present when the block is a table.")
    text: str | None = Field(default=None, description="Present when the block is plain text.")


class LecturePlan(BaseModel):
    params: dict[str, str] = Field(description="Ids that identify the course on the site.")
    course: str | None
    semester: str | None
    info: dict[str, str] | None = Field(description="Professor, department, credits, hours and weeks.")
    blocks: list[PlanBlock] = Field(description="Sections of the plan, in page order.")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

UNAUTHORIZED = {
    401: {"model": Detail, "description": "Missing or invalid token, or the site session expired (log in again)."}
}
SITE_DOWN = {502: {"model": Detail, "description": "The university site could not be reached or failed."}}


@app.exception_handler(LoginError)
async def login_error_handler(request, exc: LoginError):
    return JSONResponse({"detail": str(exc)}, status_code=401)


@app.exception_handler(RequestException)
async def request_error_handler(request, exc: RequestException):
    return JSONResponse({"detail": f"Site request failed: {exc}"}, status_code=502)


@app.exception_handler(courses.CourseNotFound)
async def course_not_found_handler(request, exc: courses.CourseNotFound):
    return JSONResponse({"detail": str(exc)}, status_code=404)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

bearer = HTTPBearer(
    auto_error=False,
    description="Token returned by `POST /auth/login`. Paste it without the `Bearer ` prefix.",
)


def current_scraper(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> SiteScraper:
    user = store.get_by_token(credentials.credentials) if credentials else None
    if user is None:
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    scraper = SiteScraper(
        settings, user.site_username, user.cookies, on_save=lambda cookies: store.save_cookies(user.id, cookies)
    )
    scraper.user = user
    return scraper


@app.post(
    "/auth/login",
    tags=["Auth"],
    summary="Log in with a site account",
    response_model=TokenResponse,
    responses={
        401: {"model": Detail, "description": "The site rejected the username or password."},
        **SITE_DOWN,
    },
)
def login(credentials: Credentials):
    """Sign in to the university site with the user's own account and return an API token.

    The password is used once and is **not stored**; only the resulting session cookies are
    kept, encrypted. Logging in again replaces the previous token.
    """
    scraper = SiteScraper(settings, credentials.username)
    scraper.login(credentials.password)
    token = store.upsert_login(credentials.username, dump_cookies(scraper.session.cookies))
    return {"token": token}


@app.post(
    "/auth/logout",
    tags=["Auth"],
    summary="Log out and delete stored data",
    response_model=OkResponse,
    responses=UNAUTHORIZED,
)
def logout(scraper: SiteScraper = Depends(current_scraper)):
    """Delete the caller's token and stored session cookies. A new login is needed afterwards."""
    store.delete(scraper.user.id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


@app.get("/health", tags=["System"], summary="Health check", response_model=OkResponse)
def health():
    """Returns `{"ok": true}` when the service is running. No authentication needed."""
    return {"ok": True}


@app.get("/targets", tags=["System"], summary="List scrape targets", response_model=TargetsResponse)
def list_targets():
    """Names of the dashboard pages that `GET /scrape/{name}` can read. No authentication needed."""
    return {"targets": list(targets_by_name)}


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------


@app.get(
    "/scrape",
    tags=["Scraping"],
    summary="Scrape every target",
    response_model=list[ScrapeResult],
    responses={**UNAUTHORIZED, **SITE_DOWN},
)
def scrape_all(scraper: SiteScraper = Depends(current_scraper)):
    """Scrape all targets listed by `/targets` for the logged-in user. Slower than a single target."""
    return [scraper.scrape(target) for target in TARGETS]


@app.get(
    "/scrape/{name}",
    tags=["Scraping"],
    summary="Scrape one target",
    response_model=ScrapeResult,
    responses={
        **UNAUTHORIZED,
        404: {"model": Detail, "description": "Unknown target name."},
        **SITE_DOWN,
    },
)
def scrape_one(
    name: str = Path(description="Target name from `GET /targets`.", examples=["scores"]),
    scraper: SiteScraper = Depends(current_scraper),
):
    """Scrape a single dashboard page (student info, scores, schedule or notices) for the logged-in user."""
    target = targets_by_name.get(name)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Unknown target '{name}'")
    return scraper.scrape(target)


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------


@app.get(
    "/courses",
    tags=["Courses"],
    summary="List courses",
    response_model=list[Course],
    responses={**UNAUTHORIZED, **SITE_DOWN},
)
def list_courses(scraper: SiteScraper = Depends(current_scraper)):
    """Courses linked from the dashboard home page, with the ids needed by the course endpoints."""
    return courses.list_courses(scraper)


@app.get(
    "/courses/{lec_open_idx}/plan",
    tags=["Courses"],
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
