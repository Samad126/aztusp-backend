# AZTUSP Backend

FastAPI service that signs in to the AzTU student portal as the user and returns scraped pages as JSON
(profile, scores, grades, timetable, notices, courses). See [README.md](README.md) for the full API table.

## Layout
- `app/api/routers/` – HTTP routes (`me`, `courses`, `auth`, `notifications`, `telegram`, `system`).
- `app/scraping/` – portal scrapers. `targets.py` (table targets + `FIELD_MAP` label -> key), `courses.py` (course tabs),
  `tasks.py`, `materials.py`, `notices.py`, `grades.py` (current-semester grades table), `client.py` (`SiteScraper`).
- `app/schemas.py` – every response model. Add a model here for every new endpoint.
- `app/watcher/` – background change notifications (email/Telegram). `app/timetable/` – timetable from Telegram PDFs.
- `tests/` – pytest; scrapers are tested against small HTML fixtures, never the live portal.

## Commands
- Tests: `.venv/bin/python -m pytest -q`
- Run: `.venv/bin/uvicorn app.main:app --reload`
- Scripts that import `app` need `PYTHONPATH=.`

## Conventions
- Output keys are English snake_case; the portal's Azerbaijani labels are mapped in `FIELD_MAP` / `TAB_COLUMN_MAP`.
- A portal failure must not crash a whole page: catch `requests.RequestException` / `CourseNotFound`, log, carry on.
- Keep scrapers pure on `BeautifulSoup`; network calls go through `SiteScraper` (`fetch`, `post_page`, `_request`).
- Comments explain *why* (portal quirks), not what.

## Portal facts learned the hard way
- Per-task scores are NOT in the task detail (`30/30` there is the task's maximum). The tasks tab loads the student's
  scores with `GET /studies/lecture_task_ajax2.php?lec_open_idx=..&t_idx_arr[]=..` -> `{"rows":[{"t_idx","score"}]}`.
- The course score table (`Cari müvəffəqiyyət`) only has totals: `sərbəst iş`, `Məşğələ` (quizzes + midterm), `Davamiyyət`.
- Grading order: Quiz 1 -> Midterm -> Quiz 2 -> Individual work -> Final (50 pts before the exam, 50 for the exam).
- Letter scale (AzTU rules): <51 F, 51-60 E, 61-70 D, 71-80 C, 81-90 B, 91-100 A.

## Secrets and safety
- `.env`, `cookies.txt` and `*.db` hold real credentials and sessions. Never print, commit or paste them.
  Use `UNI_USER` / `UNI_PASS` from `.env` only for read-only probes, and keep probe output free of secrets.
- Probe scripts and saved portal HTML go in the scratchpad or `samples/` (git-ignored), never in the repo tree.
