import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .client import PJAX_HEADERS, SiteScraper
from .targets import FIELD_MAP


class CourseNotFound(LookupError):
    pass


# Labels in the plan's top info table that clash with FIELD_MAP (its "Həftə" is a count).
PLAN_INFO_MAP = {
    "Professor adı": "professor",
    "Bölmə": "section",
    "Kafedra/Kurs/Qrup": "department_group",
    "Tələbələrin sayı": "student_count",
    "Kredit": "credits",
    "Saatlar": "hours",
    "Həftə": "weeks",
}

PLAN_TABLE_MAP = {
    "Dərsliyin növü": "book_type",
    "Müəllif": "author",
    "Dərsliyin adı": "title",
    "Nəşriyyat": "publisher",
    "Nəşr ili": "year",
    "Həftə": "week",
    "Movzu": "topic",
    "Tədris metodları": "teaching_methods",
    "Didaktik materiallar": "materials",
    "Avadanlıq": "equipment",
}


def list_courses(scraper: SiteScraper) -> list[dict]:
    """Courses linked from the dashboard home page, with the ids the course pages need."""
    soup = scraper.fetch(scraper.settings.dashboard_url)
    courses = []
    for anchor in soup.find_all("a", href=True):
        if "/studies/index.php" not in anchor["href"]:
            continue
        query = parse_qs(urlparse(anchor["href"]).query)
        if "lec_open_idx" not in query:
            continue
        courses.append(
            {
                "lec_open_idx": query["lec_open_idx"][0],
                "sem_code": query.get("sem_cd", [None])[0],
                "name": query.get("lecture_name", [anchor.get_text(" ", strip=True)])[0],
                "path": anchor["href"],
            }
        )
    return courses


def course_params(scraper: SiteScraper, lec_open_idx: str) -> dict[str, str]:
    """Query params shared by every course tab (lec_open_idx, lecture_code, sem_code)."""
    course = next((c for c in list_courses(scraper) if c["lec_open_idx"] == lec_open_idx), None)
    if course is None:
        raise CourseNotFound(f"No course with lec_open_idx={lec_open_idx}")

    page_url = urljoin(scraper.settings.dashboard_url, course["path"])
    soup = scraper.fetch(page_url, headers=PJAX_HEADERS)
    for anchor in soup.find_all("a", href=True):
        query = parse_qs(urlparse(anchor["href"]).query)
        if "lecture_code" in query:
            return {
                "lec_open_idx": lec_open_idx,
                "lecture_code": query["lecture_code"][0],
                "sem_code": query.get("sem_code", [course["sem_code"]])[0],
            }
    raise CourseNotFound(f"No lecture_code found on {page_url}")


# API name -> page file under /studies/ for the other course tabs.
COURSE_PAGES = {
    "notices": "lecture_notice",
    "board": "lecture_board",
    "materials": "lecture_data",
    "tasks": "lecture_task",
    "scores": "lecture_score",
    "attendance": "lecture_attend",
}

# Column labels of the list tabs (notices, board, materials, tasks); anything else falls back to FIELD_MAP.
TAB_COLUMN_MAP = {
    "Tarix": "date",
    "Sərbəst işin növü": "task_type",
    "Qiymətləndirmə": "evaluation",
    "Başlanğıc": "start_date",
    "Son gun": "end_date",
}

ATTEND_INFO_MAP = {
    "Fənnin adı": "course",
    "Saatların cəmi": "total_hours",
    "Həftəlik dərs saatları": "weekly_hours",
    "Kredit": "credits",
    "Qrup": "group",
    "Müəllim": "teacher",
}

# CSS classes of the attendance marks (see the legend on the page: i/e present, q/b absent, d/e not entered).
ATTEND_MARKS = {"ie": "present", "qb": "absent", "nd": "not_entered"}

SEND_VIEW = re.compile(r"send_view\(\s*['\"]?([^'\")\s]+)")
SCORE_LABEL = re.compile(r"^(.*?)\s*\(\s*(\d+(?:[.,]\d+)?)\s*\)$")


def _tab_url(scraper: SiteScraper, page: str, params: dict[str, str]) -> str:
    return urljoin(scraper.settings.dashboard_url, f"/studies/{page}.php") + "?" + urlencode(params)


def _fetch_tab(scraper: SiteScraper, lec_open_idx: str, name: str) -> tuple[dict[str, str], Tag | BeautifulSoup, str | None, str]:
    """Fetch one course tab: (ids, the tab's content, course heading, page url)."""
    params = course_params(scraper, lec_open_idx)
    url = _tab_url(scraper, COURSE_PAGES[name], params)
    soup = scraper.fetch(url, headers=PJAX_HEADERS)
    heading = soup.select_one("h6.page-title")
    course = _clean(heading.get_text(" ", strip=True)) if heading else None
    return params, soup.select_one("#secondary_content") or soup, course, url


def _clean(text: str) -> str:
    return " ".join(text.split())


def _is_hidden(tag: Tag) -> bool:
    """True if the element or an ancestor has an inline display:none (the site keeps old layouts hidden)."""
    return any("display:none" in (el.get("style") or "").replace(" ", "").lower() for el in (tag, *tag.parents) if isinstance(el, Tag))


def _visible_tables(content: Tag | BeautifulSoup) -> list[Tag]:
    return [table for table in content.find_all("table") if not _is_hidden(table)]


def course_items(scraper: SiteScraper, lec_open_idx: str, name: str) -> dict:
    """A list tab (notices, board, materials, tasks) as records. An empty list means nothing was posted."""
    params, content, course, url = _fetch_tab(scraper, lec_open_idx, name)
    tables = _visible_tables(content)
    table = next((t for t in tables if t.find("thead")), None) or next((t for t in tables if t.get("id") == "op_list"), None)
    return {"params": params, "course": course, "items": _table_records(table, url) if table else []}


def _table_records(table: Tag, page_url: str) -> list[dict[str, str]]:
    """Records for a list table: the first row names the columns.

    A lone cell spanning the table ("Qeyd olunmuş material yoxdur") is the site's empty state and is
    skipped. When a row opens a detail view with send_view(<id>), that id is returned as "id", and the
    first link in a row as "link".
    """
    rows = [(row, row.find_all(["td", "th"], recursive=False)) for row in table.find_all("tr")]
    rows = [(row, cells) for row, cells in rows if cells]
    if len(rows) < 2:
        return []
    labels = [_clean(cell.get_text(" ", strip=True)) for cell in rows[0][1]]
    keys = [TAB_COLUMN_MAP.get(label) or FIELD_MAP.get(label) or label or f"column_{i}" for i, label in enumerate(labels, 1)]

    records = []
    for row, cells in rows[1:]:
        if len(cells) == 1 and (cells[0].get("colspan") or len(keys) > 1):
            continue
        row_keys = keys if len(cells) == len(keys) else [f"column_{i}" for i in range(1, len(cells) + 1)]
        record = {key: cell.get_text(" ", strip=True) for key, cell in zip(row_keys, cells)}
        if (detail_id := _detail_id(row)) is not None:
            record["id"] = detail_id
        link = next((a["href"] for a in row.find_all("a", href=True) if not a["href"].startswith(("#", "javascript:"))), None)
        if link:
            record["link"] = urljoin(page_url, link)
        records.append(record)
    return records


def _detail_id(row: Tag) -> str | None:
    for el in (row, *row.find_all(attrs={"onclick": True}), *row.find_all("a", href=True)):
        match = SEND_VIEW.search((el.get("onclick") or "") + " " + (el.get("href") or ""))
        if match:
            return match.group(1)
    return None


def course_scores(scraper: SiteScraper, lec_open_idx: str) -> dict:
    """Current scores: one component per column of the score table (name, maximum, score) plus the total."""
    params, content, course, _ = _fetch_tab(scraper, lec_open_idx, "scores")
    result: dict = {"params": params, "course": course, "table": [], "components": [], "total": None, "notes": []}

    table = content.select_one("table#toplam_score")
    grid = _grid(table) if table else []
    result["table"] = [{_clean(label): value for label, value in zip(grid[0], row)} for row in grid[1:]]
    if len(grid) >= 2:
        for label, value in zip(grid[0], grid[1]):
            label = _clean(label)
            if label.lower().startswith("toplam"):
                result["total"] = value or None
                continue
            match = SCORE_LABEL.match(label)
            name, maximum = (match.group(1), match.group(2)) if match else (label, None)
            result["components"].append({"name": name, "max": maximum, "score": value or None})

    result["notes"] = [_clean(p.get_text(" ", strip=True)) for p in content.select("p.text-danger")]
    return result


def course_attendance(scraper: SiteScraper, lec_open_idx: str) -> dict:
    """The attendance table: class meetings, every student row with a mark per meeting, and the header values."""
    params, content, course, _ = _fetch_tab(scraper, lec_open_idx, "attendance")
    result: dict = {"params": params, "course": course, "info": {}, "legend": {}, "header": {}, "sessions": [], "students": []}

    tables = _visible_tables(content)
    info_table = next((t for t in tables if t.get("id") == "op_list"), None)
    if info_table is not None:
        result["info"], result["legend"] = _attendance_info(info_table)

    journal = next((t for t in tables if t.get("id") == "datatable-buttons"), None)
    if journal is None:
        return result

    head = {}
    header_rows = [r.find_all(["th", "td"], recursive=False) for r in (journal.find("thead") or journal).find_all("tr")]
    for cells in header_rows:
        if cells:
            head[_text(cells[0])] = cells
    numbers = [_text(c) for c in head.get("Nömrə", [])[3:] if _text(c).isdigit()]
    count = len(numbers)
    dates = _cell_texts(head, "Dərsin tarixi", count)
    journal_dates = _cell_texts(head, "Jurnalın yazılma tarixi", count)
    result["sessions"] = [
        {"number": number, "date": dates[i] or None, "journal_date": journal_dates[i] or None} for i, number in enumerate(numbers)
    ]
    # The row under the column titles repeats the header values: points and percentage (e.g. "0" and "100%").
    values = next((cells for cells in header_rows if cells and len(cells) == count + 2 and all(not _text(c) for c in cells[:count])), None)
    if values:
        result["header"] = {"score": _text(values[-2]) or None, "percent": _text(values[-1]) or None}

    username = getattr(scraper, "username", "").lower()
    for cells in _student_rows(journal, count):
        tail = cells[3 + count :]
        student_id = _text(cells[1])
        result["students"].append(
            {
                "number": _text(cells[0]),
                "student_id": student_id,
                "name": _text(cells[2]),
                "is_me": student_id.lower() == username,
                "marks": [_mark(number, cell) for number, cell in zip(numbers, cells[3 : 3 + count])],
                "score": (_text(tail[-2]) or None) if len(tail) >= 2 else None,
                "percent": (_text(tail[-1]) or None) if len(tail) >= 2 else None,
            }
        )
    return result


def _text(cell: Tag) -> str:
    return _clean(cell.get_text(" ", strip=True))


def _mark(number: str, cell: Tag) -> dict:
    classes = {c for el in (cell, *cell.select("[class]")) for c in el.get("class", [])}
    return {"session": number, "status": _text(cell) or None, "mark": next((ATTEND_MARKS[c] for c in classes if c in ATTEND_MARKS), None)}


def _student_rows(journal: Tag, count: int) -> list[list[Tag]]:
    """Cells of every student row: number, id and name, one cell per class meeting, then the totals."""
    return [
        cells
        for row in journal.find_all("tr")
        if row.find_parent("thead") is None and len(cells := row.find_all(["th", "td"], recursive=False)) >= 3 + count
    ]


def _attendance_info(table: Tag) -> tuple[dict[str, str], dict[str, str]]:
    """Course summary and the legend of mark codes.

    Row 1 holds the labels (one cell spans two rows and holds the dates), row 2 the values and
    row 3 the legend ("Davamiyyət : i/e", "Mühazirə : M", ...).
    """
    rows = [row.find_all(["td", "th"], recursive=False) for row in table.find_all("tr")]
    rows = [cells for cells in rows if cells]
    if len(rows) < 2:
        return {}, {}
    labels = [c for c in rows[0] if not c.get("rowspan")]
    info = {ATTEND_INFO_MAP.get(_text(l), _text(l)): _text(v) for l, v in zip(labels, rows[1])}
    if period := next((c for c in rows[0] if c.get("rowspan")), None):
        info["period"] = _text(period)
    legend = {}
    for cell in rows[2] if len(rows) > 2 else []:
        name, _, code = _text(cell).partition(":")
        if code:
            legend[name.strip()] = code.strip()
    return info, legend


def _cell_texts(head: dict[str, list[Tag]], label: str, count: int) -> list[str]:
    cells = head.get(label, [])[1 : 1 + count]
    texts = [_text(c) for c in cells]
    return texts + [""] * (count - len(texts))


def _own_row(journal: Tag, numbers: list[str], username: str) -> list[Tag] | None:
    """The journal row of this student: the one whose identifier column matches, else the only data row."""
    rows = [
        cells
        for row in journal.find_all("tr")
        if row.find_parent("thead") is None and len(cells := row.find_all(["th", "td"], recursive=False)) >= 3 + len(numbers)
    ]
    own = next((c for c in rows if _clean(c[1].get_text(" ", strip=True)).lower() == username.lower()), None)
    return own or (rows[0] if len(rows) == 1 else None)


def lecture_plan(scraper: SiteScraper, lec_open_idx: str) -> dict:
    """Course info plus every titled block (table or text) of the lecture plan page."""
    params = course_params(scraper, lec_open_idx)
    soup = scraper.fetch(_tab_url(scraper, "lecture_plan", params), headers=PJAX_HEADERS)

    plan: dict = {"params": params, "course": None, "semester": None, "info": None, "blocks": []}

    for cell in soup.select("td.list_title1"):
        text = cell.get_text(" ", strip=True)
        if text.startswith("Dərsin adı"):
            plan["course"] = text.split(":", 1)[1].strip()
            sibling = cell.find_next_sibling("td")
            plan["semester"] = sibling.get_text(" ", strip=True) if sibling else None
            break

    plan["info"] = _plan_info(soup)

    for title in soup.select("span.main_title2"):
        table = title.find_next_sibling("table")
        if table is None:
            continue
        block: dict = {"title": title.get_text(" ", strip=True)}
        grid = _grid(table)
        if len(grid) > 1 or (grid and len(grid[0]) > 1):
            block["rows"] = _grid_records(table, PLAN_TABLE_MAP)
        else:
            block["text"] = grid[0][0] if grid and grid[0] else ""
        plan["blocks"].append(block)
    return plan


def _plan_info(soup: BeautifulSoup) -> dict[str, str] | None:
    """Professor / department / credits row.

    Read it from the table when the page has one. Older pages had unbalanced <!-- comments, so
    the table ended up inside a comment string; then read its cells straight from the text.
    """
    for table in soup.find_all("table"):
        grid = _grid(table)
        if len(grid) >= 2 and "Professor adı" in grid[0] and len(grid[0]) == len(grid[1]):
            return {PLAN_INFO_MAP.get(label, label): value for label, value in zip(grid[0], grid[1])}
    text = soup.find(string=lambda s: s and "Professor adı" in s)
    if text is None:
        return None
    start = text.rindex("<tr", 0, text.index("Professor adı"))
    segment = text[start : text.index("</table>", start)]
    rows = [re.findall(r'list_title1">([^<]*)</td', row) for row in segment.split("</tr>")]
    rows = [[" ".join(cell.split()) for cell in row] for row in rows if row]
    if len(rows) < 2 or len(rows[0]) != len(rows[1]):
        return None
    return {PLAN_INFO_MAP.get(label, label): value for label, value in zip(rows[0], rows[1])}


def _grid(table: Tag) -> list[list[str]]:
    return [
        [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"], recursive=False)]
        for row in table.find_all("tr")
    ]


def _grid_records(table: Tag, key_map: dict[str, str]) -> list[dict[str, str]]:
    """Records for a table whose first row is the header (cells are <td>, not <th>)."""
    grid = _grid(table)
    if not grid:
        return []
    headers = [" ".join(label.split()) for label in grid[0]]
    keys = [key_map.get(label) or FIELD_MAP.get(label) or label or f"column_{i}" for i, label in enumerate(headers, 1)]
    return [dict(zip(keys, row)) for row in grid[1:] if len(row) == len(keys)]
