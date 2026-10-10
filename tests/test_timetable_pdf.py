import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

from app.timetable.pdf import TimetablePdf

TIMES = ["9:00-10:20", "10:30-11:50", "12:00-13:20"]
DAYS = ["Bazar ertesi", "Chershenbe akhshami", "Chershenbe", "Cume akhshami", "Cume"]
ALT = "alt həftə"
UST = "üst həftə"
EVERY = "hər həftə"


def grid(blocks, times=TIMES) -> Table:
    """The export's grid: the times across the top, and a weekday label down the side that spans its sub-rows.

    `blocks` is a list of (label, sub_rows); each sub-row holds the cell text under each time. A block with several
    sub-rows is split into weeks: its top half is alt, its bottom half is ust.
    """
    rows = [[""] + times]
    spans = []
    for label, sub_rows in blocks:
        start = len(rows)
        for index, cells in enumerate(sub_rows):
            rows.append([label if index == 0 else ""] + cells)
        if len(sub_rows) > 1:
            spans.append(("SPAN", (0, start), (0, len(rows) - 1)))
    table = Table(rows)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), *spans]))
    return table


def timetable_pdf(*pages: tuple[str, Table]) -> bytes:
    """One page per (group code, grid), with the code above the grid as the export prints it."""
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    story = []
    for index, (code, table) in enumerate(pages):
        if index:
            story.append(PageBreak())
        story += [Paragraph(code, styles["Title"]), table]
    SimpleDocTemplate(buffer, pagesize=landscape(A4), topMargin=36).build(story)
    return buffer.getvalue()


def card(teacher: str, course: str, kind: str = "M", room: str = "1-01") -> str:
    return f"{teacher}\n{course}\n{kind}\n{room}"


TYPES = {"M": "Lecture", "S": "Lab"}  # the type letter on a card


def session(day, time, course, kind="M", room="1-01", teacher="Ali Veliyev", week=EVERY):
    return {
        "day": day,
        "time": time,
        "week": week,
        "course": course,
        "type": TYPES.get(kind, ""),
        "room": room,
        "teacher": teacher,
    }


def grid_row(number: int, *cells: str) -> dict[str, str]:
    """A grid row: the lesson number, then the cell of each weekday in DAYS order."""
    return {"Dərs": str(number), **dict(zip(DAYS, cells, strict=True))}


def sessions_of(data: bytes, group: str):
    return TimetablePdf(data).lessons(group).sessions


def grid_of(data: bytes, group: str):
    return TimetablePdf(data).lessons(group).grid


def test_both_weeks_are_one_list_and_each_session_says_its_week():
    data = timetable_pdf(
        (
            "M1",
            grid(
                [
                    ("Bazar ertesi", [[card("Aslanov Ramin", "Database", "M", "5-410"), "", card("Verdiyev Turan", "Algorithms", "S", "3-12")]]),
                    ("Chershenbe", [["", card("Kazimov Nail", "Chemistry", "S", "2-01"), ""], ["", "", card("Kazimov Nail", "Physics", "M", "2-02")]]),
                ]
            ),
        )
    )

    assert sessions_of(data, "M1") == [
        session("Bazar ertesi", "9:00-10:20", "Database", "M", "5-410", "Aslanov Ramin"),
        session("Bazar ertesi", "12:00-13:20", "Algorithms", "S", "3-12", "Verdiyev Turan"),
        session("Chershenbe", "10:30-11:50", "Chemistry", "S", "2-01", "Kazimov Nail", week=ALT),
        session("Chershenbe", "12:00-13:20", "Physics", "M", "2-02", "Kazimov Nail", week=UST),
    ]


def test_a_lesson_that_fills_its_day_is_in_every_week():
    data = timetable_pdf(("M1", grid([("Cume", [[card("Kazimov Nail", "Bio", "M", "2-02"), "", ""]])])))

    assert sessions_of(data, "M1") == [session("Cume", "9:00-10:20", "Bio", "M", "2-02", "Kazimov Nail")]


def test_cards_in_the_same_half_are_separate_sessions_in_order():
    data = timetable_pdf(
        (
            "M1",
            grid(
                [
                    (
                        "Cume",
                        [
                            [card("Ali Veliyev", "Bio"), "", ""],
                            [card("Ali Veliyev", "Art"), "", ""],
                            [card("Ali Veliyev", "Chem"), "", ""],
                            [card("Ali Veliyev", "Phys"), "", ""],
                        ],
                    )
                ]
            ),
        )
    )

    assert [(row["course"], row["week"]) for row in sessions_of(data, "M1")] == [
        ("Bio", ALT),
        ("Art", ALT),
        ("Chem", UST),
        ("Phys", UST),
    ]


def test_question_marks_are_read_as_schwa_and_other_text_is_kept():
    data = timetable_pdf(("M1", grid([("Bazar ertesi", [[card("N?sirov Ilqar", "Müasir proqram t?minati", "S", "3-401"), "", ""]])])))

    assert sessions_of(data, "M1")[0] == session(
        "Bazar ertesi", "9:00-10:20", "Müasir proqram təminati", "S", "3-401", "Nəsirov Ilqar"
    )


def test_schwa_is_capital_only_where_the_word_is_capitalised():
    data = timetable_pdf(
        ("M1", grid([("Bazar ertesi", [[card("?liyeva Nail?", "Kriptoqrafiyanin ?saslari", "S", "2-01"), "", ""]])])),
        ("M2", grid([("Bazar ertesi", [[card("GÖVH?R Musa", "?sasli riyaziyyat", "M", "2-02"), "", ""]])])),
    )

    assert sessions_of(data, "M1")[0] == session(
        "Bazar ertesi", "9:00-10:20", "Kriptoqrafiyanin əsaslari", "S", "2-01", "Əliyeva Nailə"
    )
    assert sessions_of(data, "M2")[0] == session("Bazar ertesi", "9:00-10:20", "Əsasli riyaziyyat", "M", "2-02", "GÖVHƏR Musa")
    assert grid_of(data, "M1")[0]["Bazar ertesi"] == "Əliyeva Nailə Kriptoqrafiyanin əsaslari S 2-01"


def test_a_cramped_card_with_no_room_still_has_its_type_letter():
    data = timetable_pdf(("M1", grid([("Bazar ertesi", [[card("N?sirov Ilqar", "Müasir proqram", "S", ""), "", ""]])])))

    assert sessions_of(data, "M1")[0] == session("Bazar ertesi", "9:00-10:20", "Müasir proqram", "S", "", "Nəsirov Ilqar")


def test_a_card_without_a_teacher_line_keeps_its_group_codes_in_the_subject():
    joint = "M1/M2/\nM3\nRME Metodlar\nM\n5-K-401"
    data = timetable_pdf(("M1", grid([("Bazar ertesi", [[joint, "", ""]])])))

    assert sessions_of(data, "M1")[0] == session("Bazar ertesi", "9:00-10:20", "M1/M2/ M3 RME Metodlar", "M", "5-K-401", "")


def test_group_code_must_match_exactly_but_ignores_case():
    data = timetable_pdf(
        ("M10", grid([("Bazar ertesi", [[card("Ali Veliyev", "Chem"), "", ""]])])),
        ("M1", grid([("Bazar ertesi", [[card("Ali Veliyev", "Math"), "", ""]])])),
    )
    pdf = TimetablePdf(data)

    assert pdf.has_group("M1") and pdf.has_group("m1")
    assert pdf.lessons("M1").sessions[0]["course"] == "Math"
    assert pdf.lessons("m1").sessions[0]["course"] == "Math"
    assert pdf.lessons("M10").sessions[0]["course"] == "Chem"


def test_a_group_with_pages_in_two_places_is_merged_and_empty_pages_are_skipped():
    data = timetable_pdf(
        ("M2", grid([("Bazar ertesi", [["", "", ""]])])),
        ("M2", grid([("Bazar ertesi", [["", "", card("Ali Veliyev", "Chem")]])])),
    )

    assert [row["course"] for row in sessions_of(data, "M2")] == ["Chem"]


def test_unknown_group_is_not_in_the_pdf():
    pdf = TimetablePdf(timetable_pdf(("M1", grid([("Bazar ertesi", [[card("Ali Veliyev", "Math"), "", ""]])]))))

    assert not pdf.has_group("X9")
    assert pdf.lessons("X9") is None


def test_group_with_no_lessons_has_no_timetable():
    pdf = TimetablePdf(timetable_pdf(("M1", grid([("Bazar ertesi", [["", "", ""]])]))))

    assert pdf.has_group("M1")
    assert pdf.lessons("M1") is None


def test_a_weekday_it_does_not_know_is_left_out():
    data = timetable_pdf(
        ("M1", grid([("Shenbe", [[card("Ali Veliyev", "Lost"), "", ""]]), ("Cume", [[card("Ali Veliyev", "Bio"), "", ""]])]))
    )

    lessons = sessions_of(data, "M1")

    assert [row["day"] for row in lessons] == ["Cume"]
    assert "Lost" not in [row["course"] for row in lessons]


def test_grid_has_a_row_for_every_lesson_time_and_a_column_for_every_weekday():
    data = timetable_pdf(
        (
            "M1",
            grid(
                [
                    ("Bazar ertesi", [[card("Aslanov Ramin", "Database", "M", "5-410"), "", ""]]),
                    ("Cume", [["", "", card("Ali Veliyev", "Bio", "S", "2-02")]]),
                ]
            ),
        )
    )

    assert grid_of(data, "M1") == [
        grid_row(1, "Aslanov Ramin Database M 5-410", "", "", "", ""),
        grid_row(2, "", "", "", "", ""),
        grid_row(3, "", "", "", "", "Ali Veliyev Bio S 2-02"),
    ]


def test_grid_says_which_week_a_card_is_in_unless_it_is_in_every_week():
    data = timetable_pdf(
        (
            "M1",
            grid([("Chershenbe", [["", card("Kazimov Nail", "Chemistry", "S", "2-01"), ""], ["", "", card("Kazimov Nail", "Physics", "M", "2-02")]])]),
        )
    )

    assert grid_of(data, "M1") == [
        grid_row(1, "", "", "", "", ""),
        grid_row(2, "", "", "alt həftə: Kazimov Nail Chemistry S 2-01", "", ""),
        grid_row(3, "", "", "üst həftə: Kazimov Nail Physics M 2-02", "", ""),
    ]


def test_grid_cell_joins_its_cards_and_marks_each_week():
    data = timetable_pdf(
        (
            "M1",
            grid(
                [
                    (
                        "Cume",
                        [
                            [card("Ali Veliyev", "Bio"), "", ""],
                            [card("Ali Veliyev", "Art"), "", ""],
                            [card("Ali Veliyev", "Chem"), "", ""],
                            [card("Ali Veliyev", "Phys"), "", ""],
                        ],
                    )
                ]
            ),
        )
    )

    assert grid_of(data, "M1")[0]["Cume"] == (
        "alt həftə: Ali Veliyev Bio M 1-01 / alt həftə: Ali Veliyev Art M 1-01 / "
        "üst həftə: Ali Veliyev Chem M 1-01 / üst həftə: Ali Veliyev Phys M 1-01"
    )
