"""Submits the password change form on the university SSO site."""

from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .client import SiteScraper, form_payload

PASSWORD_FORM_PATH = "/Admin/UpdatePassword"
# The site's names for its two password inputs. The new password goes into both.
NEW_PASSWORD_FIELD = "Password"
CONFIRM_FIELD = "ConfirmPassword"
# Texts that explain the answer. A rejected change showed its reason in the info box, so every alert counts.
MESSAGE_SELECTOR = ".alert, .text-danger, .validation-summary-errors, .field-validation-error"


class PasswordFormNotFound(LookupError):
    pass


def change_password(scraper: SiteScraper, password: str) -> dict:
    """Submit the change form with the new password in both inputs.

    `changed` is true when the site answers with its sign-in page and no change form.
    """
    url, _, form = _load(scraper)
    names = {element.get("name") for element in form.find_all("input")}
    if not {NEW_PASSWORD_FIELD, CONFIRM_FIELD} <= names:
        raise PasswordFormNotFound(f"No {NEW_PASSWORD_FIELD} and {CONFIRM_FIELD} inputs on {url}")

    payload = form_payload(form)
    payload[NEW_PASSWORD_FIELD] = password
    payload[CONFIRM_FIELD] = password
    answer = scraper.post_form(_action(url, form), payload)
    if answer.status_code >= 500:
        answer.raise_for_status()  # the site failed; any other answer is read below

    # Not checked for a logged-out session: the site may end the session after a change, and that is part of the answer.
    # The URL is not used: the answers seen so far both ended at the site root, so the page content decides.
    soup = BeautifulSoup(answer.text, "html.parser")
    still_shown = _change_form(soup) is not None
    sign_in_shown = soup.find("input", attrs={"name": scraper.settings.username_field}) is not None
    changed = sign_in_shown and not still_shown
    return {
        "changed": changed,
        "url": answer.url,
        "messages": [] if changed else _messages(soup),
    }


def _load(scraper: SiteScraper) -> tuple[str, BeautifulSoup, Tag]:
    """Fetch the change page (with the logged-in check) and find its change form."""
    url = urljoin(scraper.settings.login_url, PASSWORD_FORM_PATH)
    soup = scraper.fetch(url, password_page=True)
    form = _change_form(soup)
    if form is None:
        raise PasswordFormNotFound(f"No password form found on {url}")
    return url, soup, form


def _change_form(soup: BeautifulSoup) -> Tag | None:
    """The form that has the confirmation input. The sign-in form does not have it."""
    return next((form for form in soup.find_all("form") if form.find("input", attrs={"name": CONFIRM_FIELD})), None)


def _action(url: str, form: Tag) -> str:
    return urljoin(url, form.get("action") or url)


def _messages(soup: BeautifulSoup) -> list[str]:
    texts = (" ".join(element.get_text(" ", strip=True).split()) for element in soup.select(MESSAGE_SELECTOR))
    return [text for text in texts if text]
