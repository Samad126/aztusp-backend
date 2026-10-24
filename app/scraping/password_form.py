"""Changes the site password through the "Parol redaktə et" modal of the student's profile page (student_view.php)."""

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .client import PJAX_HEADERS, BadCredentials, SiteScraper

# What the modal's confirm button posts to (relative to /telebe/info/), with the names of its three inputs.
CHANGE_PATH = "/telebe/info/student_pwd_ajax.php"
MAX_MESSAGE = 300


def change_password(scraper: SiteScraper, current: str, new: str) -> dict:
    """Send the modal's three passwords (the new one is typed twice there, so it is sent twice).

    The page shows "Parol dəyişdirildi" whatever the answer says, so the answer is not trusted: `changed` is true when
    the site now signs in with the new password. When it does not, `messages` holds the answer's text, if there is any.
    """
    answer = scraper.post_form(
        urljoin(scraper.settings.dashboard_url, CHANGE_PATH),
        {"old_pwd": current, "new_pwd": new, "new_pwd_confirm": new},
        headers=PJAX_HEADERS,
    )
    answer.raise_for_status()
    if _signs_in(scraper, new):
        return {"changed": True, "url": answer.url, "messages": []}
    return {"changed": False, "url": answer.url, "messages": _messages(answer.text)}


def _signs_in(scraper: SiteScraper, password: str) -> bool:
    """Whether the site accepts the password, tried on a session of its own so the caller's is left alone."""
    try:
        SiteScraper(scraper.settings, scraper.username).login(password)
    except BadCredentials:
        return False
    return True


def _messages(body: str) -> list[str]:
    text = " ".join(BeautifulSoup(body, "html.parser").get_text(" ", strip=True).split())
    return [text[:MAX_MESSAGE]] if text else []
