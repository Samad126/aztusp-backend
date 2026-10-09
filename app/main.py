import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import JSONResponse
from pydantic import BaseModel
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

app = FastAPI(title="AZTUSP")


@app.exception_handler(LoginError)
async def login_error_handler(request, exc: LoginError):
    return JSONResponse({"detail": str(exc)}, status_code=401)


@app.exception_handler(RequestException)
async def request_error_handler(request, exc: RequestException):
    return JSONResponse({"detail": f"Site request failed: {exc}"}, status_code=502)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/targets")
def list_targets():
    return {"targets": list(targets_by_name)}


class Credentials(BaseModel):
    username: str
    password: str


@app.post("/auth/login")
def login(credentials: Credentials):
    """Log in with the user's own site account. Returns an API token for the other endpoints.

    The password is used once to sign in and is not stored; only the resulting session
    cookies are kept (encrypted).
    """
    scraper = SiteScraper(settings, credentials.username)
    scraper.login(credentials.password)
    token = store.upsert_login(credentials.username, dump_cookies(scraper.session.cookies))
    return {"token": token}


bearer = HTTPBearer(auto_error=False)  # shows the Authorize button in /docs


def current_scraper(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> SiteScraper:
    user = store.get_by_token(credentials.credentials) if credentials else None
    if user is None:
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    scraper = SiteScraper(
        settings, user.site_username, user.cookies, on_save=lambda cookies: store.save_cookies(user.id, cookies)
    )
    scraper.user = user
    return scraper


@app.post("/auth/logout")
def logout(scraper: SiteScraper = Depends(current_scraper)):
    store.delete(scraper.user.id)
    return {"ok": True}


@app.get("/scrape")
def scrape_all(scraper: SiteScraper = Depends(current_scraper)):
    return [scraper.scrape(target) for target in TARGETS]


@app.get("/scrape/{name}")
def scrape_one(name: str, scraper: SiteScraper = Depends(current_scraper)):
    target = targets_by_name.get(name)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Unknown target '{name}'")
    return scraper.scrape(target)


@app.exception_handler(courses.CourseNotFound)
async def course_not_found_handler(request, exc: courses.CourseNotFound):
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.get("/courses")
def list_courses(scraper: SiteScraper = Depends(current_scraper)):
    return courses.list_courses(scraper)


@app.get("/courses/{lec_open_idx}/plan")
def course_plan(lec_open_idx: str, scraper: SiteScraper = Depends(current_scraper)):
    return courses.lecture_plan(scraper, lec_open_idx)
