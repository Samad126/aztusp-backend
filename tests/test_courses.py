from bs4 import BeautifulSoup

from app.scraping import courses

HOME = '<a href="/studies/index.php?lec_open_idx=7&sem_cd=S1&lecture_name=Math">x</a><a href="/other">y</a>'
COURSE = '<a href="/x?lecture_code=MA1&sem_code=S2">tab</a>'
PLAN = """
<td class="list_title1">Dərsin adı : Math</td><td>Fall</td>
<span class="main_title2">Books</span><table><tr><td>Müəllif</td><td>Ad</td></tr><tr><td>Bob</td><td>Book</td></tr></table>
<span class="main_title2">Goals</span><table><tr><td>Learn</td></tr></table>
"""


TAB = """
<span class="main_title2">Scores</span>
<table><tr><td>Fənnlər</td><td>Yekun bal</td><td>Fayl</td></tr>
<tr><td>Quiz 1</td><td>8</td><td><a href="/files/q1.pdf">q1</a></td></tr>
<tr><td colspan="2">odd row</td></tr></table>
<table><tr><td>only header</td></tr></table>
<table><tr><td>layout <table><tr><th>A</th></tr><tr><td>1</td></tr></table></td></tr></table>
"""


class FakeScraper:
    class settings:
        dashboard_url = "https://d.example.com/app/"

    def __init__(self):
        self.urls = []

    def fetch(self, url, headers=None):
        self.urls.append(url)
        html = PLAN if "lecture_plan" in url else TAB if "lecture_score" in url else COURSE if "lec_open_idx=7" in url and "index.php" in url else HOME
        return BeautifulSoup(html, "html.parser")


def test_list_courses():
    assert courses.list_courses(FakeScraper()) == [
        {"lec_open_idx": "7", "sem_code": "S1", "name": "Math", "path": "/studies/index.php?lec_open_idx=7&sem_cd=S1&lecture_name=Math"}
    ]


def test_lecture_plan():
    scraper = FakeScraper()
    plan = courses.lecture_plan(scraper, "7")
    assert plan["params"] == {"lec_open_idx": "7", "lecture_code": "MA1", "sem_code": "S2"}
    assert scraper.urls[-1].endswith("/studies/lecture_plan.php?lec_open_idx=7&lecture_code=MA1&sem_code=S2")
    assert plan["course"] == "Math"
    assert plan["blocks"] == [
        {"title": "Books", "rows": [{"author": "Bob", "Ad": "Book"}]},
        {"title": "Goals", "text": "Learn"},
    ]


def test_unknown_course():
    import pytest

    with pytest.raises(courses.CourseNotFound):
        courses.course_params(FakeScraper(), "999")


def test_course_page_reads_titled_tables_with_links():
    scraper = FakeScraper()
    page = courses.course_page(scraper, "7", "scores")
    assert scraper.urls[-1].endswith("/studies/lecture_score.php?lec_open_idx=7&lecture_code=MA1&sem_code=S2")
    assert page["params"]["lecture_code"] == "MA1"
    assert page["blocks"][0] == {
        "title": "Scores",
        "rows": [
            {"course": "Quiz 1", "final_score": "8", "Fayl": "q1", "link": "https://d.example.com/files/q1.pdf"},
            {"column_1": "odd row"},
        ],
    }
    # layout tables are skipped in favour of the table nested inside them
    assert page["blocks"][1] == {"title": "Scores", "rows": [{"A": "1"}]}
    assert len(page["blocks"]) == 2
