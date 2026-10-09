from dataclasses import dataclass, field


@dataclass(frozen=True)
class Section:
    """A repeating block (e.g. one card per semester) holding a title and a table."""

    container: str  # CSS selector matching every block
    title: str  # selector for the title, relative to the block
    table: str  # selector for the table, relative to the block


@dataclass(frozen=True)
class Target:
    name: str
    path: str  # relative to DASHBOARD_URL, or a full URL on any subdomain of BASE_DOMAIN
    tables: dict[str, str] = field(default_factory=dict)  # table name -> CSS selector; parsed into records
    pairs: dict[str, str] = field(default_factory=dict)  # table name -> CSS selector; parsed as label/value rows
    totals: dict[str, str] = field(default_factory=dict)  # table name -> first-cell label of its total row, split out of the rows
    sections: dict[str, Section] = field(default_factory=dict)  # name -> repeating titled tables
    fields: dict[str, str] = field(default_factory=dict)  # output key -> CSS selector (first match)
    pjax: bool = True  # send pjax headers; inner dashboard pages redirect away without them


TARGETS: list[Target] = [
    Target(name="student", path="/telebe/info/student_view.php", pairs={"info": "table.table-condensed"}),
    Target(
        name="scores",
        path="/telebe/score_view.php",
        tables={
            "student": "table#op_list:has(th:-soup-contains('Fakültə'))",
            "semesters": "table#datatable-buttons",
        },
        totals={"semesters": "Toplam"},
        sections={
            "semester_courses": Section(
                container="div.card:has(> .card-body > h5.text-primary)",
                title="h5.text-primary",
                table="table",
            ),
        },
    ),
    Target(name="schedule", path="/studies/lecture_time.php", tables={"timetable": "table#op_list"}),
    Target(name="notices", path="/studies/notice.php", tables={"notices": "table#op_list"}),
]


# Site label (Azerbaijani) -> output key. Labels not listed here are returned unchanged.
FIELD_MAP: dict[str, str] = {
    # student info
    "İdentifikator": "student_id",
    "İmtahan parolu": "exam_password",
    "Soyadı": "last_name",
    "Adı": "first_name",
    "Atasının adı": "father_name",
    "Şəxsiyyət vəsiqəsinin seriya və nömrəsi": "id_card_number",
    "Cinsi": "gender",
    "Təhsil növü": "education_type",
    "İngilis dili adı": "english_name",
    "Telefon": "phone",
    "Ünvanı": "address",
    "Mobil telefon": "mobile_phone",
    "Təhsil forması": "study_form",
    "Bölməsi": "language_track",
    "Fakültənin adı": "faculty",
    "Kafedranın adı": "department",
    "İxtisas adı": "major",
    "İxtisaslaşma": "specialization",
    "Doğum tarixi": "birth_date",
    "Kurs": "year_of_study",
    "Statusu": "status",
    "Status": "status",
    "Orta məktəb": "high_school",
    "Orta məktəbi bitirdiyi tarix": "high_school_graduation_date",
    "Qəbul olma tarixi": "admission_date",
    "Bitirdiyi tarix": "graduation_date",
    # scores: student header
    "Fakültə / Kafedra": "faculty_department",
    # scores: semesters
    "Il / semester": "semester",
    "Ümumi fənnlər": "total_courses",
    "Dinlənmiş fənnlər": "attended_courses",
    "Umumi kredit": "total_credits",
    "Alınmış kredit": "earned_credits",
    "Yekun orta bal": "final_average",
    # scores: courses
    "Fənn növü": "course_type",
    "Fənnlər": "course",
    "Kredit": "credits",
    "Yekun bal": "final_score",
    "Dərəcə": "grade",
    "Təkrar dərs": "retake",
    # notices
    "Nömrə": "number",
    "Bölmə": "section",
    "Movzu": "subject",
    "Müəllif": "author",
    "Tərtib tarixi": "created_at",
    "Müraciətlərin sayı": "views",
}


TARGETS_BY_NAME: dict[str, Target] = {target.name: target for target in TARGETS}
