"""Read a group's lessons from the timetable PDFs posted to the channel.

Each PDF has one page per group. The group code is printed above the grid; the grid has the lesson times across the top
and the weekdays down the side. A weekday's rows hold its sessions as cards: the teacher, the subject, a type letter and
the room, one per line, teacher first.

The university runs two weeks in turn, alt həftə and üst həftə. In a weekday's block, a card in the top half is alt
həftə, a card in the bottom half is üst həftə, and a card that fills the whole block is in every week. The two weeks are
merged into one set of lessons: each session says which week it is in.

The lessons come in two views, both from the same cards: `sessions` has one row per card, and `grid` has one row per
lesson time with a column per weekday, as the university shows its timetable.

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

LESSON = "Dərs"  # the grid's lesson-number column, as the university names it
WEEKS = ("alt", "ust")  # alt həftə, üst həftə
EVERY_WEEK = "hər həftə"
WEEK_LABELS = {("alt",): "alt həftə", ("ust",): "üst həftə", WEEKS: EVERY_WEEK}  # what a card's weeks are called
WEEKDAYS = ("Bazar ertesi", "Chershenbe akhshami", "Chershenbe", "Cume akhshami", "Cume")  # as the export prints them
WEEKDAY_INDEX = {day.lower(): index for index, day in enumerate(WEEKDAYS)}
TITLE_BAND_POINTS = 60  # the group code is printed in the top of the page, above the grid
EDGE_POINTS = 2  # slack when deciding whether a card fills its weekday's block
TITLE = re.compile(r"^[A-Za-z][A-Za-z0-9\-]*\d[A-Za-z0-9\-]*$")  # e.g. M155a4
TIME = re.compile(r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}")
ROOM_END = re.compile(r"\s*(?P<room>\d-[\w-]+)$")  # closes a card, e.g. "5-K-401"
KIND_END = re.compile(r"\s*(?<!\w)(?P<kind>[MS])$")  # the type letter, which comes before the room or ends a cramped card
KINDS = {"M": "Lecture", "S": "Lab"}  # what the type letter means


@dataclass(frozen=True)
class Lessons:
    sessions: list[dict[str, str]]  # one row per session, in weekday and lesson order; `week` says which week it is in
    grid: list[dict[str, str]]  # one row per lesson time, numbered from the first; one column per weekday

    def rows(self, view: str) -> list[dict[str, str]]:
        return self.grid if view == "grid" else self.sessions


class _Card(NamedTuple):
    order: tuple[int, int]  # weekday index, then the lesson's start in minutes
    weeks: tuple[str, ...]  # the weeks the card is in
    session: dict[str, str]
    text: str  # the card as printed, on one line


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
        """The group's lessons from both weeks, in both views. None when the group has no lessons in this PDF.

        A group can have more than one page with its code; their lessons are merged.
        """
        pages = []
        with pdfplumber.open(io.BytesIO(self._data)) as pdf:
            for index in self._pages.get(_code(group), []):
                page = _grid(pdf.pages[index])
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


def _grid(page) -> tuple[list[str], list[_Card]] | None:
    """The lesson times of a page's timetable, and its cards. None when the page has no cards."""
    for table in page.find_tables():
        raw = table.extract()
        cells = [[_clean(cell) for cell in row] for row in raw]
        if not cells or not any(TIME.match(cell) for cell in cells[0]):
            continue
        times = {index: cell.split()[0] for index, cell in enumerate(cells[0]) if TIME.match(cell)}
        rows = table.rows
        cards = []
        day = None  # (label as printed, weekday index) of the current block
        block = None  # top and bottom of the current block
        for r in range(1, len(rows)):
            if cells[r][0]:
                index = WEEKDAY_INDEX.get(cells[r][0].lower())
                if index is None:
                    log.warning("Unknown weekday %r in the timetable; its lessons are skipped", cells[r][0])
                day = None if index is None else (cells[r][0], index)
                label = rows[r].cells[0] or rows[r].bbox
                block = (label[1], label[3])
            if day is None:
                continue
            for column, time in times.items():
                box = rows[r].cells[column]
                if box is None or not cells[r][column]:
                    continue  # empty, or part of a card that starts in an earlier row
                weeks = _weeks_of(box, block)
                teacher, course, letter, room = _card(raw[r][column] or "")
                session = {
                    "day": day[0],
                    "time": time,
                    "week": WEEK_LABELS[weeks],
                    "course": course,
                    "type": KINDS.get(letter, ""),
                    "room": room,
                    "teacher": teacher,
                }
                text = " ".join(part for part in (teacher, course, letter, room) if part)
                cards.append(_Card((day[1], _start_minutes(time)), weeks, session, text))
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
    """The lessons of all the group's pages, in both views."""
    slots = sorted({time for times, _ in pages for time in times}, key=_start_minutes)
    cards = sorted((card for _, page_cards in pages for card in page_cards), key=lambda card: card.order)
    return Lessons(sessions=[card.session for card in cards], grid=_grid_rows(cards, slots))


def _grid_rows(cards: list[_Card], slots: list[str]) -> list[dict[str, str]]:
    """One row per lesson time, with the cards of each weekday in its column, as printed. Two cards in one cell are joined."""
    rows = []
    for number, time in enumerate(slots, start=1):
        row = {LESSON: str(number)}
        for index, day in enumerate(WEEKDAYS):
            row[day] = " / ".join(
                _grid_text(card) for card in cards if card.order[0] == index and card.session["time"] == time
            )
        rows.append(row)
    return rows


def _grid_text(card: _Card) -> str:
    """The card as printed. A card in one week only starts with that week, so the merged grid says which week it is in."""
    if card.weeks == WEEKS:
        return card.text
    return f"{WEEK_LABELS[card.weeks]}: {card.text}"


def _start_minutes(time: str) -> int:
    hours, minutes = time.split("-")[0].split(":")
    return int(hours) * 60 + int(minutes)


def _code(text: str) -> str:
    return re.sub(r"\s+", "", text).upper()


def _clean(cell: str | None) -> str:
    return " ".join((cell or "").split())
