from functools import lru_cache

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import Settings
from ..db import User, UserStore
from ..scraping.client import SiteScraper

bearer = HTTPBearer(
    auto_error=False,
    description="Token returned by `POST /auth/login`. Paste it without the `Bearer ` prefix.",
)


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()


@lru_cache
def get_store() -> UserStore:
    settings = get_settings()
    return UserStore(settings.database_url, settings.secret_key)


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    store: UserStore = Depends(get_store),
) -> User:
    user = store.get_by_token(credentials.credentials) if credentials else None
    if user is None:
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    return user


def current_scraper(
    user: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
    store: UserStore = Depends(get_store),
) -> SiteScraper:
    scraper = SiteScraper(
        settings, user.site_username, user.cookies, on_save=lambda cookies: store.save_cookies(user.id, cookies)
    )
    scraper.user = user
    return scraper
