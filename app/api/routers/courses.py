from fastapi import APIRouter, Depends, File, HTTPException, Path, Request, UploadFile
from fastapi.responses import StreamingResponse

from ...schemas import Course, CourseAttendance, CourseItems, CourseScores, Detail, LecturePlan, MaterialDetail, TaskDetail
from ...scraping import courses, materials, tasks
from ...scraping.client import SiteScraper
from ..deps import current_scraper
from ..errors import SITE_DOWN, UNAUTHORIZED
from ..files import file_response

router = APIRouter(prefix="/courses", tags=["Courses"])


@router.get(
    "",
    summary="List my courses",
    response_model=list[Course],
    responses={**UNAUTHORIZED, **SITE_DOWN},
)
def list_courses(scraper: SiteScraper = Depends(current_scraper)):
    """Courses linked from the dashboard, with the ids used by the other course endpoints."""
    return courses.list_courses(scraper)


@router.get(
    "/{lec_open_idx}/plan",
    summary="Get a course's lecture plan",
    response_model=LecturePlan,
    response_model_exclude_unset=True,
    responses={
        **UNAUTHORIZED,
        404: {"model": Detail, "description": "No course with that `lec_open_idx`."},
        **SITE_DOWN,
    },
)
def course_plan(
    lec_open_idx: str = Path(description="Course id from `GET /courses`."),
    scraper: SiteScraper = Depends(current_scraper),
):
    """Weekly lecture plan, textbooks and course info for one course."""
    return courses.lecture_plan(scraper, lec_open_idx)


COURSE_NOT_FOUND = {404: {"model": Detail, "description": "No course with that `lec_open_idx`."}}
LEC_OPEN_IDX = Path(description="Course id from `GET /courses`.")

LIST_PAGES = {
    "notices": "Course notices (Bildiriş)",
    "board": "Course forum (Forum)",
    "materials": "Course materials (Didaktik materiallar)",
    "tasks": "Course assessments and assignments (Qiymətləndirmə)",
}


def add_list_page(name: str, summary: str) -> None:
    def read_list_page(lec_open_idx: str = LEC_OPEN_IDX, scraper: SiteScraper = Depends(current_scraper)):
        return courses.course_items(scraper, lec_open_idx, name)

    router.add_api_route(
        f"/{{lec_open_idx}}/{name}",
        read_list_page,
        methods=["GET"],
        summary=summary,
        description=f"{summary}, scraped from the university site on every request. `items` is empty when nothing was posted.",
        response_model=CourseItems,
        responses={**UNAUTHORIZED, **COURSE_NOT_FOUND, **SITE_DOWN},
        name=f"course_{name}",
    )


for _name, _summary in LIST_PAGES.items():
    add_list_page(_name, _summary)


@router.get(
    "/{lec_open_idx}/scores",
    summary="Course scores before the final exam (Cari müvəffəqiyyət)",
    response_model=CourseScores,
    responses={**UNAUTHORIZED, **COURSE_NOT_FOUND, **SITE_DOWN},
)
def course_scores(lec_open_idx: str = LEC_OPEN_IDX, scraper: SiteScraper = Depends(current_scraper)):
    """Current points per component (e.g. seminars, independent work) and the total."""
    return courses.course_scores(scraper, lec_open_idx)


@router.get(
    "/{lec_open_idx}/attendance",
    summary="Course attendance (Davamiyyət)",
    response_model=CourseAttendance,
    responses={**UNAUTHORIZED, **COURSE_NOT_FOUND, **SITE_DOWN},
)
def course_attendance(lec_open_idx: str = LEC_OPEN_IDX, scraper: SiteScraper = Depends(current_scraper)):
    """Attendance journal: one entry per class meeting, with the attendance score and percentage."""
    return courses.course_attendance(scraper, lec_open_idx)


MATERIAL_ID = Path(pattern=r"^\d{1,12}$", description="Material `id` from a course's `materials` list.")


@router.get(
    "/materials/{material_id}",
    summary="Read one course material (Didaktik materiallar)",
    response_model=MaterialDetail,
    responses={**UNAUTHORIZED, 404: {"model": Detail, "description": "No material with that id."}, **SITE_DOWN},
)
def read_material(request: Request, material_id: str = MATERIAL_ID, scraper: SiteScraper = Depends(current_scraper)):
    """The material as the site shows it when a row of the materials list is opened: subject, date, views, files and text."""
    material = materials.material_detail(scraper, material_id)
    for attachment in material["attachments"]:
        if attachment["file_no"].isdigit():
            attachment["download"] = request.app.url_path_for(
                "download_material_file", material_id=material_id, file_no=attachment["file_no"]
            )
    return material


@router.get(
    "/materials/{material_id}/files/{file_no}",
    summary="Download a course material's file (Qoşma)",
    name="download_material_file",
    response_class=StreamingResponse,
    responses={
        200: {"description": "The file.", "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}},
        **UNAUTHORIZED,
        404: {"model": Detail, "description": "The material has no such file."},
        **SITE_DOWN,
    },
)
def download_material_file(
    material_id: str = MATERIAL_ID,
    file_no: str = Path(pattern=r"^[1-9]\d{0,2}$", description="`file_no` from the material's `attachments`."),
    scraper: SiteScraper = Depends(current_scraper),
):
    """Streams the file through the API, so the frontend does not need the site session."""
    upstream, filename = materials.open_material_file(scraper, material_id, file_no)
    return file_response(upstream, filename)


TASK_ID = Path(pattern=r"^\d{1,12}$", description="Task `id` from a course's `tasks` list.")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
TASK_NOT_FOUND = {404: {"model": Detail, "description": "No such course or task."}}


@router.get(
    "/{lec_open_idx}/tasks/{task_id}",
    summary="Read one course task (Qiymətləndirmə)",
    response_model=TaskDetail,
    responses={**UNAUTHORIZED, **TASK_NOT_FOUND, **SITE_DOWN},
)
def read_task(
    request: Request,
    lec_open_idx: str = LEC_OPEN_IDX,
    task_id: str = TASK_ID,
    scraper: SiteScraper = Depends(current_scraper),
):
    """The task as the site shows it when its `Movzu` is clicked in the tasks list: period, type, text, links and files."""
    return _with_task_downloads(request, lec_open_idx, tasks.task_detail(scraper, lec_open_idx, task_id))


def _with_task_downloads(request: Request, lec_open_idx: str, task: dict) -> dict:
    task_id = task["id"]
    for attachment in task["attachments"]:
        if attachment["file_no"].isdigit():
            attachment["download"] = request.app.url_path_for(
                "download_task_file", lec_open_idx=lec_open_idx, task_id=task_id, file_no=attachment["file_no"]
            )
    return task


@router.get(
    "/{lec_open_idx}/tasks/{task_id}/files/{file_no}",
    summary="Download a course task's file (Qoşma)",
    name="download_task_file",
    response_class=StreamingResponse,
    responses={
        200: {"description": "The file.", "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}},
        **UNAUTHORIZED,
        404: {"model": Detail, "description": "The task has no such file."},
        **SITE_DOWN,
    },
)
def download_task_file(
    lec_open_idx: str = LEC_OPEN_IDX,
    task_id: str = TASK_ID,
    file_no: str = Path(pattern=r"^[1-9]\d{0,2}$", description="`file_no` from the task's `attachments`."),
    scraper: SiteScraper = Depends(current_scraper),
):
    """Streams the file through the API, so the frontend does not need the site session."""
    upstream, filename = tasks.open_task_file(scraper, task_id, file_no)
    return file_response(upstream, filename)


@router.post(
    "/{lec_open_idx}/tasks/{task_id}/submission",
    summary="Hand in work for a course task (İşin verilmə)",
    response_model=TaskDetail,
    responses={
        **UNAUTHORIZED,
        **TASK_NOT_FOUND,
        409: {"model": Detail, "description": "The site shows no upload form for the task (`can_submit` is false)."},
        413: {"model": Detail, "description": "A file is over 20 MB."},
        422: {"description": "Not one or two files."},
        **SITE_DOWN,
    },
)
def submit_task(
    request: Request,
    lec_open_idx: str = LEC_OPEN_IDX,
    task_id: str = TASK_ID,
    file1: UploadFile = File(description="First file (`o_file1`)."),
    file2: UploadFile | None = File(None, description="Second file (`o_file2`), optional."),
    scraper: SiteScraper = Depends(current_scraper),
):
    """Uploads the file(s) to the university site, as the `Əlavə et` button does. This sends real work to the teacher.

    Returns the task as it reads afterwards (`submissions` shows the new count).
    """
    files = []
    for upload in (file1, file2):
        if upload is None:
            continue
        content = upload.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "A file is over 20 MB")
        files.append((upload.filename or "file", content, upload.content_type or "application/octet-stream"))
    return _with_task_downloads(request, lec_open_idx, tasks.submit_task(scraper, lec_open_idx, task_id, files))
