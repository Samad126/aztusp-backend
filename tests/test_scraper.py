import pytest
from bs4 import BeautifulSoup

from app.config import Settings
from app.scraping.client import LoginError, SiteScraper
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
    return Settings("k", "db", "example.com", "https://login.example.com/", "https://d.example.com/app/", "u", "p", 5.0)


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
    def __init__(self, url, text="", status_code=200):
        self.url = url
        self.text = text
        self.status_code = status_code
        self.headers = {}

    def raise_for_status(self):
        pass


LOGIN_FORM = '<form action="/sso" method="post"><input type="text" name="username"><input type="password" name="password"></form>'


def test_wrong_password_reports_incorrect_credentials(monkeypatch):
    scraper = SiteScraper(make_settings(), "user")
    # The site answers every request with its login page, so the dashboard bounces back to it.
    page = FakeResponse("https://login.example.com/", LOGIN_FORM)
    monkeypatch.setattr(scraper, "_request", lambda method, url, **kwargs: page)

    with pytest.raises(LoginError, match="^Incorrect username or password$"):
        scraper.login("wrong")


def test_log_out_visits_the_dashboard_then_sso_logout_links(monkeypatch):
    scraper = SiteScraper(make_settings(), "user")
    visited = []
    monkeypatch.setattr(scraper, "_request", lambda method, url, **kwargs: visited.append((method, url)))

    scraper.log_out()

    assert visited == [
        ("GET", "https://d.example.com/logout_proc.php"),
        ("GET", "https://login.example.com/Admin/Logout"),
    ]
