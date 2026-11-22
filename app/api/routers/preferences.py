from fastapi import APIRouter, Depends

from ...db import User, UserStore
from ...schemas import Preferences
from ..deps import current_user, get_store
from ..errors import UNAUTHORIZED

router = APIRouter(prefix="/me/preferences", tags=["My data"])


@router.get("", summary="My preferences", response_model=Preferences, responses=UNAUTHORIZED)
def read_preferences(user: User = Depends(current_user), store: UserStore = Depends(get_store)):
    """The start page and the stay signed in switch. They are kept on the server, so every device of the student shows the same."""
    start_page, stay_signed_in = store.get_preferences(user.id)
    return Preferences(start_page=start_page, stay_signed_in=stay_signed_in)


@router.put("", summary="Save my preferences", response_model=Preferences, responses=UNAUTHORIZED)
def save_preferences(body: Preferences, user: User = Depends(current_user), store: UserStore = Depends(get_store)):
    """Replace the preferences.

    With `stay_signed_in` on, the API token is renewed each time it is used (it does not run out until the student signs out of that device) and the site session
    is opened again with the **encrypted** password saved at login when it runs out, so the student does not type it again.
    It is off by default. Signing out still ends the session; the next login keeps the setting.
    """
    store.set_preferences(user.id, body.start_page, body.stay_signed_in)
    return body
