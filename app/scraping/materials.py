"""Course materials (Didaktik materiallar): the detail that opens from a row of a materials list, with its files and text."""

import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .client import PJAX_HEADERS, SiteScraper
from .notices import attachments_in, cell_text, labelled_values, open_attachment

DETAIL_PAGE = "/studies/lecture_data_view.php"
FILE_TABLE = "t_lecture_data"

# Label cells on the detail page -> output key. The list tab uses the same keys (subject, author, date, views).
LABELS = {
    "Movzu": "subject",
    "Müəllif": "author",
    "Tarix": "date",
    "Müraciətlərin sayı": "views",
    "Qoşma": "attachments",
}

# The text is not in the HTML: a script holds it as a Quill delta, `var json = '[{"insert":"..."}]'`.
DELTA_LITERAL = re.compile(r"var json = '((?:[^'\\]|\\.)*)'", re.DOTALL)
JS_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.DOTALL)
JS_CHARS = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


class MaterialNotFound(LookupError):
    pass


def material_detail(scraper: SiteScraper, material_id: str) -> dict:
    """One material as the site shows it when a row of the materials list is opened.

    The site counts the opening as a view, as it does when a row is clicked there.
    """
    url = urljoin(scraper.settings.dashboard_url, f"{DETAIL_PAGE}?idx={material_id}")
    soup = scraper.fetch(url, headers=PJAX_HEADERS)

    values = labelled_values(soup, LABELS)
    subject = cell_text(values, "subject")
    if not subject:
        raise MaterialNotFound(f"No material with id {material_id}")

    return {
        "id": material_id,
        "subject": subject,
        "author": cell_text(values, "author"),
        "date": cell_text(values, "date"),
        "views": cell_text(values, "views"),
        "attachments": attachments_in(values["attachments"], url) if "attachments" in values else [],
        "body": _body(soup),
    }


def open_material_file(scraper: SiteScraper, material_id: str, file_no: str):
    """Stream a file of a material. Returns (response, filename); raises FileNotFound if there is no such file."""
    return open_attachment(scraper, FILE_TABLE, material_id, file_no)


def _body(soup: BeautifulSoup) -> str:
    return "".join(op["insert"] for op in delta_ops(soup) if isinstance(op.get("insert"), str)).strip()


def delta_ops(soup: BeautifulSoup) -> list[dict]:
    """The operations of the Quill delta a page keeps in a script (`var json = '[...]'`); empty if there is none."""
    literal = next((match for tag in soup.find_all("script") if (match := DELTA_LITERAL.search(tag.get_text()))), None)
    if literal is None:
        return []
    # The page turns line breaks into \n before parsing the JSON, so the same is done here.
    try:
        delta = json.loads(_js_string(literal.group(1)).replace("\n", "\\n").replace("\r", "\\r"), strict=False)
    except ValueError:
        return []
    return [op for op in delta if isinstance(op, dict)] if isinstance(delta, list) else []


def _js_string(literal: str) -> str:
    """The value of a JavaScript string literal (the quotes removed): its escapes are decoded, the rest kept as is."""

    def decode(match: re.Match) -> str:
        code = match.group(1)
        return chr(int(code[1:], 16)) if len(code) > 1 else JS_CHARS.get(code, code)

    return JS_ESCAPE.sub(decode, literal)
