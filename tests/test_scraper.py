import pytest
import requests
from bs4 import BeautifulSoup

from app.config import Settings
from app.scraping.client import BadCredentials, LoginError, SiteScraper, _sso_trust, _SsoAdapter
from app.scraping.parsing import column_keys, parse_pairs, parse_table
from app.scraping.targets import Section, Target

SCORES_HTML = """
<table id="op_list"><tr><th>Ad</th><th>Soyadı</th></tr>
<tr><td>Ali</td><td>Aliyev</td></tr><tr><td>Toplam</td><td>2</td></tr></table>
<table class="info"><tr><td>Telefon</td><td>123</td></tr><tr><td>x</td><td>y</td><td>z</td></tr></table>
<div class="card"><h5>Fall</h5><table><thead><tr><th>Kredit</th><th></th></tr></thead>
<tbody><tr><td>5</td><td>A</td></tr></tbody></table></div>
<div class="card"><table><tr><th>Kurs</th></tr><tr><td>2</td></tr></table></div>
<span id="f">hello world</span>
"""

TARGET = Target(
    name="t",
    path="/p",
    tables={"main": "table#op_list", "missing": "table#nope"},
    pairs={"info": "table.info", "none": "table.nope"},
    totals={"main": "Toplam"},
    sections={"cards": Section(container="div.card", title="h5", table="table")},
    fields={"f": "#f", "g": "#nope"},
    pjax=False,
)


class StubScraper(SiteScraper):
    def fetch(self, url, headers=None):
        self.fetched = (url, headers)
        return BeautifulSoup(SCORES_HTML, "html.parser")


def make_settings():
    return Settings("k", "db", "example.com", "https://login.example.com/", "https://d.example.com/app/", 5.0)


def test_scrape_result_shape():
    scraper = StubScraper(make_settings(), "user")
    assert scraper.scrape(TARGET) == {
        "name": "t",
        "url": "https://d.example.com/p",
        "tables": {"main": [{"Ad": "Ali", "last_name": "Aliyev"}], "missing": []},
        "pairs": {"info": {"phone": "123"}, "none": {}},
        "totals": {"main": {"Ad": "Toplam", "last_name": "2"}},
        "sections": {
            "cards": [
                {"title": "Fall", "rows": [{"credits": "5", "column_2": "A"}]},
                {"title": None, "rows": [{"year_of_study": "2"}]},
            ]
        },
        "fields": {"f": "hello world", "g": None},
    }
    assert scraper.fetched == ("https://d.example.com/p", None)


def test_parse_table_without_thead_uses_first_th_row():
    table = BeautifulSoup("<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>", "html.parser").table
    assert parse_table(table) == [{"A": "1", "B": "2"}]


def test_parse_pairs_skips_rows_that_are_not_two_columns():
    table = BeautifulSoup("<table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table>", "html.parser").table
    assert parse_pairs(table) == {"a": "b"}


def testcolumn_keys_dedupes_and_falls_back():
    assert column_keys(["A", "", "A"], 3) == ["A", "column_2", "column_3"]
    assert column_keys(["A"], 2) == ["column_1", "column_2"]


def test_parse_table_reads_header_cells_outside_a_row():
    table = BeautifulSoup(
        '<table><td>Dərs</td><td>Bazar ertəsi</td><tr><td>1</td><td>Math</td></tr></table>', "html.parser"
    ).table
    assert parse_table(table) == [{"Dərs": "1", "Bazar ertəsi": "Math"}]


class FakeResponse:
    def __init__(self, url, text="", status_code=200, body=None):
        self.url = url
        self.text = text
        self.status_code = status_code
        self.headers = {}
        self.body = body

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} for {self.url}")


SSO = "https://login.example.com/"
DASHBOARD = "https://d.example.com/app/"


def stub_requests(monkeypatch, scraper, answers):
    """Answers each (method, url) with a FakeResponse. Returns the calls made, in order: (method, url, json, data)."""
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs.get("json"), kwargs.get("data")))
        return answers[(method, url)]

    monkeypatch.setattr(scraper, "_request", request)
    return calls


def sign_in_answers(system_choice=False):
    """The SSO's answers to a sign-in that works, and the dashboard's answer once it is handed the assertion."""
    answers = {
        ("POST", SSO + "api/auth/login"): FakeResponse(
            SSO + "api/auth/login", body={"user": {"selectedSystem": "GSAP"}, "systemChoiceRequired": system_choice}
        ),
        ("POST", SSO + "api/auth/platform-launch"): FakeResponse(
            SSO + "api/auth/platform-launch", body={"action": "https://d.example.com/sso/consume.php", "token": "tok"}
        ),
        ("POST", "https://d.example.com/sso/consume.php"): FakeResponse("https://d.example.com/telebe/"),
        ("GET", DASHBOARD): FakeResponse(DASHBOARD),
    }
    if system_choice:
        select = SSO + "api/auth/select-system"
        answers[("POST", select)] = FakeResponse(select, body={"user": {"selectedSystem": "GSAP"}})
    return answers


def test_wrong_password_reports_incorrect_credentials(monkeypatch):
    scraper = SiteScraper(make_settings(), "user")
    rejected = FakeResponse(SSO + "api/auth/login", status_code=401)
    stub_requests(monkeypatch, scraper, {("POST", SSO + "api/auth/login"): rejected})

    with pytest.raises(BadCredentials, match="^Incorrect username or password$"):
        scraper.login("wrong")


def test_other_refusals_are_site_failures_not_wrong_passwords(monkeypatch):
    scraper = SiteScraper(make_settings(), "user")
    blocked = FakeResponse(SSO + "api/auth/login", status_code=403)
    stub_requests(monkeypatch, scraper, {("POST", SSO + "api/auth/login"): blocked})

    with pytest.raises(requests.HTTPError):
        scraper.login("secret")


def test_sign_in_calls_the_sso_api_then_hands_the_assertion_to_the_dashboard(monkeypatch):
    scraper = SiteScraper(make_settings(), "user")
    calls = stub_requests(monkeypatch, scraper, sign_in_answers())

    scraper.login("secret")

    assert calls == [
        ("POST", SSO + "api/auth/login", {"username": "user", "password": "secret"}, None),
        ("POST", SSO + "api/auth/platform-launch", {"target": "GSAP"}, None),
        ("POST", "https://d.example.com/sso/consume.php", None, {"assertion": "tok"}),
        ("GET", DASHBOARD, None, None),
    ]


def test_a_choice_of_systems_is_answered_with_the_dashboard_system(monkeypatch):
    scraper = SiteScraper(make_settings(), "user")
    calls = stub_requests(monkeypatch, scraper, sign_in_answers(system_choice=True))

    scraper.login("secret")

    assert calls[1] == ("POST", SSO + "api/auth/select-system", {"system": "GSAP"}, None)
    assert calls[2] == ("POST", SSO + "api/auth/platform-launch", {"target": "GSAP"}, None)


def test_a_dashboard_that_sends_the_visitor_to_the_sso_is_not_a_wrong_password(monkeypatch):
    scraper = SiteScraper(make_settings(), "user")
    answers = sign_in_answers()
    answers[("GET", DASHBOARD)] = FakeResponse(SSO)
    stub_requests(monkeypatch, scraper, answers)

    with pytest.raises(LoginError, match="^The dashboard did not accept the sign-in$") as raised:
        scraper.login("secret")
    assert not isinstance(raised.value, BadCredentials)


def test_log_out_visits_the_dashboard_then_ends_the_sso_sign_in(monkeypatch):
    scraper = SiteScraper(make_settings(), "user")
    visited = []
    monkeypatch.setattr(scraper, "_request", lambda method, url, **kwargs: visited.append((method, url)))

    scraper.log_out()

    assert visited == [
        ("GET", "https://d.example.com/logout_proc.php"),
        ("POST", "https://login.example.com/api/auth/logout"),
    ]


def test_only_the_sso_host_uses_the_intermediate_trust_store():
    scraper = SiteScraper(make_settings(), "user")

    assert isinstance(scraper.session.get_adapter(SSO + "api/auth/login"), _SsoAdapter)
    assert not isinstance(scraper.session.get_adapter(DASHBOARD), _SsoAdapter)


def test_sso_trust_store_adds_the_intermediate_to_the_usual_roots():
    subjects = [{key: value for rdn in cert["subject"] for key, value in rdn} for cert in _sso_trust().get_ca_certs()]

    assert any(subject.get("commonName") == "Sectigo Public Server Authentication CA DV R36" for subject in subjects)
