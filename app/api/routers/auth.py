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
    later without asking for it again, and `POST /me/password` keeps it up to date. Logging in again adds a token for
    that device, so other devices stay signed in, and replaces the saved password. The token lasts 1 day.
    """
    scraper = SiteScraper(settings, credentials.username)
    scraper.login(credentials.password)
    token = store.upsert_login(credentials.username, dump_cookies(scraper.session.cookies), credentials.password)
    return {"token": token}


@router.post(
    "/logout",
    summary="Log out of this device",
    response_model=OkResponse,
    responses=UNAUTHORIZED,
)
def logout(scraper: SiteScraper = Depends(current_scraper), store: UserStore = Depends(get_store)):
    """End this device's API session: its token stops working. Other devices stay signed in.

    When this was the last signed-in device, the university dashboard and SSO are logged out too and the site session
    cookies are dropped; otherwise the site session is left alone, since the other devices share it.
    The saved password, notification settings, Telegram link and profile photo are kept, so change notifications keep running.
    The session ends even when the university site cannot be reached. A new login is needed afterwards.
    """
    if not store.has_other_sessions(scraper.user):
        try:
            scraper.log_out()
        except requests.RequestException as exc:
            log.warning("Could not log %s out of the university site: %s", scraper.user.site_username, exc)
    store.end_session(scraper.user)
    return {"ok": True}
