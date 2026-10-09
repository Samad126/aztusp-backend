from fastapi import APIRouter, Depends

from ...config import Settings
from ...db import UserStore
from ...schemas import Credentials, Detail, OkResponse, TokenResponse
from ...scraping.client import SiteScraper, dump_cookies
from ..deps import current_scraper, get_settings, get_store
from ..errors import SITE_DOWN, UNAUTHORIZED

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

    The password is used once and is **not stored**; only the resulting session cookies are
    kept, encrypted. Logging in again replaces the previous token.
    """
    scraper = SiteScraper(settings, credentials.username)
    scraper.login(credentials.password)
    token = store.upsert_login(credentials.username, dump_cookies(scraper.session.cookies))
    return {"token": token}


@router.post(
    "/logout",
    summary="Log out and delete stored data",
    response_model=OkResponse,
    responses=UNAUTHORIZED,
)
def logout(scraper: SiteScraper = Depends(current_scraper), store: UserStore = Depends(get_store)):
    """Delete the caller's token and stored session cookies. A new login is needed afterwards."""
    store.delete(scraper.user.id)
    return {"ok": True}
