import pytest
from bs4 import BeautifulSoup

from app.scraping import materials
from app.scraping.notices import FileNotFound

# The detail page is a fragment: label cells, a link cell for the files, and the text in a script (a Quill delta).
# The script is raw, so its backslashes are the ones the page sends: \n, \' and \\\" are escapes of the JS literal.
DETAIL = r"""<table class="table table-border"><tr><td><font class='title1'>Movzu</font></td><td colspan=3>&nbsp;&nbsp;Mövzu 1. Test</td></tr>
<tr><td><font class='title1'>Tarix</font></td><td>&nbsp;&nbsp;<font class="basic4">2026-10-13 00:36:40</font></td>
<td><font class='title1'>Müraciətlərin sayı</font></td><td>&nbsp;&nbsp;<font class="basic4">4</font></td></tr>
<tr><td><font class='title1'>Qoşma</font></td><td colspan=3>&nbsp;&nbsp;<a href="file_down.php?table=t_lecture_data&wr_idx=99287&wr_fno=1"><font class="basic4">Slides.pdf</font></a><br>&nbsp;&nbsp;<a href="file_down.php?table=t_lecture_data&wr_idx=99287&wr_fno=2"><font class="basic4">Diagram.jpg</font></a></td></tr>
<tr><Td colspan=4 height=250 valign="top"><div style="height: 200px" id="wr_content1"></div></td></tr></table>
<script type="text/javascript">
$(document).ready(function(){
var json = '[{"insert":"Birinci sətir\nİkinci \'sitat\' və \\\"dırnaq\\\" ç\n"}]'.replace(/\n/g,'\\n').replace(/\r/g,'\\r');
var data_delta = new Delta(JSON.parse(json));
});
</script>"""


class FakeScraper:
    class settings:
        dashboard_url = "https://d.example.com/app/"

    def __init__(self, html):
        self.html = html
        self.urls = []

    def fetch(self, url, headers=None):
        self.urls.append(url)
        return BeautifulSoup(self.html, "html.parser")


def test_material_detail():
    scraper = FakeScraper(DETAIL)
    assert materials.material_detail(scraper, "99287") == {
        "id": "99287",
        "subject": "Mövzu 1. Test",
        "author": "",
        "date": "2026-10-13 00:36:40",
        "views": "4",
        "attachments": [
            {"name": "Slides.pdf", "url": "https://d.example.com/studies/file_down.php?table=t_lecture_data&wr_idx=99287&wr_fno=1", "file_no": "1"},
            {"name": "Diagram.jpg", "url": "https://d.example.com/studies/file_down.php?table=t_lecture_data&wr_idx=99287&wr_fno=2", "file_no": "2"},
        ],
        "body": 'Birinci sətir\nİkinci \'sitat\' və "dırnaq" ç',
    }
    assert scraper.urls == ["https://d.example.com/studies/lecture_data_view.php?idx=99287"]


def test_author_is_read_when_the_site_shows_it():
    html = DETAIL.replace("<tr><td><font class='title1'>Tarix", "<tr><td><font class='title1'>Müəllif</font></td><td>İlqar</td></tr><tr><td><font class='title1'>Tarix", 1)
    assert materials.material_detail(FakeScraper(html), "99287")["author"] == "İlqar"


def test_missing_material_is_not_found():
    with pytest.raises(materials.MaterialNotFound):
        materials.material_detail(FakeScraper('<table><tr><td><font class="title1">Movzu</font></td><td></td></tr></table>'), "1")


def test_material_without_files_or_text():
    html = '<table><tr><td><font class="title1">Movzu</font></td><td>Sillabus</td></tr></table>'
    assert materials.material_detail(FakeScraper(html), "5") == {
        "id": "5",
        "subject": "Sillabus",
        "author": "",
        "date": "",
        "views": "",
        "attachments": [],
        "body": "",
    }


def test_body_is_joined_from_every_text_insert():
    script = r"""var json = '[{"insert":"A"},{"insert":{"image":"x.png"}},{"insert":"B\n"},{"insert":"C"}]'.replace(/\n/g,'\\n');"""
    assert materials._body(BeautifulSoup(f"<script>{script}</script>", "html.parser")) == "AB\nC"


def test_body_that_is_not_valid_is_empty():
    script = r"""var json = '[{"insert":"unterminated'.replace(/\n/g,'\\n');"""
    assert materials._body(BeautifulSoup(f"<script>{script}</script>", "html.parser")) == ""


def test_no_delta_script_means_no_body():
    assert materials._body(BeautifulSoup("<div>no script</div>", "html.parser")) == ""


@pytest.mark.parametrize(
    "literal, expected",
    [
        (r"plain", "plain"),
        (r"\n\t\r", "\n\t\r"),
        (r"\'quoted\' \"double\" \\back", "'quoted' \"double\" \\back"),
        (r"ç\x41", "çA"),
        (r"\q keeps the letter", "q keeps the letter"),
    ],
)
def test_js_string_escapes(literal, expected):
    assert materials._js_string(literal) == expected


def test_download_uses_the_material_table():
    class Response:
        status_code = 200
        headers = {"Content-Disposition": 'attachment; filename="Slides.pdf"'}

        def close(self):
            self.closed = True

    class Scraper(FakeScraper):
        def open_download(self, url):
            self.download_url = url
            return Response()

    scraper = Scraper("")
    response, filename = materials.open_material_file(scraper, "99287", "1")
    assert filename == "Slides.pdf"
    assert scraper.download_url == "https://d.example.com/studies/file_down.php?table=t_lecture_data&wr_idx=99287&wr_fno=1"


def test_a_missing_material_file_is_not_found():
    class Html:
        status_code = 400
        headers = {"Content-Type": "text/html; charset=UTF-8"}

        def close(self):
            self.closed = True

    class Scraper(FakeScraper):
        def open_download(self, url):
            return Html()

    with pytest.raises(FileNotFound):
        materials.open_material_file(Scraper(""), "99287", "9")
