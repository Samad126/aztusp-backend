import re
from urllib.parse import parse_qs, quote, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .client import PJAX_HEADERS, SiteScraper

DETAIL_PAGE = "/studies/notice_view.php"
NOTICE_TABLE = "t_notice"

# Label cells on the detail page -> output key.
LABELS = {
    "Movzu": "subject",
    "Müəllif": "author",
    "Tərtib tarixi": "created_at",
    "Müraciətlərin sayı": "views",
    "Qoşma fayl": "attachments",
}


class NoticeNotFound(LookupError):
    pass


class FileNotFound(LookupError):
    pass


DOWNLOAD_PAGE = "/studies/file_down.php"
FILENAME = re.compile(r'filename="?([^";]+)"?')


def open_notice_file(scraper: SiteScraper, notice_id: str, file_no: str):
    """Stream an attachment of a notice. Returns (response, filename); raises FileNotFound if there is no such file."""
    return open_attachment(scraper, NOTICE_TABLE, notice_id, file_no)


def open_attachment(scraper: SiteScraper, table: str, record_id: str, file_no: str):
    """Stream file `file_no` of a record in the site's `table` (`t_notice`, `t_lecture_data`). Returns (response, filename).

    The site answers a missing file with an HTML page, not an error, so only a real `attachment` response counts.
    """
    url = urljoin(scraper.settings.dashboard_url, f"{DOWNLOAD_PAGE}?table={table}&wr_idx={record_id}&wr_fno={file_no}")
    response = scraper.open_download(url)
    disposition = response.headers.get("Content-Disposition", "")
    if response.status_code != 200 or not disposition.lower().startswith("attachment"):
        response.close()
        raise FileNotFound(f"No file {file_no} for {record_id}")
    return response, _filename(disposition)


def _filename(disposition: str) -> str:
    """The site sends UTF-8 names that arrive decoded as Latin-1; undo that."""
    match = FILENAME.search(disposition)
    name = match.group(1) if match else "download"
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def content_disposition(filename: str) -> str:
    fallback = filename.encode("ascii", "replace").decode().replace("?", "_").replace('"', "'")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


def labelled_values(soup: BeautifulSoup, labels: dict[str, str]) -> dict[str, Tag]:
    """The cell after each label cell, keyed by the label's output name in `labels`. The first match of a label wins."""
    values: dict[str, Tag] = {}
    for cell in soup.find_all("td"):
        key = labels.get(cell.get_text(" ", strip=True))
        value = cell.find_next_sibling("td")
        if key and value is not None and key not in values:
            values[key] = value
    return values


def cell_text(values: dict[str, Tag], key: str) -> str:
    return values[key].get_text(" ", strip=True) if key in values else ""


def attachments_in(cell: Tag, page_url: str) -> list[dict]:
    """The file links of a `Qoşma` cell: name, the site's download link and the file number (`wr_fno`)."""
    return [
        {
            "name": a.get_text(" ", strip=True),
            "url": urljoin(page_url, a["href"]),
            "file_no": parse_qs(urlparse(a["href"]).query).get("wr_fno", [""])[0],
        }
        for a in cell.find_all("a", href=True)
    ]


def notice_detail(scraper: SiteScraper, notice_id: str) -> dict:
    """One notice as the site shows it when a row of the notices list is opened."""
    url = urljoin(scraper.settings.dashboard_url, f"{DETAIL_PAGE}?wr_idx={notice_id}")
    soup = scraper.fetch(url, headers=PJAX_HEADERS)

    values = labelled_values(soup, LABELS)
    subject = cell_text(values, "subject")
    if not subject:
        raise NoticeNotFound(f"No notice with id {notice_id}")

    attachments = attachments_in(values["attachments"], url) if "attachments" in values else []

    # The message sits in the last row of the table that holds the labels.
    rows = values["subject"].find_parent("table").find_all("tr", recursive=False)
    body_cell = rows[-1].find("td") if rows and len(rows[-1].find_all("td", recursive=False)) == 1 else None
    body = body_cell.get_text("\n", strip=True) if body_cell is not None else ""

    return {
        "id": notice_id,
        "url": url,
        "subject": subject,
        "author": cell_text(values, "author"),
        "created_at": cell_text(values, "created_at"),
        "views": cell_text(values, "views"),
        "attachments": attachments,
        "body": body,
    }
