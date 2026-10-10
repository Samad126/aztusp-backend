from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from ...config import Settings
from ...db import Notifications, User, UserStore
from ...schemas import Detail, NotificationsIn, NotificationsOut, OkResponse
from ...scraping.client import SiteScraper
from ..deps import current_user, get_settings, get_store
from ..errors import SITE_DOWN, UNAUTHORIZED

router = APIRouter(prefix="/me/notifications", tags=["Notifications"])


@router.get(
    "",
    summary="My change notifications",
    response_model=NotificationsOut,
    responses={**UNAUTHORIZED, 404: {"model": Detail, "description": "Change notifications are off."}},
)
def read_notifications(user: User = Depends(current_user), store: UserStore = Depends(get_store)):
    """Where result changes are sent, which fields are watched, and how the last check went."""
    saved = store.get_notifications(user.id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Change notifications are off")
    return NotificationsOut(**asdict(saved))


@router.put(
    "",
    summary="Turn change notifications on or update them",
    response_model=NotificationsOut,
    responses={
        **UNAUTHORIZED,
        401: {"model": Detail, "description": "Missing or invalid token, or the site rejected the saved password (log in again)."},
        **SITE_DOWN,
        422: {"model": Detail, "description": "No email and no Telegram connected, no saved password, or invalid settings."},
    },
)
def save_notifications(
    body: NotificationsIn,
    user: User = Depends(current_user),
    store: UserStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
):
    """Turn change notifications on, or replace them.

    Needs an email, or a connected Telegram (`/me/telegram/link`), or both. The watcher signs in with the password
    saved at login (kept **encrypted**), so nothing else is sent here. If the site rejects the saved password, for
    example after a change made on the site, log in again to save the new one. Turning notifications off keeps the
    saved password; logging out keeps it too.
    """
    if body.email is None and store.get_telegram_chat(user.id) is None:
        raise HTTPException(status_code=422, detail="Add an email or connect Telegram first")
    password = store.saved_password(user.id)
    if password is None:
        raise HTTPException(status_code=422, detail="No saved password: log in again first")
    SiteScraper(settings, user.site_username).login(password)  # 401 if the site rejects it
    store.set_notifications(user.id, Notifications(email=body.email, fields=body.fields))
    return NotificationsOut(**asdict(store.get_notifications(user.id)))


@router.delete(
    "",
    summary="Turn change notifications off",
    response_model=OkResponse,
    responses=UNAUTHORIZED,
)
def delete_notifications(user: User = Depends(current_user), store: UserStore = Depends(get_store)):
    """Stop watching: deletes the email and the saved results. The saved password and the Telegram link stay."""
    store.delete_notifications(user.id)
    return {"ok": True}
