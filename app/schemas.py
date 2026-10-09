"""Request and response models for the HTTP API."""

from typing import Literal

from pydantic import BaseModel, Field


class Detail(BaseModel):
    detail: str = Field(description="Human-readable error message.")


class Credentials(BaseModel):
    username: str = Field(description="Site username (the same one used on the student portal).", examples=["M0000000000"])
    password: str = Field(description="Site password. Used once to sign in; never stored.")


class TokenResponse(BaseModel):
    token: str = Field(description="API token. Send it as `Authorization: Bearer <token>`. Shown only once.")


class OkResponse(BaseModel):
    ok: bool = True


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
