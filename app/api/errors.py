import logging

import psycopg
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from requests import RequestException

from ..schemas import Detail
from ..scraping.client import LoginError
from ..scraping.courses import CourseNotFound
from ..scraping.notices import FileNotFound, NoticeNotFound
from ..scraping.password_form import PasswordFormNotFound

UNAUTHORIZED = {
    401: {"model": Detail, "description": "Missing, invalid or expired token, or the site session expired (log in again)."}
}
SITE_DOWN = {502: {"model": Detail, "description": "The university site could not be reached or failed."}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # FastAPI repeats each submitted value under "input", so a wrong login or password change would send the
        # password back. The location and message are enough to fix the request.
        errors = [{key: value for key, value in error.items() if key != "input"} for error in exc.errors()]
        return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)

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

    @app.exception_handler(PasswordFormNotFound)
    async def password_form_not_found(request: Request, exc: PasswordFormNotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(psycopg.OperationalError)
    async def database_unavailable(request: Request, exc: psycopg.OperationalError):
        logging.getLogger(__name__).error("Database unavailable: %s", exc)
        return JSONResponse({"detail": "Database unavailable, try again shortly"}, status_code=503)
