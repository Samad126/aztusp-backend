import logging

import requests
from fastapi import APIRouter, Depends

from ...config import Settings
from ...db import UserStore
from ...schemas import Credentials, Detail, OkResponse, TokenResponse
from ...scraping.client import SiteScraper, dump_cookies
from ..deps import current_scraper, get_settings, get_store
from ..errors import SITE_DOWN, UNAUTHORIZED

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/login",
    summary="Log in with a site account",
    response_model=TokenResponse,
    responses={
        401: {"model": Detail, "description": "The site rejected the username or password."},
        **SITE_DOWN,
    },
)
def login(
    credentials: Credentials,
    settings: Settings = Depends(get_settings),
    store: UserStore = Depends(get_store),
):
    """Sign in to the university site with the user's own account and return an API token.

    The session cookies and the password are kept **encrypted**. The password lets change notifications sign in
    later without asking for it again, and `POST /me/password` keeps it up to date. Logging in again replaces the
    previous token and the saved password. The token lasts 1 day.
    """
    scraper = SiteScraper(settings, credentials.username)
    scraper.login(credentials.password)
    token = store.upsert_login(credentials.username, dump_cookies(scraper.session.cookies), credentials.password)
    return {"token": token}


@router.post(
    "/logout",
    summary="Log out of the university site and end the API session",
    response_model=OkResponse,
    responses=UNAUTHORIZED,
)
def logout(scraper: SiteScraper = Depends(current_scraper), store: UserStore = Depends(get_store)):
    """Log out of the university dashboard and SSO too, then end the API session: the token and the session cookies stop working.

    The saved password, notification settings, Telegram link and profile photo are kept, so change notifications keep running.
    The session ends even when the university site cannot be reached. A new login is needed afterwards.
    """
    try:
        scraper.log_out()
    except requests.RequestException as exc:
        log.warning("Could not log %s out of the university site: %s", scraper.user.site_username, exc)
    store.end_session(scraper.user.id)
    return {"ok": True}
