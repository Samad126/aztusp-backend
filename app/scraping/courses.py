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


def lecture_plan(scraper: SiteScraper, lec_open_idx: str) -> dict:
    """Course info plus every titled block (table or text) of the lecture plan page."""
    params = course_params(scraper, lec_open_idx)
    url = urljoin(scraper.settings.dashboard_url, "/studies/lecture_plan.php")
    query = urlencode(params)
    soup = scraper.fetch(f"{url}?{query}", headers=PJAX_HEADERS)

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

    The page has unbalanced <!-- comments, so this table is parsed as part of a comment
    string instead of as markup; read its cells straight from the text.
    """
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
