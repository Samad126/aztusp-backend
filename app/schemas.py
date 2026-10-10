"""Request and response models for the HTTP API."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .watcher.config import DEFAULT_FIELDS, WATCHABLE_FIELDS


class Detail(BaseModel):
    detail: str = Field(description="Human-readable error message.")


class Credentials(BaseModel):
    username: str = Field(description="Site username (the same one used on the student portal).", examples=["M0000000000"])
    password: str = Field(description="Site password. Used to sign in; this endpoint does not store it.")


class TokenResponse(BaseModel):
    token: str = Field(
        description="API token. Send it as `Authorization: Bearer <token>`. Shown only once. It expires after 1 day, then log in again."
    )


class OkResponse(BaseModel):
    ok: bool = True


EMAIL_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class NotificationSettings(BaseModel):
    email: str | None = Field(
        None,
        description="Address that result changes are mailed to. Optional when Telegram is connected.",
        examples=["student@example.com"],
    )
    fields: list[str] = Field(
        default_factory=lambda: list(DEFAULT_FIELDS),
        description=f"Result fields to watch, one or more of: {', '.join(WATCHABLE_FIELDS)}.",
    )

    @field_validator("email")
    @classmethod
    def email_looks_right(cls, value: str | None) -> str | None:
        if not value:
            return None
        if not EMAIL_ADDRESS.match(value):
            raise ValueError("must be an email address")
        return value

    @model_validator(mode="after")
    def known_fields(self):
        if not self.fields or any(name not in WATCHABLE_FIELDS for name in self.fields):
            raise ValueError(f"fields must be one or more of: {', '.join(WATCHABLE_FIELDS)}")
        return self


class NotificationsIn(NotificationSettings):
    model_config = ConfigDict(json_schema_extra={"example": {"email": "student@example.com", "fields": ["final_score", "grade"]}})


class NotificationsOut(NotificationSettings):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "student@example.com",
                "fields": ["final_score", "grade"],
                "telegram_linked": True,
                "status": "ok",
                "last_checked_at": "2026-10-10T11:30:00Z",
            }
        }
    )

    telegram_linked: bool = Field(description="Whether a Telegram chat is connected (see `/me/telegram/link`).")
    status: Literal["ok", "wrong_password", "error"] = Field(
        description="`ok`: the last check worked. `wrong_password`: the site rejected the saved password, so checks are paused "
        "until you log in again. `error`: the last check failed, usually because the site is down."
    )
    last_checked_at: datetime | None = Field(None, description="When the last check ran; `null` before the first one.")


class TelegramLink(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"url": "https://t.me/AztuGradeBot?start=Qk3v9xR2mLp8WbT1sYc4Zg", "expires_at": "2026-10-10T11:45:00Z"}}
    )

    url: str = Field(description="Open this on a phone or desktop. Pressing Start in Telegram connects the chat to your account.")
    expires_at: datetime = Field(description="The link works once and stops working at this time.")


class TelegramStatus(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"linked": True}})

    linked: bool = Field(description="Whether a Telegram chat is connected to your account.")


class Section(BaseModel):
    title: str | None = Field(description="Heading of the block, e.g. the semester name.")
    rows: list[dict[str, str]]


class ScrapeResult(BaseModel):
    name: str = Field(description="Target name.")
    url: str = Field(description="Page that was scraped.")
    tables: dict[str, list[dict[str, str]]] = Field(
        description="Tables parsed into records, keyed by table name. Column names are translated to English where known."
    )
    pairs: dict[str, dict[str, str]] = Field(description="Two-column label/value tables, keyed by table name.")
    totals: dict[str, dict[str, str] | None] = Field(
        description="Total row split out of a table (`null` if the table has none)."
    )
    sections: dict[str, list[Section]] = Field(description="Repeating titled tables, e.g. one per semester.")
    fields: dict[str, str | None] = Field(description="Single text values picked out of the page.")


# --- /me/* pages -----------------------------------------------------------------------------
# These keep the scrape envelope (name, url, tables, pairs, totals, sections, fields) but type what
# is inside it. Unused parts of the envelope are always empty. Cells the site leaves blank are `""`.

Cell = str
Empty = dict[str, str]


class PageEnvelope(BaseModel):
    name: str = Field(description="Target name.")
    url: str = Field(description="Page that was scraped.")


class StudentInfo(BaseModel):
    student_id: Cell = Field("", description="Student id (İdentifikator).")
    exam_password: Cell = Field("", description="Exam password (İmtahan parolu); the site often shows a placeholder.")
    last_name: Cell = ""
    first_name: Cell = ""
    father_name: Cell = ""
    id_card_number: Cell = ""
    gender: Cell = ""
    education_type: Cell = ""
    english_name: Cell = ""
    phone: Cell = ""
    address: Cell = ""
    mobile_phone: Cell = ""
    study_form: Cell = ""
    language_track: Cell = ""
    faculty: Cell = ""
    department: Cell = ""
    major: Cell = ""
    specialization: Cell = ""
    birth_date: Cell = ""
    year_of_study: Cell = ""
    status: Cell = ""
    high_school: Cell = ""
    high_school_graduation_date: Cell = ""
    admission_date: Cell = ""
    graduation_date: Cell = ""


class ProfilePairs(BaseModel):
    info: StudentInfo = Field(default_factory=StudentInfo)


class ProfilePage(PageEnvelope):
    tables: dict[str, list[dict[str, str]]] = Field(description="Always empty.")
    pairs: ProfilePairs = Field(default_factory=ProfilePairs)
    totals: dict[str, dict[str, str] | None] = Field(description="Always empty.")
    sections: dict[str, list[Section]] = Field(description="Always empty.")
    fields: dict[str, str | None] = Field(description="Always empty.")


class ScoresStudentRow(BaseModel):
    faculty_department: Cell = ""
    student_id: Cell = ""
    first_name: Cell = ""
    status: Cell = ""


class SemesterSummary(BaseModel):
    semester: Cell = Field("", description="e.g. `2026/payiz`.")
    total_courses: Cell = ""
    attended_courses: Cell = ""
    total_credits: Cell = ""
    earned_credits: Cell = ""
    final_average: Cell = ""


class ScoresTables(BaseModel):
    student: list[ScoresStudentRow] = []
    semesters: list[SemesterSummary] = Field([], description="One row per semester, without the total row.")


class ScoresTotals(BaseModel):
    semesters: SemesterSummary | None = Field(None, description="The `Toplam` row of the semesters table; `null` if absent.")


class CourseResult(BaseModel):
    course_type: Cell = Field("", description="e.g. `məcburi` (compulsory).")
    course: Cell = ""
    credits: Cell = ""
    column_4: Cell = Field("", description="Column with no title on the site (right after credits).")
    column_5: Cell = Field("", description="Column with no title on the site.")
    final_score: Cell = Field("", description="`Yekun bal`.")
    grade: Cell = Field("", description="`Dərəcə`, e.g. `F`.")
    retake: Cell = Field("", description="`Təkrar dərs`: `Y` or `N`.")


class SemesterCourses(BaseModel):
    title: str | None = Field(description="Semester heading, e.g. `2026 Tədris ili payiz Semestr`.")
    rows: list[CourseResult]


class ScoresSections(BaseModel):
    semester_courses: list[SemesterCourses] = Field([], description="One block per semester, in page order.")


class ScoresPage(PageEnvelope):
    tables: ScoresTables = Field(default_factory=ScoresTables)
    pairs: dict[str, dict[str, str]] = Field(description="Always empty.")
    totals: ScoresTotals = Field(default_factory=ScoresTotals)
    sections: ScoresSections = Field(default_factory=ScoresSections)
    fields: dict[str, str | None] = Field(description="Always empty.")


class ScheduleSections(BaseModel):
    semesters: list[Section] = Field(
        [],
        description=(
            "One block per semester shown on the page, in page order. `title` is the heading "
            "(e.g. `2026İl payiz Semestr Dərs cədvəli`); `rows` are timetable rows keyed by the column titles "
            "(`Dərs`, then one column per weekday). In the channel fallback there is one block per group with both weeks in it, "
            "and one row per session with `day` (`monday` to `friday`), `time`, `week` (`upper`, `lower` or `both`), `course`, "
            "`type` (`lecture` or `lab`), `room` and `teacher` (`view=list`). "
            "Empty when `view=grid`, which fills `grids`. `rows` is empty when no lessons are scheduled."
        ),
    )


class ScheduleCard(BaseModel):
    type: str = Field(description="`lecture` (type M) or `lab` (type S); empty when the export does not say.")
    week: str = Field(description="`upper` (üst həftə) or `lower` (alt həftə) when the lesson is in one week only; `both` when every week.")
    course: str = Field(description="Subject, as the export prints it.")
    teacher: str = Field(description="Teacher; empty when the export does not name one.")
    room: str = Field(description="Room; empty when the export does not show it.")


class ScheduleLesson(BaseModel):
    time: str = Field(description="Lesson time, e.g. `9:00-10:20`.")
    cards: list[ScheduleCard] = Field(description="The lessons in this slot, both weeks together; empty when there are none.")


class ScheduleDay(BaseModel):
    day: str = Field(description="English weekday in lowercase: `monday` to `friday`.")
    lessons: list[ScheduleLesson] = Field(description="One per time in the grid's `times`, in the same order.")


class ScheduleGrid(BaseModel):
    title: str = Field(description="Heading, e.g. `M1 Dərs cədvəli`.")
    times: list[str] = Field(description="The lesson times across the top, in order.")
    days: list[ScheduleDay] = Field(description="One row per weekday, in order.")


class SchedulePage(PageEnvelope):
    tables: dict[str, list[dict[str, str]]] = Field(description="Always empty.")
    pairs: dict[str, dict[str, str]] = Field(description="Always empty.")
    totals: dict[str, dict[str, str] | None] = Field(description="Always empty.")
    sections: ScheduleSections = Field(default_factory=ScheduleSections)
    grids: list[ScheduleGrid] = Field(
        [],
        description=(
            "The channel's timetable in the university's layout, one grid per group, when `view=grid`. "
            "Empty in the other views."
        ),
    )
    fields: dict[str, str | None] = Field(description="Always empty.")


class NoticeRow(BaseModel):
    id: Cell = Field("", description="Notice id; use it with `/me/notices/{notice_id}`. Empty if the row has no detail view.")
    number: Cell = ""
    section: Cell = ""
    subject: Cell = ""
    author: Cell = ""
    created_at: Cell = ""
    views: Cell = Field("", description="`Müraciətlərin sayı`.")


class NoticesTables(BaseModel):
    notices: list[NoticeRow] = []


class NoticesPage(PageEnvelope):
    tables: NoticesTables = Field(default_factory=NoticesTables)
    pairs: dict[str, dict[str, str]] = Field(description="Always empty.")
    totals: dict[str, dict[str, str] | None] = Field(description="Always empty.")
    sections: dict[str, list[Section]] = Field(description="Always empty.")
    fields: dict[str, str | None] = Field(description="Always empty.")


class Course(BaseModel):
    lec_open_idx: str = Field(description="Course id; use it with `/courses/{lec_open_idx}/plan`.")
    sem_code: str | None = Field(description="Semester code the course belongs to.")
    name: str
    path: str = Field(description="Dashboard path of the course page.")


class PlanBlock(BaseModel):
    title: str
    rows: list[dict[str, str]] | None = Field(default=None, description="Present when the block is a table.")
    text: str | None = Field(default=None, description="Present when the block is plain text.")


class CourseItems(BaseModel):
    params: dict[str, str] = Field(description="Ids that identify the course on the site.")
    course: str | None = Field(description="Course heading as shown on the site.")
    items: list[dict[str, str]] = Field(
        description="Rows of the list. Empty if nothing was posted. `id` (when present) identifies the entry on the site; `link` is its first link."
    )


class ScoreComponent(BaseModel):
    name: str
    max: str | None = Field(description="Maximum points, taken from the column title, e.g. `Məşğələ(30)`.")
    score: str | None = Field(description="Points earned; `null` if not graded yet.")


class CourseScores(BaseModel):
    params: dict[str, str] = Field(description="Ids that identify the course on the site.")
    course: str | None
    table: list[dict[str, str]] = Field(description="The score table as shown: column title -> value, empty cells kept as `\"\"`.")
    components: list[ScoreComponent] = Field(description="The same data split into name, maximum and score per column, without the total.")
    total: str | None = Field(description="Total (`Toplam`) as shown on the site.")
    notes: list[str] = Field(description="Remarks printed under the table.")


class AttendanceSession(BaseModel):
    number: str = Field(description="Class meeting number, starting at 1.")
    date: str | None = Field(description="Date of the class (`Dərsin tarixi`); `null` until it has been held.")
    journal_date: str | None = Field(description="Date the teacher filled in the journal (`Jurnalın yazılma tarixi`).")


class AttendanceMark(BaseModel):
    session: str = Field(description="Class meeting number.")
    status: str | None = Field(description="Mark text as shown on the site; `null` if empty.")
    mark: Literal["present", "absent", "not_entered"] | None = Field(description="Mark classified by its colour class on the site.")


class AttendanceStudent(BaseModel):
    number: str
    student_id: str
    name: str
    marks: list[AttendanceMark] = Field(description="One entry per class meeting, in order.")
    score: str | None = Field(description="Points for attendance (`Bal`).")
    percent: str | None = Field(description="Attendance percentage (`Davamiyyət faizi`).")


class CourseAttendance(BaseModel):
    params: dict[str, str] = Field(description="Ids that identify the course on the site.")
    course: str | None
    info: dict[str, str] = Field(description="Course summary: course, total_hours, weekly_hours, credits, group, teacher, period.")
    legend: dict[str, str] = Field(description="Mark codes explained on the page, e.g. `Davamiyyət`: `i/e`, `Mühazirə`: `M`.")
    header: dict[str, str | None] = Field(description="Values printed in the table header under `Bal` and `Davamiyyət faizi`: `score`, `percent`.")
    sessions: list[AttendanceSession]
    students: list[AttendanceStudent] = Field(description="Every student row on the page, in page order.")


class LecturePlan(BaseModel):
    params: dict[str, str] = Field(description="Ids that identify the course on the site.")
    course: str | None
    semester: str | None
    info: dict[str, str] | None = Field(description="Professor, department, credits, hours and weeks.")
    blocks: list[PlanBlock] = Field(description="Sections of the plan, in page order.")


class NoticeAttachment(BaseModel):
    name: str
    url: str = Field(description="Link on the university site. It needs the site session, so use `download` instead.")
    file_no: str = Field("", description="File number within the notice (`wr_fno`).")
    download: str = Field("", description="API path that downloads the file, e.g. `/api/v1/me/notices/34/files/1`. Send the same `Authorization` header.")


class NoticeDetail(BaseModel):
    id: str
    url: str = Field(description="Page that was scraped.")
    subject: str
    author: str = ""
    created_at: str = Field("", description="e.g. `2020-12-13 22:15:12`.")
    views: str = Field("", description="`Müraciətlərin sayı`.")
    attachments: list[NoticeAttachment] = Field([], description="Files attached to the notice (`Qoşma fayl`).")
    body: str = Field("", description="Message text, with line breaks kept.")


# --- /me/password -----------------------------------------------------------------------


class PasswordChangeIn(BaseModel):
    password: str = Field(description="The new site password.")
    confirm_password: str = Field(description="The new password again. Must match `password`.")

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("password and confirm_password must match")
        return self


class PasswordChangeResult(BaseModel):
    changed: bool = Field(description="True when the site answered with its sign-in page and no change form.")
    url: str = Field(description="Page the site answered with, after any redirects.")
    messages: list[str] = Field(description="Alert and error texts on that page. Empty when `changed` is true.")
