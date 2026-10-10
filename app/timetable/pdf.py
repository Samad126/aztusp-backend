"""Read a group's lessons from the timetable PDFs posted to the channel.

Each PDF has one page per group. The group code is printed above the grid; the grid has the lesson times across the top
and the weekdays down the side. A weekday's rows hold its sessions as cards: the teacher, the subject, a type letter and
the room, one per line, teacher first.

The university runs two weeks in turn, alt həftə and üst həftə. In a weekday's block, a card in the top half is alt
həftə, a card in the bottom half is üst həftə, and a card that fills the whole block is in both weeks. The two weeks are
merged into one set of lessons: each card says which week it is in.

The lessons come in two layouts, both from the same cards: `sessions` has one row per card, and `grid` is the university's
layout, one row per weekday with a cell per lesson time that lists the cards in it. Both use the same values: weekdays in
English lowercase, the type as `lecture` or `lab`, and the week as `lower` (alt), `upper` (üst) or `both`.

The export writes ə as '?', so a '?' is read as ə: as Ə where its word is capitalised (a name, the start of a subject, or a
word in capitals). The export does not mark ş, ç, ı or ğ, so those come out as plain letters (masin for maşın) and stay
that way. Other text is kept as the export prints it. The export wraps long words across lines, and a wrap comes out as a
space, so a word split by a wrap is not joined back together.
"""

import io
import logging
import re
from dataclasses import dataclass
from typing import NamedTuple

import pdfplumber
import pypdfium2 as pdfium

log = logging.getLogger(__name__)

WEEKS = ("alt", "ust")  # alt həftə, üst həftə
WEEK_VALUES = {("alt",): "lower", ("ust",): "upper", WEEKS: "both"}  # the week of a card, as the output names it
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")  # the output's name for each weekday, in the export's order
WEEKDAYS = ("Bazar ertesi", "Chershenbe akhshami", "Chershenbe", "Cume akhshami", "Cume")  # as the export prints them
WEEKDAY_INDEX = {day.lower(): index for index, day in enumerate(WEEKDAYS)}
TITLE_BAND_POINTS = 60  # the group code is printed in the top of the page, above the grid
EDGE_POINTS = 2  # slack when deciding whether a card fills its weekday's block
TITLE = re.compile(r"^[A-Za-z][A-Za-z0-9\-]*\d[A-Za-z0-9\-]*$")  # e.g. M155a4
TIME = re.compile(r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}")
ROOM_END = re.compile(r"\s*(?P<room>\d-[\w-]+)$")  # closes a card, e.g. "5-K-401"
KIND_END = re.compile(r"\s*(?<!\w)(?P<kind>[MS])$")  # the type letter, which comes before the room or ends a cramped card
KINDS = {"M": "lecture", "S": "lab"}  # what the type letter means
CARD_KEYS = ("type", "week", "course", "teacher", "room")  # what a card holds in the grid


@dataclass(frozen=True)
class Lessons:
    sessions: list[dict[str, str]]  # the list layout: one row per session, in weekday and lesson order
    grid: dict  # the grid layout: the lesson times, and a row per weekday (see `_grid`)


class _Card(NamedTuple):
    order: tuple[int, int]  # weekday index, then the lesson's start in minutes
    session: dict[str, str]


class TimetablePdf:
    """One timetable PDF. Its pages are indexed by group code when it is opened; a group's lessons are read on request."""

    def __init__(self, data: bytes):
        self._data = data
        self._pages: dict[str, list[int]] = {}
        for index, title in enumerate(_page_titles(data)):
            if title:
                self._pages.setdefault(_code(title), []).append(index)

    def has_group(self, group: str) -> bool:
        return _code(group) in self._pages

    def lessons(self, group: str) -> Lessons | None:
        """The group's lessons from both weeks, in both layouts. None when the group has no lessons in this PDF.

        A group can have more than one page with its code; their lessons are merged.
        """
        pages = []
        with pdfplumber.open(io.BytesIO(self._data)) as pdf:
            for index in self._pages.get(_code(group), []):
                page = _page_lessons(pdf.pages[index])
                if page is not None:
                    pages.append(page)
        return _lessons(pages) if pages else None


def _page_titles(data: bytes) -> list[str]:
    """The group code printed above each page's grid, or '' where there is none. Only the text is read, so it is quick."""
    titles = []
    document = pdfium.PdfDocument(data)
    try:
        for page in document:
            height = page.get_height()
            band = page.get_textpage().get_text_bounded(0, height - TITLE_BAND_POINTS, page.get_width(), height)
            titles.append(next((word for word in band.split() if TITLE.match(word)), ""))
    finally:
        document.close()
    return titles


def _page_lessons(page) -> tuple[list[str], list[_Card]] | None:
    """The lesson times of a page's timetable, and its cards. None when the page has no cards."""
    for table in page.find_tables():
        raw = table.extract()
        cells = [[_clean(cell) for cell in row] for row in raw]
        if not cells or not any(TIME.match(cell) for cell in cells[0]):
            continue
        times = {index: cell.split()[0] for index, cell in enumerate(cells[0]) if TIME.match(cell)}
        rows = table.rows
        cards = []
        day = None  # weekday index of the current block
        block = None  # top and bottom of the current block
        for r in range(1, len(rows)):
            if cells[r][0]:
                day = WEEKDAY_INDEX.get(cells[r][0].lower())
                if day is None:
                    log.warning("Unknown weekday %r in the timetable; its lessons are skipped", cells[r][0])
                label = rows[r].cells[0] or rows[r].bbox
                block = (label[1], label[3])
            if day is None:
                continue
            for column, time in times.items():
                box = rows[r].cells[column]
                if box is None or not cells[r][column]:
                    continue  # empty, or part of a card that starts in an earlier row
                teacher, course, letter, room = _card(raw[r][column] or "")
                session = {
                    "day": DAYS[day],
                    "time": time,
                    "week": WEEK_VALUES[_weeks_of(box, block)],
                    "course": course,
                    "type": KINDS.get(letter, ""),
                    "room": room,
                    "teacher": teacher,
                }
                cards.append(_Card((day, _start_minutes(time)), session))
        if cards:
            return list(times.values()), cards
    return None


def _card(raw: str) -> tuple[str, str, str, str]:
    """The teacher, subject, type letter (M or S) and room of one card. A card with no teacher line starts with its group
    codes."""
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    teacher = ""
    if len(lines) > 1 and _is_name(lines[0]):
        teacher = _schwa(_clean(lines.pop(0)), every_word=True)
    text = " ".join(lines)
    room = ""
    match = ROOM_END.search(text)
    if match:
        room = match.group("room")
        text = text[: match.start()]
    letter = ""
    match = KIND_END.search(text)
    if match:
        letter = match.group("kind")
        text = text[: match.start()]
    return teacher, _schwa(_clean(text), every_word=False), letter, room


def _schwa(text: str, every_word: bool) -> str:
    """Reads the export's '?' as ə, or as Ə where its word is capitalised: in a word written in capitals, at the start of
    a name (`every_word`), or at the very start of a subject. Elsewhere the export's case is not known, so it is ə."""

    def read(word: re.Match) -> str:
        token = word.group()
        letters = [char for char in token if char.isalpha()]
        in_capitals = len(letters) > 1 and all(char.isupper() for char in letters)
        out = []
        for index, char in enumerate(token):
            if char != "?":
                out.append(char)
            elif in_capitals or (index == 0 and (every_word or word.start() == 0)):
                out.append("Ə")
            else:
                out.append("ə")
        return "".join(out)

    return re.sub(r"\S+", read, text)


def _is_name(line: str) -> bool:
    return len(line.split()) >= 2 and not re.search(r"[\d/]", line)


def _weeks_of(box, block) -> tuple[str, ...]:
    """Which weeks a card is in: the top half of its weekday's block is alt, the bottom half is ust."""
    top, bottom = box[1], box[3]
    block_top, block_bottom = block
    if top <= block_top + EDGE_POINTS and bottom >= block_bottom - EDGE_POINTS:
        return WEEKS
    middle = (block_top + block_bottom) / 2
    return ("alt",) if (top + bottom) / 2 < middle else ("ust",)


def _lessons(pages: list[tuple[list[str], list[_Card]]]) -> Lessons:
    """The lessons of all the group's pages, in both layouts."""
    slots = sorted({time for times, _ in pages for time in times}, key=_start_minutes)
    cards = sorted((card for _, page_cards in pages for card in page_cards), key=lambda card: card.order)
    return Lessons(sessions=[card.session for card in cards], grid=_grid(cards, slots))


def _grid(cards: list[_Card], slots: list[str]) -> dict:
    """The university's layout: the lesson times across the top, and a row per weekday. Each cell lists the cards of that
    lesson, from both weeks; a card says which week it is in."""
    return {
        "times": slots,
        "days": [
            {
                "day": day,
                "lessons": [
                    {
                        "time": time,
                        "cards": [
                            {key: card.session[key] for key in CARD_KEYS}
                            for card in cards
                            if card.order[0] == index and card.session["time"] == time
                        ],
                    }
                    for time in slots
                ],
            }
            for index, day in enumerate(DAYS)
        ],
    }


def _start_minutes(time: str) -> int:
    hours, minutes = time.split("-")[0].split(":")
    return int(hours) * 60 + int(minutes)


def _code(text: str) -> str:
    return re.sub(r"\s+", "", text).upper()


def _clean(cell: str | None) -> str:
    return " ".join((cell or "").split())
