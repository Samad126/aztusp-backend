import functools
import json
import logging
import ssl
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from requests.certs import where as default_ca_bundle
from requests.cookies import RequestsCookieJar, create_cookie

from ..config import Settings
from .cookies import share_cookies_across_subdomains
from .parsing import is_total, map_keys, parse_pairs, parse_table
from .targets import Section, Target

log = logging.getLogger(__name__)

MAX_REDIRECTS = 10
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
SKIPPED_INPUT_TYPES = {"submit", "button", "image", "reset", "file"}

# The dashboard only serves inner pages to pjax requests; a plain GET bounces to /telebe.
PJAX_HEADERS = {
    "X-PJAX": "true",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://gsap.aztu.edu.az/telebe/",
}

# The dashboard's "Çıxış" (log out) link ends its app session. The SSO sign-in is ended through its API.
DASHBOARD_LOGOUT_PATH = "/logout_proc.php"

# The sign-in page of the SSO is a web app that calls these JSON endpoints on its own host.
SSO_LOGIN_PATH = "/api/auth/login"
SSO_SELECT_SYSTEM_PATH = "/api/auth/select-system"
SSO_PLATFORM_LAUNCH_PATH = "/api/auth/platform-launch"
SSO_LOGOUT_PATH = "/api/auth/logout"
# The system the dashboard belongs to (GSAP, the master's portal). A user with a choice of systems is switched to it.
DASHBOARD_SYSTEM = "GSAP"
# The statuses the SSO answers a wrong username or password with. Other errors are site failures, so the watcher
# doesn't pause a student's checks for them.
REJECTED_STATUSES = {400, 401}

# newsso.aztu.edu.az sends its certificate without the intermediate that issued it. Browsers fetch that intermediate
# themselves, but requests can't, so it is trusted here too. The chain still has to end at a root in certifi.
# Remove this and _SsoAdapter once the university serves the full chain.
SSO_INTERMEDIATE_CA = Path(__file__).with_name("sso_intermediate.pem")


class LoginError(RuntimeError):
    pass


class BadCredentials(LoginError):
    """The site rejected the username or password."""


def dump_cookies(jar: RequestsCookieJar) -> str:
    return json.dumps(
        [
            {k: getattr(c, k) for k in ("name", "value", "domain", "path", "secure", "expires")}
            for c in jar
        ]
    )


def load_cookies(jar: RequestsCookieJar, data: str) -> None:
    for item in json.loads(data):
        jar.set_cookie(create_cookie(**item))


@functools.cache
def _sso_trust() -> ssl.SSLContext:
    """The usual roots plus SSO_INTERMEDIATE_CA. Built once and shared by every SiteScraper."""
    context = ssl.create_default_context(cafile=default_ca_bundle())
    context.load_verify_locations(cafile=str(SSO_INTERMEDIATE_CA))
    return context


class _SsoAdapter(HTTPAdapter):
    """Mounted on the SSO host only, so the extra intermediate is trusted for that host and no other."""

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = _sso_trust()
        return super().init_poolmanager(*args, **kwargs)


class SiteScraper:
    """Scrapes the site as one user. The password is only used while logging in; it is never kept.

    Session cookies are handed to on_save after every successful request so the caller can persist them.
    """

    def __init__(
        self,
        settings: Settings,
        username: str,
        cookies: str | None = None,
        on_save: Callable[[str], None] | None = None,
    ):
        self.settings = settings
        self.username = username
        self._password: str | None = None
        self._on_save = on_save
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "Mozilla/5.0 (compatible; AZTUSP/1.0)"
        self.session.mount(urljoin(settings.login_url, "/"), _SsoAdapter())

        if cookies:
            try:
                load_cookies(self.session.cookies, cookies)
            except (ValueError, TypeError, KeyError) as exc:
                log.warning("Could not restore saved cookies for %s: %s", username, exc)

        self._lock = threading.Lock()

    def login(self, password: str) -> None:
        with self._lock:
            self._password = password
            try:
                self._login()
            finally:
                self._password = None

    def scrape(self, target: Target) -> dict:
        url = urljoin(self.settings.dashboard_url, target.path)
        soup = self.fetch(url, headers=PJAX_HEADERS if target.pjax else None)

        result: dict = {"name": target.name, "url": url, "tables": {}, "pairs": {}, "fields": {}}
        for key, selector in target.tables.items():
            table = soup.select_one(selector)
            result["tables"][key] = parse_table(table) if table else []
        result["totals"] = {}
        for key, label in target.totals.items():
            rows = result["tables"].get(key, [])
            result["totals"][key] = next((row for row in rows if is_total(row, label)), None)
            result["tables"][key] = [row for row in rows if not is_total(row, label)]
        result["sections"] = {key: self._read_section(soup, section) for key, section in target.sections.items()}
        for key, selector in target.pairs.items():
            table = soup.select_one(selector)
            result["pairs"][key] = parse_pairs(table) if table else {}
        for key, selector in target.fields.items():
            element = soup.select_one(selector)
            result["fields"][key] = element.get_text(" ", strip=True) if element else None
        return map_keys(result)

    @staticmethod
    def _read_section(soup: BeautifulSoup, section: Section) -> list[dict]:
        return [
            {
                "title": title.get_text(" ", strip=True) if (title := card.select_one(section.title)) else None,
                "rows": parse_table(table) if (table := card.select_one(section.table)) else [],
            }
            for card in soup.select(section.container)
        ]

    def fetch(self, url: str, headers: dict[str, str] | None = None, password_page: bool = False) -> BeautifulSoup:
        """Fetch a page, logging in again if the saved session is missing or expired.

        `password_page`: for pages with password inputs of their own, such as the change password form.
        """
        with self._lock:
            response = self._request("GET", url, headers=headers)
            if self._looks_logged_out(response, password_page):
                raise LoginError("Site session expired, please log in again")
            self._save_cookies()
            return BeautifulSoup(response.text, "html.parser")

    def open_download(self, url: str) -> requests.Response:
        """Open a file download as a stream (caller must close it). Raises LoginError if the session is gone."""
        with self._lock:
            response = self._request("GET", url, stream=True)
            if self._looks_logged_out(response):
                response.close()
                raise LoginError("Site session expired, please log in again")
            self._save_cookies()
            return response

    def post_form(self, url: str, data: dict[str, str], files: dict | None = None) -> requests.Response:
        """Submit a form to the site and return the answer after redirects. The caller reads the answer.

        `files` makes it a multipart upload: field name -> (filename, content, content type).
        """
        with self._lock:
            response = self._request("POST", url, data=data, files=files)
            self._save_cookies()
            return response

    def post_page(self, url: str, data: dict[str, str]) -> BeautifulSoup:
        """Like fetch, for a page the site only serves to the POST its own script sends (it opens a modal with it)."""
        with self._lock:
            response = self._request("POST", url, data=data, headers=PJAX_HEADERS)
            if self._looks_logged_out(response):
                raise LoginError("Site session expired, please log in again")
            self._save_cookies()
            return BeautifulSoup(response.text, "html.parser")

    def log_out(self) -> None:
        """Log out of the dashboard app, then end the SSO sign-in through its API.

        Nothing is saved afterwards, since the caller deletes the stored session. Network errors are raised.
        """
        with self._lock:
            self._request("GET", urljoin(self.settings.dashboard_url, DASHBOARD_LOGOUT_PATH))
            self._request("POST", urljoin(self.settings.login_url, SSO_LOGOUT_PATH))

    def _login(self) -> None:
        settings = self.settings
        if self._password is None:
            raise LoginError("No password available to log in")
        self.session.cookies.clear()

        # The same calls the SSO page makes: sign in, choose the system if asked, then launch the dashboard.
        credentials = {"username": self.username, "password": self._password}
        sign_in = self._request("POST", urljoin(settings.login_url, SSO_LOGIN_PATH), json=credentials)
        if sign_in.status_code in REJECTED_STATUSES:
            raise BadCredentials("Incorrect username or password")
        sign_in.raise_for_status()
        log.info("Signed in to the SSO as %s", self.username)

        if sign_in.json().get("systemChoiceRequired"):
            self._sso_post(SSO_SELECT_SYSTEM_PATH, {"system": DASHBOARD_SYSTEM})
        launch = self._sso_post(SSO_PLATFORM_LAUNCH_PATH, {"target": DASHBOARD_SYSTEM}).json()
        self._enter_dashboard(launch)

        dashboard = self._request("GET", settings.dashboard_url)
        if self._looks_logged_out(dashboard):
            raise LoginError("The dashboard did not accept the sign-in")

        self._save_cookies()
        log.info("Logged in as %s", self.username)

    def _sso_post(self, path: str, body: dict) -> requests.Response:
        """POST JSON to the SSO's API. The sign-in was already accepted, so any failure here is raised."""
        response = self._request("POST", urljoin(self.settings.login_url, path), json=body)
        response.raise_for_status()
        return response

    def _enter_dashboard(self, launch: dict) -> None:
        """Hand the sign-in to the dashboard as the SSO page does: open the redirect, or post the assertion to it."""
        if launch.get("redirectUrl"):
            self._request("GET", urljoin(self.settings.login_url, launch["redirectUrl"]))
        else:
            assertion = {"assertion": launch["token"]}
            self._request("POST", urljoin(self.settings.login_url, launch["action"]), data=assertion)

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Like session.request, but follows redirects one hop at a time.

        The cookie jar is shared across subdomains before each hop, so a redirect
        from login.example.com to dashboard.example.com carries the session cookie.
        """
        for _ in range(MAX_REDIRECTS):
            response = self.session.request(
                method, url, timeout=self.settings.timeout, allow_redirects=False, **kwargs
            )
            share_cookies_across_subdomains(self.session.cookies, self.settings.base_domain)

            if response.status_code not in REDIRECT_STATUSES or "Location" not in response.headers:
                return response

            url = urljoin(response.url, response.headers["Location"])
            if response.status_code in (301, 302, 303):
                method, kwargs = "GET", {}

        raise requests.TooManyRedirects(f"More than {MAX_REDIRECTS} redirects, last URL: {url}")

    def _looks_logged_out(self, response: requests.Response, password_page: bool = False) -> bool:
        # The dashboard sends a signed-out visitor to the SSO host, and that host only serves the sign-in page.
        # The change password form is on that host too, so for it only a redirect to the sign-in root counts.
        if password_page:
            return _same_page(response.url, self.settings.login_url)
        return urlparse(response.url).netloc == urlparse(self.settings.login_url).netloc

    def _save_cookies(self) -> None:
        if self._on_save:
            self._on_save(dump_cookies(self.session.cookies))


def form_payload(form: Tag) -> dict[str, str]:
    """Collect every named input in the form, including hidden CSRF tokens."""
    payload = {}
    for element in form.find_all("input"):
        name = element.get("name")
        kind = (element.get("type") or "text").lower()
        if not name or kind in SKIPPED_INPUT_TYPES:
            continue
        if kind in {"checkbox", "radio"} and not element.has_attr("checked"):
            continue
        payload[name] = element.get("value", "")
    return payload


def _same_page(url_a: str, url_b: str) -> bool:
    a, b = urlparse(url_a), urlparse(url_b)
    return (a.netloc, a.path.rstrip("/")) == (b.netloc, b.path.rstrip("/"))
