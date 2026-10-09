import logging

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from requests import RequestException

from ..schemas import Detail
from ..scraping.client import LoginError
from ..scraping.courses import CourseNotFound
from ..scraping.notices import FileNotFound, NoticeNotFound

UNAUTHORIZED = {
    401: {"model": Detail, "description": "Missing or invalid token, or the site session expired (log in again)."}
}
SITE_DOWN = {502: {"model": Detail, "description": "The university site could not be reached or failed."}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(LoginError)
    async def login_error(request: Request, exc: LoginError):
        return JSONResponse({"detail": str(exc)}, status_code=401)

    @app.exception_handler(RequestException)
    async def request_error(request: Request, exc: RequestException):
        return JSONResponse({"detail": f"Site request failed: {exc}"}, status_code=502)

    @app.exception_handler(CourseNotFound)
    async def course_not_found(request: Request, exc: CourseNotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(NoticeNotFound)
    async def notice_not_found(request: Request, exc: NoticeNotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(FileNotFound)
    async def file_not_found(request: Request, exc: FileNotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(psycopg.OperationalError)
    async def database_unavailable(request: Request, exc: psycopg.OperationalError):
        logging.getLogger(__name__).error("Database unavailable: %s", exc)
        return JSONResponse({"detail": "Database unavailable, try again shortly"}, status_code=503)
