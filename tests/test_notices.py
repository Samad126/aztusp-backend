import pytest
from bs4 import BeautifulSoup

from app.scraping import notices
from app.scraping.parsing import parse_table

DETAIL = """<table border="1"><tr><td><font class="title1">Movzu</font></td><td colspan="3">  Onlayn Imtahandan Istifadə Təlimatı</td></tr>
<tr><td><font class="title1">Müəllif</font></td><td colspan="3">  Admin</td></tr>
<tr><td><font class="title1">Tərtib tarixi</font></td><td>  <font>2020-12-13 22:15:12</font></td>
<td><font class="title1">Müraciətlərin sayı</font></td><td>  <font>37430</font></td></tr>
<tr><td><font class="title1">Qoşma fayl</font></td><td colspan="3">  <a href="file_down.php?table=t_notice&amp;wr_idx=34&amp;wr_fno=1"><font>slides.pptx</font></a></td></tr>
<tr><td colspan="4" height="250"><table><tr><td><span>Hörmətli Tələbələr</span></td></tr></table></td></tr></table>"""


class FakeScraper:
    class settings:
        dashboard_url = "https://d.example.com/app/"

    def __init__(self, html):
        self.html = html
        self.urls = []

    def fetch(self, url, headers=None):
        self.urls.append(url)
        return BeautifulSoup(self.html, "html.parser")


def test_notice_detail():
    scraper = FakeScraper(DETAIL)
    assert notices.notice_detail(scraper, "34") == {
        "id": "34",
        "url": "https://d.example.com/studies/notice_view.php?wr_idx=34",
        "subject": "Onlayn Imtahandan Istifadə Təlimatı",
        "author": "Admin",
        "created_at": "2020-12-13 22:15:12",
        "views": "37430",
        "attachments": [{"name": "slides.pptx", "url": "https://d.example.com/studies/file_down.php?table=t_notice&wr_idx=34&wr_fno=1", "file_no": "1"}],
        "body": "Hörmətli Tələbələr",
    }


def test_missing_notice_is_not_found():
    with pytest.raises(notices.NoticeNotFound):
        notices.notice_detail(FakeScraper("<html><body></body></html>"), "99")


def test_list_rows_expose_the_notice_id():
    table = BeautifulSoup(
        '<table><thead><tr><th>Nömrə</th><th>Movzu</th></tr></thead><tr id="op34"><td>1</td><td onclick="send_view(\'34\');">Hello</td></tr>'
        "<tr><td>2</td><td>No detail</td></tr></table>",
        "html.parser",
    ).table
    assert parse_table(table) == [{"Nömrə": "1", "Movzu": "Hello", "id": "34"}, {"Nömrə": "2", "Movzu": "No detail"}]


def test_download_filename_is_repaired_and_header_is_safe():
    mangled = 'attachment; filename="tÉ\x99lÉ\x99bÉ\x99 magistr.pptx"'
    assert notices._filename(mangled) == "tələbə magistr.pptx"
    header = notices.content_disposition("tələbə magistr.pptx")
    assert header.startswith('attachment; filename="t_l_b_ magistr.pptx"') and "filename*=UTF-8''t%C9%99l%C9%99b%C9%99%20magistr.pptx" in header


def test_html_answer_means_no_such_file():
    class Html:
        status_code = 200
        headers = {"Content-Type": "text/html; charset=UTF-8"}

        def close(self):
            self.closed = True

    class Scraper(FakeScraper):
        def open_download(self, url):
            return Html()

    with pytest.raises(notices.FileNotFound):
        notices.open_notice_file(Scraper(""), "34", "2")
