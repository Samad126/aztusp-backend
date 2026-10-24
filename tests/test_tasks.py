import pytest
from bs4 import BeautifulSoup

from app.scraping import tasks

# The modal the site builds for a task: label cells, the text in a script (a Quill delta), the files, and the upload form.
DETAIL = r"""<script>
var json = '[{"insert":"Read the file.\n"},{"attributes":{"bold":true},"insert":"Link: "},{"attributes":{"link":"https://drive.example.com/f"},"insert":"https://drive.example.com/f"},{"insert":"\n"}]'.replace(/\n/g, '\\n').replace(/\r/g, '\\r');
</script>
<table border="1"><form name="eeaa">
<tr><td><font class='title1'>Movzu <!-- x --></font></td><td colspan="3">&nbsp;Quiz 1</td></tr>
<tr><td><font class='title1'>Başlanğıc</font></td><td>&nbsp;20261026 ~ 20261030</td><td><font class='title1'>Verilmə əlaməti</font></td><td>&nbsp;qebul olunmur</td></tr>
<tr><td><font class='title1'>Sərbəst işin növü</font></td><td>&nbsp;Məşğələ</td><td><font class='title1'>İşin verilmə vəziyyəti</font></td><td>&nbsp;0/30</td></tr>
<tr><td><font class='title1'>Qiymətləndirmə</font></td><td colspan="3">&nbsp;Bəli</td></tr>
<tr><td><font class='title1'>Qoşma1</font></td><td colspan="3">&nbsp;<a href="file_down.php?table=t_lecture_task&wr_idx=86617&wr_fno=1">Quiz 1.pdf</a></td></tr>
<tr><td><font class='title1'>Qoşma2</font></td><td colspan="3">&nbsp;<a href="file_down.php?table=t_lecture_task&wr_idx=86617&wr_fno=2"></a></td></tr>
</form></table>
<form name="eeaa_new" method="post" action="lecture_task_present_proc.php"><table><tr><td>İşin verilmə</td><td><input type="file" name="o_file1"><input type="file" name="o_file2"></td></tr></table></form>"""

CLOSED = DETAIL[: DETAIL.index('<form name="eeaa_new"')]


class FakeScraper:
    class settings:
        dashboard_url = "https://d.example.com/app/"

    def __init__(self, html=DETAIL):
        self.html = html
        self.posts = []
        self.uploads = []

    def fetch(self, url, headers=None):
        return BeautifulSoup('<a href="/x?lecture_code=C1&sem_code=S2">tab</a>', "html.parser")

    def post_page(self, url, data):
        self.posts.append((url, data))
        return BeautifulSoup(self.html, "html.parser")

    def post_form(self, url, data, files=None):
        self.uploads.append((url, data, files))

        class Response:
            def raise_for_status(self):
                pass

        return Response()


@pytest.fixture(autouse=True)
def course(monkeypatch):
    monkeypatch.setattr(tasks, "course_params", lambda scraper, idx: {"lec_open_idx": idx, "lecture_code": "C1", "sem_code": "S2"})


def test_task_detail():
    scraper = FakeScraper()
    assert tasks.task_detail(scraper, "23855", "86617") == {
        "id": "86617",
        "subject": "Quiz 1",
        "task_type": "Məşğələ",
        "start_date": "20261026",
        "end_date": "20261030",
        "submission_method": "qebul olunmur",
        "score": "0",
        "max_score": "30",
        "evaluation": "Bəli",
        "body": "Read the file.\nLink: https://drive.example.com/f",
        "links": ["https://drive.example.com/f"],
        "attachments": [{"name": "Quiz 1.pdf", "url": "https://d.example.com/studies/file_down.php?table=t_lecture_task&wr_idx=86617&wr_fno=1", "file_no": "1"}],
        "can_submit": True,
    }
    url, data = scraper.posts[0]
    assert url == "https://d.example.com/studies/lecture_task_view.php?t_idx=86617"
    assert data["lec_open_idx"] == "23855" and data["lecture_code"] == "C1" and data["sem_code"] == "S2"


def test_unknown_task_is_not_found():
    with pytest.raises(tasks.TaskNotFound):
        tasks.task_detail(FakeScraper("<script>window.close()</script>"), "23855", "1")


def test_submit_sends_the_modals_form_with_the_files():
    scraper = FakeScraper()
    tasks.submit_task(scraper, "23855", "86617", [("a.pdf", b"A", "application/pdf"), ("b.txt", b"B", "text/plain")])
    url, data, files = scraper.uploads[0]
    assert url == "https://d.example.com/studies/lecture_task_present_proc.php"
    assert data == {"idx": "", "t_idx": "86617", "mode": "add", "sem_code": "S2", "lec_open_idx": "23855"}
    assert files == {"o_file1": ("a.pdf", b"A", "application/pdf"), "o_file2": ("b.txt", b"B", "text/plain")}


def test_submit_is_refused_without_an_upload_form_or_with_wrong_file_count():
    closed = FakeScraper(CLOSED)
    with pytest.raises(tasks.TaskClosed):
        tasks.submit_task(closed, "23855", "86617", [("a.pdf", b"A", "application/pdf")])
    open_ = FakeScraper()
    for files in ([], [("a", b"", "x")] * 3):
        with pytest.raises(ValueError):
            tasks.submit_task(open_, "23855", "86617", files)
    assert closed.uploads == open_.uploads == []
