import pytest
from fastapi.testclient import TestClient
from telethon.crypto import AuthKey
from telethon.sessions import StringSession

from app.api import deps
from app.config import ConfigError
from app.main import app
from app.scraping import courses
from app.schemas import SchedulePage
from app.timetable import channel, service
from app.timetable.config import TimetableSource
from app.timetable.pdf import Lessons


def make_session() -> str:
    """A session string that Telethon accepts (the account behind it is not real)."""
    session = StringSession()
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(bytes(range(256)))
    return session.save()


SESSION = make_session()
SOURCE = TimetableSource(channel="aztu_timetable", api_id=1, api_hash="hash", session=SESSION)
EMPTY_PAGE = {
    "name": "schedule",
    "url": "https://d.example.com/studies/lecture_time.php",
    "tables": {},
    "pairs": {},
    "totals": {},
    "sections": {"semesters": [{"title": None, "rows": []}]},
    "fields": {},
}
UNI_PAGE = {
    **EMPTY_PAGE,
    "sections": {"semesters": [{"title": "2026 payiz Dərs cədvəli", "rows": [{"Dərs": "1", "Bazar ertəsi": "Math"}]}]},
}
SESSIONS = [
    {"day": "Bazar ertesi", "time": "9:00-10:20", "week": "alt həftə", "course": "Chem", "type": "Lecture", "room": "1-01", "teacher": "Ali"},
    {"day": "Cume", "time": "9:00-10:20", "week": "üst həftə", "course": "Physics", "type": "Lab", "room": "2-02", "teacher": "Ali"},
]
GRID = [{"Dərs": "1", "Bazar ertesi": "alt həftə: Chem", "Cume": "üst həftə: Physics"}]
LESSONS = Lessons(sessions=SESSIONS, grid=GRID)


class FakeScraper:
    def __init__(self, page):
        self.page = page

    def scrape(self, target):
        assert target.name == "schedule"
        return self.page


def no_search(*args, **kwargs):
    raise AssertionError("the channel should not be searched")


def found_m2(post_id: int = 42) -> list[channel.FoundGroup]:
    return [channel.FoundGroup(group="M2", lessons=LESSONS, post_id=post_id)]


def test_university_lessons_are_returned_as_they_are(monkeypatch):
    monkeypatch.setattr(channel, "search", no_search)

    assert service.read_timetable(FakeScraper(UNI_PAGE), SOURCE) == UNI_PAGE


def test_no_channel_configured_returns_the_empty_university_timetable(monkeypatch):
    monkeypatch.setattr(channel, "search", no_search)

    assert service.read_timetable(FakeScraper(EMPTY_PAGE), None) == EMPTY_PAGE


def test_no_groups_means_nothing_to_search_for(monkeypatch):
    monkeypatch.setattr(courses, "student_groups", lambda scraper: [])
    monkeypatch.setattr(channel, "search", no_search)

    assert service.read_timetable(FakeScraper(EMPTY_PAGE), SOURCE) == EMPTY_PAGE


def test_fallback_has_the_university_shape(monkeypatch):
    monkeypatch.setattr(courses, "student_groups", lambda scraper: ["M2"])
    monkeypatch.setattr(channel, "search", lambda source, groups: found_m2())

    result = service.read_timetable(FakeScraper(EMPTY_PAGE), SOURCE)

    assert result == {
        "name": "schedule",
        "url": "https://t.me/aztu_timetable/42",
        "tables": {},
        "pairs": {},
        "totals": {},
        "sections": {"semesters": [{"title": "M2 Dərs cədvəli", "rows": SESSIONS}]},
        "fields": {},
    }
    SchedulePage.model_validate(result)


def test_fallback_searches_for_the_groups_of_the_student(monkeypatch):
    seen = {}
    monkeypatch.setattr(courses, "student_groups", lambda scraper: ["M1", "M2"])

    def search(source, groups):
        seen["groups"] = groups
        return []

    monkeypatch.setattr(channel, "search", search)
    service.read_timetable(FakeScraper(EMPTY_PAGE), SOURCE)

    assert seen["groups"] == ["M1", "M2"]


def test_no_timetable_found_in_the_channel_returns_the_university_answer(monkeypatch):
    monkeypatch.setattr(courses, "student_groups", lambda scraper: ["M2"])
    monkeypatch.setattr(channel, "search", lambda source, groups: [])

    assert service.read_timetable(FakeScraper(EMPTY_PAGE), SOURCE) == EMPTY_PAGE


def test_channel_error_returns_the_university_answer(monkeypatch):
    monkeypatch.setattr(courses, "student_groups", lambda scraper: ["M2"])

    def broken(source, groups):
        raise channel.TimetableSourceError("not signed in")

    monkeypatch.setattr(channel, "search", broken)

    assert service.read_timetable(FakeScraper(EMPTY_PAGE), SOURCE) == EMPTY_PAGE


def test_both_weeks_are_one_block(monkeypatch):
    monkeypatch.setattr(courses, "student_groups", lambda scraper: ["M2"])
    monkeypatch.setattr(channel, "search", lambda source, groups: found_m2())

    result = service.read_timetable(FakeScraper(EMPTY_PAGE), SOURCE)

    assert [block["title"] for block in result["sections"]["semesters"]] == ["M2 Dərs cədvəli"]
    SchedulePage.model_validate(result)


def test_grid_view_returns_the_grid_rows_in_one_block(monkeypatch):
    monkeypatch.setattr(courses, "student_groups", lambda scraper: ["M2"])
    monkeypatch.setattr(channel, "search", lambda source, groups: found_m2())

    result = service.read_timetable(FakeScraper(EMPTY_PAGE), SOURCE, "grid")

    assert result["sections"]["semesters"] == [{"title": "M2 Dərs cədvəli", "rows": GRID}]
    SchedulePage.model_validate(result)


def test_fallback_links_a_private_channel_post(monkeypatch):
    private = TimetableSource(channel="-1004368645921", api_id=1, api_hash="hash", session=SESSION)
    monkeypatch.setattr(courses, "student_groups", lambda scraper: ["M2"])
    monkeypatch.setattr(channel, "search", lambda source, groups: found_m2(post_id=42))

    result = service.read_timetable(FakeScraper(EMPTY_PAGE), private)

    assert result["url"] == "https://t.me/c/4368645921/42"


def test_university_timetable_is_the_same_in_both_views(monkeypatch):
    monkeypatch.setattr(channel, "search", no_search)

    assert service.read_timetable(FakeScraper(UNI_PAGE), SOURCE, "grid") == UNI_PAGE


@pytest.fixture
def api(monkeypatch):
    """The real route, with the site scraper and the timetable source replaced; no database is needed."""
    app.dependency_overrides[deps.current_scraper] = lambda: FakeScraper(EMPTY_PAGE)
    app.dependency_overrides[deps.get_timetable_source] = lambda: SOURCE
    monkeypatch.setattr(courses, "student_groups", lambda scraper: ["M2"])
    monkeypatch.setattr(channel, "search", lambda source, groups: found_m2(post_id=7))
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_schedule_endpoint_answers_with_the_fallback(api):
    response = api.get("/api/v1/me/schedule")

    assert response.status_code == 200
    body = response.json()
    assert body["url"] == "https://t.me/aztu_timetable/7"
    assert body["sections"]["semesters"] == [{"title": "M2 Dərs cədvəli", "rows": SESSIONS}]


def test_schedule_endpoint_grid_view(api):
    response = api.get("/api/v1/me/schedule?view=grid")

    assert response.status_code == 200
    assert response.json()["sections"]["semesters"] == [{"title": "M2 Dərs cədvəli", "rows": GRID}]


def test_schedule_endpoint_rejects_an_unknown_view(api):
    assert api.get("/api/v1/me/schedule?view=table").status_code == 422


def test_from_env_is_off_when_nothing_is_set(monkeypatch):
    for name in ("SCHEDULE_CHANNEL", "SCHEDULE_TELEGRAM_API_ID", "SCHEDULE_TELEGRAM_API_HASH", "SCHEDULE_TELEGRAM_SESSION"):
        monkeypatch.delenv(name, raising=False)

    assert TimetableSource.from_env() is None


def test_from_env_names_the_missing_variables(monkeypatch):
    monkeypatch.setenv("SCHEDULE_CHANNEL", "@aztu_timetable")
    monkeypatch.setenv("SCHEDULE_TELEGRAM_API_ID", "1")
    monkeypatch.delenv("SCHEDULE_TELEGRAM_API_HASH", raising=False)
    monkeypatch.delenv("SCHEDULE_TELEGRAM_SESSION", raising=False)

    with pytest.raises(ConfigError, match="SCHEDULE_TELEGRAM_API_HASH, SCHEDULE_TELEGRAM_SESSION"):
        TimetableSource.from_env()


def test_from_env_reads_a_complete_setup(monkeypatch):
    monkeypatch.setenv("SCHEDULE_CHANNEL", "@aztu_timetable")
    monkeypatch.setenv("SCHEDULE_TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("SCHEDULE_TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("SCHEDULE_TELEGRAM_SESSION", SESSION)

    source = TimetableSource.from_env()

    assert (source.channel, source.api_id, source.api_hash) == ("aztu_timetable", 12345, "abc")
    assert "abc" not in repr(source)


def test_from_env_rejects_a_bad_session_and_api_id(monkeypatch):
    monkeypatch.setenv("SCHEDULE_CHANNEL", "aztu_timetable")
    monkeypatch.setenv("SCHEDULE_TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("SCHEDULE_TELEGRAM_SESSION", SESSION)
    monkeypatch.setenv("SCHEDULE_TELEGRAM_API_ID", "not-a-number")
    with pytest.raises(ConfigError, match="must be a number"):
        TimetableSource.from_env()

    monkeypatch.setenv("SCHEDULE_TELEGRAM_API_ID", "1")
    monkeypatch.setenv("SCHEDULE_TELEGRAM_SESSION", "garbage")
    with pytest.raises(ConfigError, match="not a session string"):
        TimetableSource.from_env()


def test_from_env_accepts_a_private_channel_by_its_id(monkeypatch):
    monkeypatch.setenv("SCHEDULE_CHANNEL", "-1004368645921")
    monkeypatch.setenv("SCHEDULE_TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("SCHEDULE_TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("SCHEDULE_TELEGRAM_SESSION", SESSION)

    source = TimetableSource.from_env()

    assert source.peer == -1004368645921
    assert source.post_url(7) == "https://t.me/c/4368645921/7"


def test_from_env_rejects_a_bare_number_as_the_channel(monkeypatch):
    monkeypatch.setenv("SCHEDULE_CHANNEL", "4368645921")
    monkeypatch.setenv("SCHEDULE_TELEGRAM_API_ID", "1")
    monkeypatch.setenv("SCHEDULE_TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("SCHEDULE_TELEGRAM_SESSION", SESSION)

    with pytest.raises(ConfigError, match="SCHEDULE_CHANNEL must be"):
        TimetableSource.from_env()
