"""Turn dashboard HTML into plain Python structures."""

from bs4 import BeautifulSoup, Tag

from .targets import FIELD_MAP


def is_total(row: dict[str, str], label: str) -> bool:
    return next(iter(row.values()), None) == label


def parse_table(table: Tag) -> list[dict[str, str]]:
    """Turn an HTML <table> into a list of {column name: cell text} records."""
    thead = table.find("thead")
    # Some pages put <th> straight into <thead> without a <tr>.
    header_row = (thead.find("tr") or thead) if thead else None
    rows = table.select("tbody tr") or [
        row for row in table.find_all("tr") if row.find_parent("thead") is None
    ]
    if header_row is None and rows and rows[0].find("th"):
        header_row, rows = rows[0], rows[1:]

    headers = (
        [cell.get_text(" ", strip=True) for cell in header_row.find_all(["th", "td"], recursive=False)]
        if header_row
        else []
    )

    records = []
    for row in rows:
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"], recursive=False)]
        if not cells:
            continue
        keys = column_keys(headers, len(cells))
        records.append(dict(zip(keys, cells)))
    return records


def column_keys(headers: list[str], width: int) -> list[str]:
    """Use the header names, falling back to column_N for blank or repeated ones."""
    if len(headers) != width:
        return [f"column_{i}" for i in range(1, width + 1)]
    seen: set[str] = set()
    keys = []
    for i, name in enumerate(headers, start=1):
        key = name if name and name not in seen else f"column_{i}"
        seen.add(key)
        keys.append(key)
    return keys


def map_keys(result: dict) -> dict:
    """Rename site labels to the English keys in FIELD_MAP; unknown labels are kept as-is."""

    def rename(row):
        return {FIELD_MAP.get(clean, clean): value for label, value in row.items() if (clean := " ".join(label.split()))}

    result["tables"] = {k: [rename(r) for r in rows] for k, rows in result["tables"].items()}
    result["pairs"] = {k: rename(row) for k, row in result["pairs"].items()}
    result["totals"] = {k: rename(row) if row else row for k, row in result["totals"].items()}
    result["sections"] = {
        k: [{**sec, "rows": [rename(r) for r in sec["rows"]]} for sec in secs]
        for k, secs in result["sections"].items()
    }
    return result


def parse_pairs(table: Tag) -> dict[str, str]:
    """Turn a two-column label/value <table> into {label: value}."""
    pairs = {}
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) == 2:
            pairs[cells[0].get_text(" ", strip=True)] = cells[1].get_text(" ", strip=True)
    return pairs


def find_login_form(html: str) -> Tag | None:
    password_input = BeautifulSoup(html, "html.parser").find("input", attrs={"type": "password"})
    return password_input.find_parent("form") if password_input else None
