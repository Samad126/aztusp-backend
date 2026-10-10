from fastapi import APIRouter, Depends, HTTPException

from ...config import Settings
from ...db import User, UserStore
from ...schemas import Detail, OkResponse, TelegramLink, TelegramStatus
from ..deps import current_user, get_settings, get_store
from ..errors import UNAUTHORIZED

router = APIRouter(prefix="/me/telegram", tags=["Notifications"])


@router.get(
    "",
    summary="Is my Telegram connected",
    response_model=TelegramStatus,
    responses=UNAUTHORIZED,
)
def read_telegram(user: User = Depends(current_user), store: UserStore = Depends(get_store)):
    return {"linked": store.get_telegram_chat(user.id) is not None}


@router.post(
    "/link",
    summary="Get a link that connects my Telegram",
    response_model=TelegramLink,
    responses={**UNAUTHORIZED, 503: {"model": Detail, "description": "Telegram is not set up on the server."}},
)
def create_link(
    user: User = Depends(current_user),
    store: UserStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
):
    """A link to the AZTUSP Telegram bot. Open it and press Start: that connects your Telegram chat to your account.

    The link works once and expires after 15 minutes. Ask for a new one if it has expired.
    """
    if not settings.telegram_bot_username:
        raise HTTPException(status_code=503, detail="Telegram is not set up on the server")
    code, expires_at = store.create_link_code(user.id)
    return {"url": f"https://t.me/{settings.telegram_bot_username}?start={code}", "expires_at": expires_at}


@router.delete(
    "",
    summary="Disconnect my Telegram",
    response_model=OkResponse,
    responses={**UNAUTHORIZED, 409: {"model": Detail, "description": "Notifications need an email first."}},
)
def delete_telegram(user: User = Depends(current_user), store: UserStore = Depends(get_store)):
    """Stop Telegram messages. If notifications are on, they need an email first, or nothing would be sent."""
    saved = store.get_notifications(user.id)
    if saved is not None and saved.email is None:
        raise HTTPException(status_code=409, detail="Add an email before disconnecting Telegram")
    store.delete_telegram_chat(user.id)
    return {"ok": True}
