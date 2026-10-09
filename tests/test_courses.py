from bs4 import BeautifulSoup

from app.scraping import courses

HOME = '<a href="/studies/index.php?lec_open_idx=7&sem_cd=S1&lecture_name=Math">x</a><a href="/other">y</a>'
COURSE = '<a href="/x?lecture_code=MA1&sem_code=S2">tab</a>'
TITLE = '<h6 class="page-title float-left">Math     ( M1 ) </h6>'

EMPTY_LIST = TITLE + """<div id="secondary_content"><div style="display: none"><table id="op_list"><tr><td>Nömrə</td><td>Movzu</td></tr>
<tr><td colspan="2">Qeyd olunmuş material yoxdur</td></tr></table></div>
<table id="op_list" class="table"><thead><tr><th>Nömrə</th><th>Movzu</th><th>Müəllif</th><th>Tarix</th><th>Müraciətlərin sayı</th></tr></thead>
<tbody><tr><td colspan="5">Qeyd olunmuş material yoxdur</td></tr></tbody></table></div>"""

FILLED_LIST = TITLE + """<div id="secondary_content"><table id="op_list"><thead><tr><th>Nömrə</th><th>Movzu</th><th>Müəllif</th><th>Tarix</th><th>Müraciətlərin sayı</th></tr></thead>
<tbody><tr onclick="send_view(12)"><td>1</td><td>Syllabus</td><td>Dr. X</td><td>2026-09-20</td><td>4</td></tr>
<tr><td>2</td><td><a href="/files/a.pdf">Slides</a></td><td>Dr. X</td><td>2026-09-27</td><td>0</td></tr></tbody></table></div>"""

TASKS = TITLE + """<div id="secondary_content"><table id="datatable-task"><thead><tr><th>Nömrə</th><th>Sərbəst işin növü</th><th>Qiymətləndirmə</th><th>Movzu</th><th></th><th>Başlanğıc</th><th>Son gun</th></tr></thead>
<tbody><tr><td colspan="8">Qeyd olunmuş material yoxdur</td></tr></tbody></table></div>"""

SCORES = TITLE + """<div id="secondary_content"><div class="container-fluid"><div class="card-body table-responsive">
<br><table id="toplam_score" class="table table-striped" border="1"><tbody class="text-center">
  <!--   <tr bgcolor="#F9F9FB" height="30" align="center">
    <td><b>Adı</td> -->
            <td>sərbəst iş(10)</td>
                <td>Məşğələ(30)</td>
            <td>Davamiyyət</td>
    <td><b>Toplam<!-- 총점 --></b></td>
                <tr height="24" align="center">
                <td><input type="hidden" name="score_S1[]" id="score_S10" value="0"><font color="">7</font></td>
                <td><input type="hidden" name="score_S1[]" id="score_S11" value="0"><font color="">0</font></td>
                           <td><input type="hidden" value="0"></td>
            <td><input type="hidden" value="0">7</td>
            </tr></tbody></table>
<p class="text-danger" style="font-size: 15px;"><i><b>*Qeyd:</b> Məşğələ balının görünməsi üçün minimum 3 qiymət olmalıdır.</i></p></div></div></div>"""


def _attend(rows: str, own_id: str = "S1") -> str:
    head = "".join(f'<th colspan="1">{i}</th>' for i in range(1, 4))
    blank = "<th></th>" * 3
    dates = "".join(f"<th><font>{d}</font></th>" for d in ("15.09", "22.09", ""))
    journal = "".join(f"<th><font>{d}</font></th>" for d in ("16.09", "", ""))
    return TITLE + f"""<div id="secondary_content">
<table id="op_list"><tbody>
<tr><td>Fənnin adı<!-- x --></td><td>Saatların cəmi</td><td>Həftəlik dərs saatları</td><td>Kredit</td><td rowspan="2">2026.09.15 - 2026.12.31</td><td>Qrup</td><td>Müəllim</td></tr>
<tr><td>Math</td><td>30</td><td></td><td>6</td><td>M1</td><td>Dr. X</td></tr>
<tr><td>Davamiyyət : i/e</td><td>İştirak etmir: q/b</td><td colspan="2">Mühazirə : M</td></tr></tbody></table>
<table id="datatable-buttons"><thead><tr></tr>
<tr><th rowspan="2">Nömrə</th><th rowspan="2">İdentifikator</th><th rowspan="2">Adı</th>{head}<th rowspan="38"></th><th>Bal</th><th>Davamiyyət faizi</th></tr>
<tr>{blank}<th>0</th><th>100%</th></tr>
<tr><th colspan="3">Dərsin tarixi</th>{dates}<th></th><th></th></tr>
<tr><th colspan="3">Jurnalın yazılma tarixi</th>{journal}<th></th><th></th></tr></thead>
{rows.replace("{own}", own_id)}</table></div>"""


MY_ROW = (
    '<tbody><tr><th>1</th><th>{own}</th><th>Me</th>'
    '<td><span class="attend-label ie">i</span></td><td><span class="attend-label qb">q</span></td><td></td>'
    '<td></td><td>2</td><td>66</td></tr></tbody>'
)
OTHER_ROW = (
    '<tbody><tr><th>2</th><th>S2</th><th>Other</th><td></td><td></td><td></td><td></td><td>0</td><td>100</td></tr></tbody>'
)
ATTEND = _attend(MY_ROW + OTHER_ROW)

TAB_PAGES = {
    "lecture_notice": EMPTY_LIST,
    "lecture_board": FILLED_LIST,
    "lecture_data": EMPTY_LIST,
    "lecture_task": TASKS,
    "lecture_score": SCORES,
    "lecture_attend": ATTEND,
}

PLAN = """
<td class="list_title1">Dərsin adı : Math</td><td>Fall</td>
<span class="main_title2">Books</span><table><tr><td>Müəllif</td><td>Ad</td></tr><tr><td>Bob</td><td>Book</td></tr></table>
<span class="main_title2">Goals</span><table><tr><td>Learn</td></tr></table>
"""


class FakeScraper:
    username = "S1"

    class settings:
        dashboard_url = "https://d.example.com/app/"

    def __init__(self):
        self.urls = []

    def fetch(self, url, headers=None):
        self.urls.append(url)
        if "/studies/lecture_" in url:
            page = url.split("/studies/")[1].split(".php")[0]
            html = PLAN if page == "lecture_plan" else TAB_PAGES[page]
        elif "lec_open_idx=7" in url and "index.php" in url:
            html = COURSE
        else:
            html = HOME
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


def test_list_tab_empty_state_gives_no_items_and_ignores_hidden_table():
    scraper = FakeScraper()
    page = courses.course_items(scraper, "7", "notices")
    assert scraper.urls[-1].endswith("/studies/lecture_notice.php?lec_open_idx=7&lecture_code=MA1&sem_code=S2")
    assert page == {"params": {"lec_open_idx": "7", "lecture_code": "MA1", "sem_code": "S2"}, "course": "Math ( M1 )", "items": []}
    assert courses.course_items(scraper, "7", "tasks")["items"] == []


def test_list_tab_rows_get_english_keys_ids_and_links():
    page = courses.course_items(FakeScraper(), "7", "board")
    assert page["items"] == [
        {"number": "1", "subject": "Syllabus", "author": "Dr. X", "date": "2026-09-20", "views": "4", "id": "12"},
        {"number": "2", "subject": "Slides", "author": "Dr. X", "date": "2026-09-27", "views": "0", "link": "https://d.example.com/files/a.pdf"},
    ]


def test_scores_keep_every_cell_including_zero_and_empty():
    page = courses.course_scores(FakeScraper(), "7")
    assert page["table"] == [{"sərbəst iş(10)": "7", "Məşğələ(30)": "0", "Davamiyyət": "", "Toplam": "7"}]
    assert page["components"] == [
        {"name": "sərbəst iş", "max": "10", "score": "7"},
        {"name": "Məşğələ", "max": "30", "score": "0"},
        {"name": "Davamiyyət", "max": None, "score": None},
    ]
    assert page["total"] == "7"
    assert page["notes"] == ["*Qeyd: Məşğələ balının görünməsi üçün minimum 3 qiymət olmalıdır."]


def test_attendance_returns_header_legend_sessions_and_every_student():
    page = courses.course_attendance(FakeScraper(), "7")
    assert page["info"] == {
        "course": "Math", "total_hours": "30", "weekly_hours": "", "credits": "6", "group": "M1", "teacher": "Dr. X",
        "period": "2026.09.15 - 2026.12.31",
    }
    assert page["legend"] == {"Davamiyyət": "i/e", "İştirak etmir": "q/b", "Mühazirə": "M"}
    assert page["header"] == {"score": "0", "percent": "100%"}
    assert page["sessions"] == [
        {"number": "1", "date": "15.09", "journal_date": "16.09"},
        {"number": "2", "date": "22.09", "journal_date": None},
        {"number": "3", "date": None, "journal_date": None},
    ]
    me, other = page["students"]
    assert (me["number"], me["student_id"], me["name"]) == ("1", "S1", "Me")
    assert me["marks"] == [
        {"session": "1", "status": "i", "mark": "present"},
        {"session": "2", "status": "q", "mark": "absent"},
        {"session": "3", "status": None, "mark": None},
    ]
    assert (me["score"], me["percent"]) == ("2", "66")
    assert (other["student_id"], other["score"], other["percent"]) == ("S2", "0", "100")


def test_plan_info_read_from_live_table():
    live = PLAN + """<table><thead><tr><th>Professor adı</th><th>Kredit</th></tr></thead><tbody><tr><td>Dr. X</td><td>6</td></tr></tbody></table>"""
    class Scraper(FakeScraper):
        def fetch(self, url, headers=None):
            return BeautifulSoup(live, "html.parser") if "lecture_plan" in url else super().fetch(url, headers)
    assert courses.lecture_plan(Scraper(), "7")["info"] == {"professor": "Dr. X", "credits": "6"}


def test_course_name_falls_back_to_dashboard_name_without_group_suffix():
    class NoHeading(FakeScraper):
        def fetch(self, url, headers=None):
            soup = super().fetch(url, headers)
            for heading in soup.select("h6.page-title"):
                heading.decompose()
            for anchor in soup.find_all("a", href=True):
                anchor["href"] = anchor["href"].replace("lecture_name=Math", "lecture_name=Math%5BM1%5D")
            return soup

    for name in ("notices", "scores", "attendance"):
        assert courses._fetch_tab(NoHeading(), "7", name)[2] == "Math"
