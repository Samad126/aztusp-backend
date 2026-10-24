"""Course tasks (Qiymətləndirmə): the detail that opens from a row of a tasks list, its files, and handing in work."""

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .client import SiteScraper
from .courses import course_params
from .materials import delta_ops
from .notices import attachments_in, cell_text, labelled_values, open_attachment

DETAIL_PAGE = "/studies/lecture_task_view.php"
SUBMIT_PAGE = "/studies/lecture_task_present_proc.php"
TASK_TABLE = "t_lecture_task"
# The upload form's two file inputs, in order.
UPLOAD_FIELDS = ("o_file1", "o_file2")

LABELS = {
    "Movzu": "subject",
    "Başlanğıc": "period",
    "Verilmə əlaməti": "submission_method",
    "Sərbəst işin növü": "task_type",
    "İşin verilmə vəziyyəti": "score",
    "Qiymətləndirmə": "evaluation",
    "Qoşma1": "file1",
    "Qoşma2": "file2",
}


class TaskNotFound(LookupError):
    pass


def open_task_file(scraper: SiteScraper, task_id: str, file_no: str):
    """Stream a file of a task. Returns (response, filename); raises FileNotFound if there is no such file."""
    return open_attachment(scraper, TASK_TABLE, task_id, file_no)


def task_detail(scraper: SiteScraper, lec_open_idx: str, task_id: str) -> dict:
    """One task as the site shows it when its `Movzu` is clicked in the tasks list.

    The site only serves this page to the POST its own script sends (the list's filters, which the page ignores).
    """
    params = course_params(scraper, lec_open_idx)
    url = urljoin(scraper.settings.dashboard_url, f"{DETAIL_PAGE}?t_idx={task_id}")
    soup = scraper.post_page(url, {**params, "page_num": "1", "page_list": "30", "field": "s_date", "sort": "ASC", "search_item": "subject", "search_word": ""})

    values = labelled_values(soup, LABELS)
    subject = cell_text(values, "subject")
    if not subject:
        raise TaskNotFound(f"No task with id {task_id}")

    start, _, end = cell_text(values, "period").partition("~")
    score, _, maximum = (part.strip() for part in cell_text(values, "score").partition("/"))
    attachments = [a for key in ("file1", "file2") if key in values for a in attachments_in(values[key], url) if a["name"]]
    return {
        "id": task_id,
        "subject": subject,
        "task_type": cell_text(values, "task_type"),
        "start_date": start.strip(),
        "end_date": end.strip(),
        "submission_method": cell_text(values, "submission_method"),
        "score": score or None,
        "max_score": maximum or None,
        "evaluation": cell_text(values, "evaluation"),
        "body": "".join(op["insert"] for op in delta_ops(soup) if isinstance(op.get("insert"), str)).strip(),
        "links": _links(soup),
        "attachments": attachments,
        "can_submit": _upload_form(soup) is not None,
    }


def _links(soup: BeautifulSoup) -> list[str]:
    """The links of the task's text, in order and without repeats."""
    links: list[str] = []
    for op in delta_ops(soup):
        link = (op.get("attributes") or {}).get("link")
        if isinstance(link, str) and link not in links:
            links.append(link)
    return links


def _upload_form(soup: BeautifulSoup):
    return next((form for form in soup.find_all("form") if form.find("input", attrs={"type": "file"})), None)


def submit_task(scraper: SiteScraper, lec_open_idx: str, task_id: str, files: list[tuple[str, bytes, str]]) -> dict:
    """Hand in work: up to two files `(filename, content, content type)`, as the `Əlavə et` button of the modal does.

    The task is read first, so a closed or unknown task is refused before anything is sent. Returns the task as it
    reads afterwards.
    """
    if not 1 <= len(files) <= len(UPLOAD_FIELDS):
        raise ValueError(f"Send one or two files, not {len(files)}")
    task = task_detail(scraper, lec_open_idx, task_id)
    if not task["can_submit"]:
        raise TaskClosed(f"Task {task_id} does not take work")

    params = course_params(scraper, lec_open_idx)
    form = {"idx": "", "t_idx": task_id, "mode": "add", "sem_code": params["sem_code"], "lec_open_idx": lec_open_idx}
    upload = {field: file for field, file in zip(UPLOAD_FIELDS, files)}
    response = scraper.post_form(urljoin(scraper.settings.dashboard_url, SUBMIT_PAGE), form, files=upload)
    response.raise_for_status()
    return task_detail(scraper, lec_open_idx, task_id)


class TaskClosed(RuntimeError):
    pass
