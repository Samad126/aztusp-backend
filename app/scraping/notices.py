import re
from urllib.parse import parse_qs, quote, urljoin, urlparse

from bs4 import Tag

from .client import PJAX_HEADERS, SiteScraper

DETAIL_PAGE = "/studies/notice_view.php"

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
    """Stream an attachment of a notice. Returns (response, filename); raises FileNotFound if there is no such file.

    The site answers a missing attachment with an HTML page, not an error, so only a real
    `attachment` response counts as a file.
    """
    url = urljoin(scraper.settings.dashboard_url, f"{DOWNLOAD_PAGE}?table=t_notice&wr_idx={notice_id}&wr_fno={file_no}")
    response = scraper.open_download(url)
    disposition = response.headers.get("Content-Disposition", "")
    if response.status_code != 200 or not disposition.lower().startswith("attachment"):
        response.close()
        raise FileNotFound(f"Notice {notice_id} has no file {file_no}")
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


def notice_detail(scraper: SiteScraper, notice_id: str) -> dict:
    """One notice as the site shows it when a row of the notices list is opened."""
    url = urljoin(scraper.settings.dashboard_url, f"{DETAIL_PAGE}?wr_idx={notice_id}")
    soup = scraper.fetch(url, headers=PJAX_HEADERS)

    values: dict[str, Tag] = {}
    for cell in soup.find_all("td"):
        key = LABELS.get(cell.get_text(" ", strip=True))
        value = cell.find_next_sibling("td")
        if key and value is not None and key not in values:
            values[key] = value

    subject = values["subject"].get_text(" ", strip=True) if "subject" in values else ""
    if not subject:
        raise NoticeNotFound(f"No notice with id {notice_id}")

    def text(key: str) -> str:
        return values[key].get_text(" ", strip=True) if key in values else ""

    attachments = (
        [
            {
                "name": a.get_text(" ", strip=True),
                "url": urljoin(url, a["href"]),
                "file_no": parse_qs(urlparse(a["href"]).query).get("wr_fno", [""])[0],
            }
            for a in values["attachments"].find_all("a", href=True)
        ]
        if "attachments" in values
        else []
    )

    # The message sits in the last row of the table that holds the labels.
    rows = values["subject"].find_parent("table").find_all("tr", recursive=False)
    body_cell = rows[-1].find("td") if rows and len(rows[-1].find_all("td", recursive=False)) == 1 else None
    body = body_cell.get_text("\n", strip=True) if body_cell is not None else ""

    return {
        "id": notice_id,
        "url": url,
        "subject": subject,
        "author": text("author"),
        "created_at": text("created_at"),
        "views": text("views"),
        "attachments": attachments,
        "body": body,
    }
