import json
import logging
import threading
from typing import Callable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from requests.cookies import RequestsCookieJar, create_cookie

from .config import Settings
from .cookies import share_cookies_across_subdomains
from .targets import FIELD_MAP, Target

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


class LoginError(RuntimeError):
    pass


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
        self.session.headers["User-Agent"] = "Mozilla/5.0 (compatible; UserHelperScraper/1.0)"

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
            is_total = lambda row: next(iter(row.values()), None) == label
            result["totals"][key] = next((row for row in rows if is_total(row)), None)
            result["tables"][key] = [row for row in rows if not is_total(row)]
        result["sections"] = {
            key: [
                {
                    "title": title.get_text(" ", strip=True) if (title := card.select_one(section.title)) else None,
                    "rows": parse_table(table) if (table := card.select_one(section.table)) else [],
                }
                for card in soup.select(section.container)
            ]
            for key, section in target.sections.items()
        }
        for key, selector in target.pairs.items():
            table = soup.select_one(selector)
            result["pairs"][key] = parse_pairs(table) if table else {}
        for key, selector in target.fields.items():
            element = soup.select_one(selector)
            result["fields"][key] = element.get_text(" ", strip=True) if element else None
        return _map_keys(result)

    def fetch(self, url: str, headers: dict[str, str] | None = None) -> BeautifulSoup:
        """Fetch a page, logging in again if the saved session is missing or expired."""
        with self._lock:
            response = self._request("GET", url, headers=headers)
            if self._looks_logged_out(response):
                raise LoginError("Site session expired, please log in again")
            self._save_cookies()
            return BeautifulSoup(response.text, "html.parser")

    def _login(self) -> None:
        settings = self.settings
        if self._password is None:
            raise LoginError("No password available to log in")
        self.session.cookies.clear()

        login_page = self._request("GET", settings.login_url)
        login_page.raise_for_status()

        form = _find_login_form(login_page.text)
        if form is None:
            raise LoginError(f"No password form found on {login_page.url}")

        payload = _form_payload(form)
        payload[settings.username_field] = self.username
        payload[settings.password_field] = self._password

        action = urljoin(login_page.url, form.get("action") or login_page.url)
        method = (form.get("method") or "post").upper()
        if method == "GET":
            result = self._request("GET", action, params=payload)
        else:
            result = self._request("POST", action, data=payload)
        log.info("Login form submitted, status %s", result.status_code)

        self._enter_dashboard_app(result)

        dashboard = self._request("GET", settings.dashboard_url)
        if self._looks_logged_out(dashboard):
            raise LoginError("Login failed: dashboard redirected back to the login page")

        self._save_cookies()
        log.info("Logged in as %s", self.username)

    def _enter_dashboard_app(self, landing: requests.Response) -> None:
        """Follow the SSO page's token link into the dashboard app.

        The dashboard host doesn't accept the SSO cookies directly; the page the SSO
        lands on links to it with a one-time token that starts the app's own session.
        """
        dashboard_host = urlparse(self.settings.dashboard_url).netloc
        if urlparse(landing.url).netloc == dashboard_host:
            return
        if landing.status_code in REDIRECT_STATUSES or not landing.text:
            return

        for anchor in BeautifulSoup(landing.text, "html.parser").find_all("a", href=True):
            link = urljoin(landing.url, anchor["href"])
            if urlparse(link).netloc == dashboard_host:
                log.info("Entering dashboard app via %s", urlparse(link).path)
                self._request("GET", link)
                return
        log.warning("No link to %s found on %s", dashboard_host, landing.url)

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
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

    def _looks_logged_out(self, response: requests.Response) -> bool:
        if _same_page(response.url, self.settings.login_url):
            return True
        # A password input only means "logged out" on the login host; dashboard pages
        # can contain unrelated ones (e.g. the exam password field on the student page).
        on_login_host = urlparse(response.url).netloc == urlparse(self.settings.login_url).netloc
        return on_login_host and _find_login_form(response.text) is not None

    def _save_cookies(self) -> None:
        if self._on_save:
            self._on_save(dump_cookies(self.session.cookies))


def parse_table(table: Tag) -> list[dict[str, str]]:
    """Turn an HTML <table> into a list of {column name: cell text} records."""
    thead = table.find("thead")
    # Some pages put <th> straight into <thead> without a <tr>.
    header_row = (thead.find("tr") or thead) if thead else None
    rows = table.select("tbody tr") or [
        row for row in table.find_all("tr") if row.find_parent("thead") is None
    ]
    if header_row is None and rows and rows[0].find("th"):
        header_row, rows = rows[0], rows[1:]

    headers = (
        [cell.get_text(" ", strip=True) for cell in header_row.find_all(["th", "td"], recursive=False)]
        if header_row
        else []
    )

    records = []
    for row in rows:
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"], recursive=False)]
        if not cells:
            continue
        keys = _column_keys(headers, len(cells))
        records.append(dict(zip(keys, cells)))
    return records


def _column_keys(headers: list[str], width: int) -> list[str]:
    """Use the header names, falling back to column_N for blank or repeated ones."""
    if len(headers) != width:
        return [f"column_{i}" for i in range(1, width + 1)]
    seen: set[str] = set()
    keys = []
    for i, name in enumerate(headers, start=1):
        key = name if name and name not in seen else f"column_{i}"
        seen.add(key)
        keys.append(key)
    return keys


def _map_keys(result: dict) -> dict:
    """Rename site labels to the English keys in FIELD_MAP; unknown labels are kept as-is."""

    def rename(row):
        return {FIELD_MAP.get(clean, clean): value for label, value in row.items() if (clean := " ".join(label.split()))}

    result["tables"] = {k: [rename(r) for r in rows] for k, rows in result["tables"].items()}
    result["pairs"] = {k: rename(row) for k, row in result["pairs"].items()}
    result["totals"] = {k: rename(row) if row else row for k, row in result["totals"].items()}
    result["sections"] = {
        k: [{**sec, "rows": [rename(r) for r in sec["rows"]]} for sec in secs]
        for k, secs in result["sections"].items()
    }
    return result


def parse_pairs(table: Tag) -> dict[str, str]:
    """Turn a two-column label/value <table> into {label: value}."""
    pairs = {}
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) == 2:
            pairs[cells[0].get_text(" ", strip=True)] = cells[1].get_text(" ", strip=True)
    return pairs


def _find_login_form(html: str) -> Tag | None:
    password_input = BeautifulSoup(html, "html.parser").find("input", attrs={"type": "password"})
    return password_input.find_parent("form") if password_input else None


def _form_payload(form: Tag) -> dict[str, str]:
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
