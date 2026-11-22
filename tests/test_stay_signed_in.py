"""A student who stays signed in gets a new site session from the saved password; others are sent to the sign-in page."""

import pytest

from app.config import Settings
from app.scraping import client as site
from app.scraping.client import BadCredentials, LoginError, SiteScraper

SETTINGS = Settings("k", "db", "example.com", "https://l.example.com/", "https://d.example.com/", 5.0)


class FakeResponse:
    def __init__(self, url, text="<p>ok</p>"):
        self.url = url
        self.text = text

    def close(self):
        pass


def scraper_with(saved, responses, logins):
    scraper = SiteScraper(SETTINGS, "u", saved_password=saved)
    queue = list(responses)
    scraper._request = lambda method, url, **kw: queue.pop(0)

    def fake_login():
        logins.append(scraper._password)

    scraper._login = fake_login
    return scraper


OUT = FakeResponse("https://l.example.com/")
IN = FakeResponse("https://d.example.com/page")


def test_an_expired_session_is_replaced_with_the_saved_password():
    logins = []
    scraper = scraper_with(lambda: "secret", [OUT, IN], logins)
    assert scraper.fetch("https://d.example.com/page").text == "ok"
    assert logins == ["secret"]
    assert scraper._password is None


def test_without_a_saved_password_the_session_expiry_is_raised():
    logins = []
    scraper = scraper_with(None, [OUT], logins)
    with pytest.raises(LoginError):
        scraper.fetch("https://d.example.com/page")
    assert logins == []


def test_a_password_that_no_longer_works_asks_for_a_login():
    scraper = scraper_with(lambda: "old", [OUT], [])

    def rejected():
        raise BadCredentials("no")

    scraper._login = rejected
    with pytest.raises(LoginError):
        scraper.fetch("https://d.example.com/page")


def test_it_gives_up_when_the_new_session_is_logged_out_too():
    logins = []
    scraper = scraper_with(lambda: "secret", [OUT, OUT], logins)
    with pytest.raises(LoginError):
        scraper.fetch("https://d.example.com/page")
    assert logins == ["secret"]


STUB = FakeResponse(
    "https://d.example.com/page",
    "<script language=\"javascript\"> if (parent) { parent.window.open('', '_self'); parent.window.close(); } "
    "else { window.open('', '_self'); self.close(); } </script>",
)


def test_the_dashboards_close_window_stub_counts_as_logged_out():
    # The dashboard answers a dead session with 200 and this page instead of a redirect to the sign-in page.
    scraper = SiteScraper(SETTINGS, "u")
    assert scraper._looks_logged_out(STUB)


def test_a_long_page_that_mentions_the_script_is_not_the_stub():
    scraper = SiteScraper(SETTINGS, "u")
    page = FakeResponse("https://d.example.com/page", "<p>x</p>" * 1000 + "window.open('', '_self')")
    assert not scraper._looks_logged_out(page)


def test_the_stub_is_replaced_with_a_new_session_for_students_who_stay_signed_in():
    logins = []
    scraper = scraper_with(lambda: "secret", [STUB, IN], logins)
    assert scraper.fetch("https://d.example.com/page").text == "ok"
    assert logins == ["secret"]


def test_the_stub_asks_for_a_login_otherwise_instead_of_returning_an_empty_page():
    scraper = scraper_with(None, [STUB], [])
    with pytest.raises(LoginError):
        scraper.fetch("https://d.example.com/page")
