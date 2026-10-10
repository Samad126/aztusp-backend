import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

from app.timetable.pdf import TimetablePdf

TIMES = ["9:00-10:20", "10:30-11:50", "12:00-13:20"]
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]  # the output's names
LOWER = "lower"  # alt həftə
UPPER = "upper"  # üst həftə
BOTH = "both"  # every week


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


TYPES = {"M": "lecture", "S": "lab"}  # the type letter on a card


def session(day, time, course, kind="M", room="1-01", teacher="Ali Veliyev", week=BOTH):
    return {
        "day": day,
        "time": time,
        "week": week,
        "course": course,
        "type": TYPES.get(kind, ""),
        "room": room,
        "teacher": teacher,
    }


def grid_card(teacher: str, course: str, kind: str = "M", room: str = "1-01", week: str = BOTH) -> dict[str, str]:
    return {"type": TYPES.get(kind, ""), "week": week, "course": course, "teacher": teacher, "room": room}


def sessions_of(data: bytes, group: str):
    return TimetablePdf(data).lessons(group).sessions


def grid_of(data: bytes, group: str):
    return TimetablePdf(data).lessons(group).grid


def cards_in(data: bytes, group: str, day: str, time: str):
    """The cards in one cell of the grid: the weekday's row, at the lesson time."""
    row = next(row for row in grid_of(data, group)["days"] if row["day"] == day)
    return next(lesson["cards"] for lesson in row["lessons"] if lesson["time"] == time)


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
        session("monday", "9:00-10:20", "Database", "M", "5-410", "Aslanov Ramin"),
        session("monday", "12:00-13:20", "Algorithms", "S", "3-12", "Verdiyev Turan"),
        session("wednesday", "10:30-11:50", "Chemistry", "S", "2-01", "Kazimov Nail", week=LOWER),
        session("wednesday", "12:00-13:20", "Physics", "M", "2-02", "Kazimov Nail", week=UPPER),
    ]


def test_a_lesson_that_fills_its_day_is_in_both_weeks():
    data = timetable_pdf(("M1", grid([("Cume", [[card("Kazimov Nail", "Bio", "M", "2-02"), "", ""]])])))

    assert sessions_of(data, "M1") == [session("friday", "9:00-10:20", "Bio", "M", "2-02", "Kazimov Nail")]


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
        ("Bio", LOWER),
        ("Art", LOWER),
        ("Chem", UPPER),
        ("Phys", UPPER),
    ]


def test_question_marks_are_read_as_schwa_and_other_text_is_kept():
    data = timetable_pdf(("M1", grid([("Bazar ertesi", [[card("N?sirov Ilqar", "Müasir proqram t?minati", "S", "3-401"), "", ""]])])))

    assert sessions_of(data, "M1")[0] == session(
        "monday", "9:00-10:20", "Müasir proqram təminati", "S", "3-401", "Nəsirov Ilqar"
    )


def test_schwa_is_capital_only_where_the_word_is_capitalised():
    data = timetable_pdf(
        ("M1", grid([("Bazar ertesi", [[card("?liyeva Nail?", "Kriptoqrafiyanin ?saslari", "S", "2-01"), "", ""]])])),
        ("M2", grid([("Bazar ertesi", [[card("GÖVH?R Musa", "?sasli riyaziyyat", "M", "2-02"), "", ""]])])),
    )

    assert sessions_of(data, "M1")[0] == session(
        "monday", "9:00-10:20", "Kriptoqrafiyanin əsaslari", "S", "2-01", "Əliyeva Nailə"
    )
    assert sessions_of(data, "M2")[0] == session("monday", "9:00-10:20", "Əsasli riyaziyyat", "M", "2-02", "GÖVHƏR Musa")
    assert cards_in(data, "M1", "monday", "9:00-10:20") == [grid_card("Əliyeva Nailə", "Kriptoqrafiyanin əsaslari", "S", "2-01")]


def test_a_cramped_card_with_no_room_still_has_its_type_letter():
    data = timetable_pdf(("M1", grid([("Bazar ertesi", [[card("N?sirov Ilqar", "Müasir proqram", "S", ""), "", ""]])])))

    assert sessions_of(data, "M1")[0] == session("monday", "9:00-10:20", "Müasir proqram", "S", "", "Nəsirov Ilqar")


def test_a_card_without_a_teacher_line_keeps_its_group_codes_in_the_subject():
    joint = "M1/M2/\nM3\nRME Metodlar\nM\n5-K-401"
    data = timetable_pdf(("M1", grid([("Bazar ertesi", [[joint, "", ""]])])))

    assert sessions_of(data, "M1")[0] == session("monday", "9:00-10:20", "M1/M2/ M3 RME Metodlar", "M", "5-K-401", "")


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

    assert [row["day"] for row in lessons] == ["friday"]
    assert "Lost" not in [row["course"] for row in lessons]


def test_grid_has_a_row_per_weekday_in_english_and_a_cell_per_lesson_time():
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
    grid_layout = grid_of(data, "M1")

    assert grid_layout["times"] == TIMES
    assert [row["day"] for row in grid_layout["days"]] == DAYS
    for row in grid_layout["days"]:
        assert [lesson["time"] for lesson in row["lessons"]] == TIMES


def test_grid_cell_holds_its_cards_with_type_week_teacher_and_room():
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

    assert cards_in(data, "M1", "monday", "9:00-10:20") == [grid_card("Aslanov Ramin", "Database", "M", "5-410")]
    assert cards_in(data, "M1", "friday", "12:00-13:20") == [grid_card("Ali Veliyev", "Bio", "S", "2-02")]
    assert cards_in(data, "M1", "friday", "9:00-10:20") == []


def test_grid_cards_say_which_week_they_are_in():
    data = timetable_pdf(
        (
            "M1",
            grid([("Chershenbe", [["", card("Kazimov Nail", "Chemistry", "S", "2-01"), ""], ["", "", card("Kazimov Nail", "Physics", "M", "2-02")]])]),
        )
    )

    assert cards_in(data, "M1", "wednesday", "10:30-11:50") == [grid_card("Kazimov Nail", "Chemistry", "S", "2-01", week=LOWER)]
    assert cards_in(data, "M1", "wednesday", "12:00-13:20") == [grid_card("Kazimov Nail", "Physics", "M", "2-02", week=UPPER)]


def test_grid_cell_holds_every_card_of_its_lesson_in_order():
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
    cell = cards_in(data, "M1", "friday", "9:00-10:20")

    assert [(item["course"], item["week"]) for item in cell] == [
        ("Bio", LOWER),
        ("Art", LOWER),
        ("Chem", UPPER),
        ("Phys", UPPER),
    ]
