import email
import urllib.request
from http.cookiejar import CookieJar

from app.scraping.cookies import share_cookies_across_subdomains


class FakeResponse:
    def __init__(self, set_cookie: str):
        self._headers = email.message_from_string(f"Set-Cookie: {set_cookie}\n\n")

    def info(self):
        return self._headers


def store_cookie(jar: CookieJar, url: str, set_cookie: str) -> None:
    jar.extract_cookies(FakeResponse(set_cookie), urllib.request.Request(url))


def cookie_header_for(jar: CookieJar, url: str) -> str | None:
    request = urllib.request.Request(url)
    jar.add_cookie_header(request)
    return request.get_header("Cookie")


def test_login_subdomain_cookie_is_sent_to_dashboard_after_sharing():
    jar = CookieJar()
    store_cookie(jar, "https://login.example.com/login", "sid=abc123; Path=/; Secure")

    assert cookie_header_for(jar, "https://dashboard.example.com/") is None

    share_cookies_across_subdomains(jar, "example.com")

    assert cookie_header_for(jar, "https://dashboard.example.com/") == "sid=abc123"
    assert cookie_header_for(jar, "https://login.example.com/login") == "sid=abc123"


def test_cookies_for_unrelated_domains_are_not_shared():
    jar = CookieJar()
    store_cookie(jar, "https://tracker.other.com/", "t=1; Path=/")

    share_cookies_across_subdomains(jar, "example.com")

    assert {cookie.domain for cookie in jar} == {"tracker.other.com"}
