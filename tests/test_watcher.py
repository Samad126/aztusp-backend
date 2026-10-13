"""Watcher tests: result changes and absence marks. The database tests need TEST_DATABASE_URL, otherwise they are skipped."""

import os
import ssl
from dataclasses import replace
from types import SimpleNamespace

import pytest
import requests
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app.config import ConfigError, Settings
from app.db import Notifications, Subscription, UserStore
from app.schemas import NotificationsIn, NotificationsOut
from app.scraping.client import BadCredentials, SiteScraper
from app.scraping.targets import TARGETS_BY_NAME
from app.watcher import attendance, checker
from app.watcher import notify as notify_module
from app.watcher.attendance import AttendanceChange, find_attendance_changes, format_attendance_message, take_attendance_snapshot
from app.watcher.checker import Watcher
from app.watcher.config import SmtpSettings, WatcherSettings, watched_fields
from app.watcher.links import CourseLink
from app.watcher.notify import NotifyError, format_message, send_email, send_telegram
from app.watcher.results import Change, find_changes, take_snapshot
from app.watcher.tabs import (
    ScoreChange,
    WorkChange,
    find_score_changes,
    find_work_changes,
    format_score_message,
    format_work_message,
    take_score_snapshot,
    take_work_snapshot,
)

FIELDS = ("final_score", "grade")
SEMESTER = "2026 Tədris ili payiz Semestr"
SETTINGS = Settings("k", "db", "example.com", "https://login.example.com/", "https://d.example.com/app/", "u", "p", 5.0)
SMTP = SmtpSettings(host="smtp-relay.brevo.com", port=587, username="login@example.com", password="smtp-key", sender="sender@example.com")
FRONTEND = "https://app.example.com"
WATCH = WatcherSettings(interval_seconds=1800, smtp=SMTP, telegram_bot_token="123:ABC", frontend_url=FRONTEND)
SCORES_PAGE = "https://d.example.com/telebe/score_view.php"
GRADES_LINKS = f"Open in AZTUSP: {FRONTEND}/grades\nOpen on the university site: {SCORES_PAGE}"


def tab_url(page, idx):
    """The university's address for a course tab, as the course readers return it."""
    return f"https://d.example.com/studies/{page}.php?lec_open_idx={idx}"

SCORES_HTML = f"""
<div class="card"><div class="card-body"><h5 class="text-primary">{SEMESTER}</h5>
<table><thead><tr><th>Fənn növü</th><th>Fənnlər</th><th>Kredit</th><th></th><th></th><th>Yekun bal</th><th>Dərəcə</th><th>Təkrar dərs</th></tr></thead>
<tbody>
<tr><td>məcburi</td><td>Algorithms</td><td>5</td><td>x</td><td>y</td><td>78</td><td>B</td><td>N</td></tr>
<tr><td>seçmə</td><td>Databases</td><td>4</td><td>x</td><td>y</td><td></td><td></td><td>N</td></tr>
</tbody></table></div></div>
"""


def scores_page(*rows):
    return {"sections": {"semester_courses": [{"title": SEMESTER, "rows": list(rows)}]}}


def row(course, final_score="", grade="", **extra):
    return {"course": course, "course_type": "məcburi", "credits": "5", "final_score": final_score, "grade": grade, "retake": "N", **extra}


class HtmlScraper(SiteScraper):
    def fetch(self, url, headers=None):
        return BeautifulSoup(SCORES_HTML, "html.parser")


def test_real_scores_html_becomes_a_snapshot():
    scores = HtmlScraper(SETTINGS, "M1").scrape(TARGETS_BY_NAME["scores"])

    assert take_snapshot(scores, FIELDS) == [
        {"semester": SEMESTER, "course": "Algorithms", "values": {"final_score": "78", "grade": "B"}},
        {"semester": SEMESTER, "course": "Databases", "values": {"final_score": None, "grade": None}},
    ]


def test_take_snapshot_skips_rows_without_a_course():
    snapshot = take_snapshot(scores_page(row("Algorithms", "78", "B"), {"final_score": "1"}), FIELDS)

    assert [entry["course"] for entry in snapshot] == ["Algorithms"]


def test_find_changes_reports_new_and_updated_results():
    before = take_snapshot(scores_page(row("Algorithms", "70", "C"), row("Databases")), FIELDS)
    after = take_snapshot(scores_page(row("Algorithms", "78", "C"), row("Databases", "85", "A")), FIELDS)

    assert find_changes(before, after, FIELDS) == [
        Change(SEMESTER, "Algorithms", "final_score", "70", "78"),
        Change(SEMESTER, "Databases", "final_score", None, "85"),
        Change(SEMESTER, "Databases", "grade", None, "A"),
    ]


def test_find_changes_only_looks_at_watched_fields():
    before = take_snapshot(scores_page(row("Algorithms", "70", "C")), ("grade",))
    after = take_snapshot(scores_page(row("Algorithms", "78", "C")), ("grade",))

    assert find_changes(before, after, ("grade",)) == []


def test_find_changes_ignores_unchanged_and_cleared_results():
    before = take_snapshot(scores_page(row("Algorithms", "78", "B")), FIELDS)

    assert find_changes(before, take_snapshot(scores_page(row("Algorithms", "78", "B")), FIELDS), FIELDS) == []
    assert find_changes(before, take_snapshot(scores_page(row("Algorithms", "", "")), FIELDS), FIELDS) == []


def test_format_message_names_the_course_and_the_result_and_links_to_the_grades():
    subject, body = format_message(
        [Change(SEMESTER, "Algorithms", "final_score", "70", "78"), Change(SEMESTER, "Algorithms", "grade", None, "B")],
        FRONTEND,
        SCORES_PAGE,
    )

    assert subject == "AZTUSP: result updated for Algorithms"
    assert body == f"Course: Algorithms\nSemester: {SEMESTER}\nFinal score: 78 (was 70)\nGrade: B\n\n{GRADES_LINKS}"


def test_format_message_counts_courses_in_the_subject():
    subject, body = format_message(
        [Change(SEMESTER, "Algorithms", "grade", None, "B"), Change(SEMESTER, "Databases", "grade", None, "A")], FRONTEND, SCORES_PAGE
    )

    assert subject == "AZTUSP: results updated for 2 courses"
    assert "Course: Algorithms" in body and "Course: Databases" in body
    assert body.endswith(GRADES_LINKS)


class FakeResponse:
    def __init__(self, status_code, text="{}"):
        self.status_code = status_code
        self.text = text

    @property
    def ok(self):
        return self.status_code < 400


def test_send_telegram_posts_the_text(monkeypatch):
    calls = []
    monkeypatch.setattr(notify_module.requests, "post", lambda url, json, timeout: calls.append((url, json)) or FakeResponse(200))

    send_telegram("123:ABC", "42", "hello")

    assert calls == [("https://api.telegram.org/bot123:ABC/sendMessage", {"chat_id": "42", "text": "hello"})]


def test_send_telegram_errors_do_not_leak_the_token(monkeypatch):
    def unreachable(url, json, timeout):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(notify_module.requests, "post", unreachable)

    with pytest.raises(NotifyError) as exc:
        send_telegram("123:SECRET", "42", "x")
    assert "SECRET" not in str(exc.value)


def test_send_telegram_reports_the_rejection_reason(monkeypatch):
    monkeypatch.setattr(
        notify_module.requests, "post", lambda url, json, timeout: FakeResponse(400, '{"ok":false,"description":"Bad Request: chat not found"}')
    )

    with pytest.raises(NotifyError, match="chat not found"):
        send_telegram("123:ABC", "42", "x")


def smtp_class(calls):
    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            calls.append(("connect", host, port, context))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            calls.append(("starttls", context))

        def login(self, user, password):
            calls.append(("login", user, password))

        def send_message(self, message):
            calls.append(("send", message["To"], message["Subject"]))

    return FakeSMTP


def test_send_email_uses_starttls_with_certificate_checks(monkeypatch):
    calls = []
    monkeypatch.setattr(notify_module.smtplib, "SMTP", smtp_class(calls))

    send_email(SMTP, "student@example.com", "subject", "body")

    assert [c[0] for c in calls] == ["connect", "starttls", "login", "send"]
    assert calls[1][1].verify_mode == ssl.CERT_REQUIRED
    assert calls[2:] == [("login", "login@example.com", "smtp-key"), ("send", "student@example.com", "subject")]


def test_send_email_on_port_465_connects_with_ssl(monkeypatch):
    calls = []
    monkeypatch.setattr(notify_module.smtplib, "SMTP_SSL", smtp_class(calls))

    send_email(replace(SMTP, port=465), "student@example.com", "subject", "body")

    assert [c[0] for c in calls] == ["connect", "login", "send"]


def test_notify_sends_to_the_channels_the_student_chose(monkeypatch):
    sent = []
    monkeypatch.setattr(notify_module, "send_email", lambda smtp, to, subject, body: sent.append(("email", to)))
    monkeypatch.setattr(notify_module, "send_telegram", lambda token, chat, text: sent.append(("telegram", chat)))

    assert notify_module.notify(WATCH, "student@example.com", "42", "s", "b") is True
    assert sent == [("email", "student@example.com"), ("telegram", "42")]


def test_notify_reports_a_channel_that_is_not_configured(caplog):
    settings = WatcherSettings(interval_seconds=1800, smtp=None, telegram_bot_token="123:ABC")

    assert notify_module.notify(settings, "student@example.com", None, "s", "b") is False
    assert "SMTP is not configured" in caplog.text


@pytest.fixture
def clean_env(monkeypatch):
    for key in ("WATCH_INTERVAL_MINUTES", "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "MAIL_FROM", "TELEGRAM_BOT_TOKEN", "FRONTEND_URL"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_defaults_check_every_30_minutes_with_telegram_only(clean_env):
    clean_env.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")

    settings = WatcherSettings.from_env()

    assert settings.interval_seconds == 1800
    assert settings.smtp is None
    assert settings.telegram_bot_token == "123:ABC"


def test_links_open_aztusp_at_its_default_address_or_the_one_given(clean_env):
    clean_env.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    assert WatcherSettings.from_env().frontend_url == "https://aztu.alakbaroff.com"

    clean_env.setenv("FRONTEND_URL", "https://example.com/aztusp/")
    assert WatcherSettings.from_env().frontend_url == "https://example.com/aztusp"


def test_smtp_sender_defaults_to_the_login(clean_env):
    clean_env.setenv("SMTP_HOST", "smtp-relay.brevo.com")
    clean_env.setenv("SMTP_USERNAME", "login@example.com")
    clean_env.setenv("SMTP_PASSWORD", "smtp-key")

    settings = WatcherSettings.from_env()

    assert settings.smtp.sender == "login@example.com"
    assert settings.smtp.port == 587


def test_at_least_one_channel_is_required(clean_env):
    with pytest.raises(ConfigError, match="No delivery channel"):
        WatcherSettings.from_env()


def test_partly_configured_email_is_an_error(clean_env):
    clean_env.setenv("SMTP_HOST", "smtp-relay.brevo.com")

    with pytest.raises(ConfigError, match="missing: SMTP_USERNAME, SMTP_PASSWORD"):
        WatcherSettings.from_env()


class FakeStore:
    def __init__(self, subscriptions):
        self.subscriptions = subscriptions
        self.snapshots = {}
        self.course_snapshots = {}
        self.statuses = {}

    def subscribers(self):
        # Like the database: the status recorded by the last check is what the next pass sees.
        return [
            replace(s, notifications=replace(s.notifications, status=self.statuses.get(s.user_id, s.notifications.status)))
            for s in self.subscriptions
        ]

    def load_snapshot(self, user_id):
        return self.snapshots.get(user_id)

    def save_snapshot(self, user_id, data):
        self.snapshots[user_id] = data

    def load_course_snapshot(self, user_id, watch):
        return self.course_snapshots.get((user_id, watch))

    def save_course_snapshot(self, user_id, watch, data):
        self.course_snapshots[(user_id, watch)] = data

    def record_check(self, user_id, status):
        self.statuses[user_id] = status


def subscription(
    user_id,
    username="M1",
    password="pw",
    fields=FIELDS,
    status="ok",
    email="student@example.com",
    chat="42",
    attendance=False,
    individual_work=False,
    course_scores=False,
):
    notifications = Notifications(
        email,
        list(fields),
        status,
        telegram_linked=chat is not None,
        attendance=attendance,
        individual_work=individual_work,
        course_scores=course_scores,
    )
    return Subscription(user_id, username, password, notifications, chat)


@pytest.fixture
def portal(monkeypatch):
    """A fake university portal. Pages are served per site username, one per check; an exception in the list is raised instead.
    Passwords in `rejected` fail to sign in."""
    pages, rejected, logins = {}, set(), []

    class FakeSiteScraper:
        def __init__(self, settings, username, cookies=None, on_save=None):
            self.username = username

        def login(self, password):
            logins.append((self.username, password))
            if password in rejected:
                raise BadCredentials("Incorrect username or password")

        def scrape(self, target):
            page = pages[self.username].pop(0)
            if isinstance(page, Exception):
                raise page
            return page

    monkeypatch.setattr(checker, "SiteScraper", FakeSiteScraper)
    return SimpleNamespace(pages=pages, rejected=rejected, logins=logins)


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(checker, "notify", lambda settings, email, chat, subject, body: messages.append((email, chat, subject, body)) or True)
    return messages


def test_first_check_saves_a_baseline_without_notifying(portal, sent):
    portal.pages["M1"] = [scores_page(row("Algorithms", "70", "C"))]
    store = FakeStore([subscription(1)])

    Watcher(SETTINGS, WATCH, store).check_all()

    assert sent == []
    assert store.snapshots[1] == {"fields": list(FIELDS), "courses": take_snapshot(scores_page(row("Algorithms", "70", "C")), FIELDS)}
    assert store.statuses[1] == "ok"


def test_a_changed_result_goes_to_that_students_channels(portal, sent):
    portal.pages["M1"] = [scores_page(row("Algorithms", "70", "C")), scores_page(row("Algorithms", "78", "B"))]
    watcher = Watcher(SETTINGS, WATCH, FakeStore([subscription(1)]))

    watcher.check_all()
    watcher.check_all()

    assert sent == [
        (
            "student@example.com",
            "42",
            "AZTUSP: result updated for Algorithms",
            f"Course: Algorithms\nSemester: {SEMESTER}\nFinal score: 78 (was 70)\nGrade: B (was C)\n\n{GRADES_LINKS}",
        )
    ]


def test_every_check_signs_in_again(portal, sent):
    portal.pages["M1"] = [scores_page(row("Algorithms", "78", "B")), scores_page(row("Algorithms", "78", "B"))]
    watcher = Watcher(SETTINGS, WATCH, FakeStore([subscription(1)]))

    watcher.check_all()
    watcher.check_all()

    assert portal.logins == [("M1", "pw"), ("M1", "pw")]
    assert sent == []


def test_each_student_is_read_with_their_own_account(portal, sent):
    portal.pages["M1"] = [scores_page(row("Algorithms", "70", "C")), scores_page(row("Algorithms", "70", "C"))]
    portal.pages["M2"] = [scores_page(row("Databases", "60", "D")), scores_page(row("Databases", "65", "D"))]
    store = FakeStore([subscription(1, "M1", "pw1", email="one@example.com", chat=None), subscription(2, "M2", "pw2", email="two@example.com", chat=None)])
    watcher = Watcher(SETTINGS, WATCH, store)

    watcher.check_all()
    watcher.check_all()

    assert portal.logins == [("M1", "pw1"), ("M2", "pw2"), ("M1", "pw1"), ("M2", "pw2")]
    assert [(email, subject) for email, _, subject, _ in sent] == [("two@example.com", "AZTUSP: result updated for Databases")]


def test_a_rejected_password_pauses_the_student(portal, sent):
    portal.rejected.add("pw")
    portal.pages["M1"] = [scores_page(row("Algorithms", "78", "B"))]
    store = FakeStore([subscription(1)])
    watcher = Watcher(SETTINGS, WATCH, store)

    watcher.check_all()
    watcher.check_all()

    assert store.statuses[1] == "wrong_password"
    assert portal.logins == [("M1", "pw")]  # no second attempt with the same password
    assert store.snapshots == {} and sent == []


def test_a_student_already_marked_wrong_password_is_not_checked(portal):
    store = FakeStore([subscription(1, status="wrong_password")])

    Watcher(SETTINGS, WATCH, store).check_all()

    assert portal.logins == []


def test_a_failed_check_is_recorded_and_the_others_still_run(portal, sent):
    portal.pages["M1"] = [RuntimeError("portal went away")]
    portal.pages["M2"] = [scores_page(row("Databases", "60", "D"))]
    store = FakeStore([subscription(1, "M1"), subscription(2, "M2")])

    Watcher(SETTINGS, WATCH, store).check_all()

    assert store.statuses == {1: "error", 2: "ok"}
    assert 2 in store.snapshots


def test_empty_scores_page_keeps_the_previous_snapshot(portal, sent):
    portal.pages["M1"] = [scores_page(row("Algorithms", "70", "C")), scores_page()]
    store = FakeStore([subscription(1)])
    watcher = Watcher(SETTINGS, WATCH, store)

    watcher.check_all()
    before = store.snapshots[1]
    watcher.check_all()

    assert sent == []
    assert store.snapshots[1] is before


def test_a_newly_watched_field_is_baselined_not_announced(portal, sent):
    portal.pages["M1"] = [scores_page(row("Algorithms", "78", "C"))]
    store = FakeStore([subscription(1)])
    store.snapshots[1] = {"fields": ["grade"], "courses": take_snapshot(scores_page(row("Algorithms", "", "C")), ("grade",))}

    Watcher(SETTINGS, WATCH, store).check_all()

    assert sent == []
    assert store.snapshots[1]["fields"] == list(FIELDS)


def test_a_telegram_only_student_is_messaged_on_telegram(portal, sent):
    portal.pages["M1"] = [scores_page(row("Algorithms", "70", "C")), scores_page(row("Algorithms", "78", "B"))]
    watcher = Watcher(SETTINGS, WATCH, FakeStore([subscription(1, email=None, chat="42")]))

    watcher.check_all()
    watcher.check_all()

    assert [(email, chat) for email, chat, _, _ in sent] == [(None, "42")]


def test_a_run_says_how_many_students_it_checked(portal, sent, caplog):
    caplog.set_level("INFO")
    Watcher(SETTINGS, WATCH, FakeStore([])).check_all()

    assert "Checking 0 student(s)" in caplog.text


DASHBOARD = [
    {"lec_open_idx": "7", "sem_code": "S1", "name": "Math [M1]", "path": "/a"},
    {"lec_open_idx": "8", "sem_code": "S1", "name": "Physics [M1]", "path": "/b"},
]


def journal(course, *meetings, student="M1", others=()):
    """A course's attendance page: the student's row with one (date, mark) per class meeting, and rows of other students."""
    sessions = [{"number": str(n), "date": date, "journal_date": None} for n, (date, _) in enumerate(meetings, 1)]
    marks = [{"session": str(n), "status": None, "mark": mark} for n, (_, mark) in enumerate(meetings, 1)]
    own = {"number": "1", "student_id": student, "name": "Me", "marks": marks, "score": "0", "percent": "100"}
    others_rows = [
        {"number": str(n), "student_id": other, "name": "Other", "marks": [], "score": "0", "percent": "0"}
        for n, other in enumerate(others, 2)
    ]
    return {"course": course, "sessions": sessions, "students": [own, *others_rows]}


@pytest.fixture
def campus(monkeypatch):
    """Fake attendance pages: one dashboard for everyone, and each student's journal per course: `campus[username][lec_open_idx]`.
    An exception in place of a page is raised when that page is read."""
    journals = {}

    def read(scraper, lec_open_idx, listing=None):
        page = journals[scraper.username][lec_open_idx]
        if isinstance(page, Exception):
            raise page
        return {"url": tab_url("lecture_attend", lec_open_idx), **page}

    monkeypatch.setattr(attendance.courses, "list_courses", lambda scraper: DASHBOARD)
    monkeypatch.setattr(attendance.courses, "course_attendance", read)
    return journals


def test_marks_that_become_absent_or_present_are_the_changes():
    previous = [
        {
            "lec_open_idx": "7",
            "course": "Math",
            "sessions": [
                {"number": "1", "date": "15.09", "mark": "present"},
                {"number": "2", "date": "22.09", "mark": "absent"},
                {"number": "3", "date": "29.09", "mark": "not_entered"},
                {"number": "4", "date": "06.10", "mark": None},
                {"number": "7", "date": "27.10", "mark": "present"},
            ],
        }
    ]
    current = [
        {
            "lec_open_idx": "7",
            "course": "Math",
            "sessions": [
                {"number": "1", "date": "15.09", "mark": "absent"},  # present -> absent
                {"number": "2", "date": "22.09", "mark": "present"},  # absent -> present, a correction
                {"number": "3", "date": "29.09", "mark": "absent"},  # not entered -> absent
                {"number": "4", "date": "06.10", "mark": "present"},  # blank -> present
                {"number": "5", "date": "13.10", "mark": "absent"},  # a new meeting, absent
                {"number": "6", "date": "20.10", "mark": "present"},  # a new meeting, present
                {"number": "7", "date": "27.10", "mark": "present"},  # present stays present: not reported
            ],
        }
    ]

    changes = find_attendance_changes(previous, current)

    assert [(c.session, c.old, c.new) for c in changes] == [
        ("1", "present", "absent"),
        ("2", "absent", "present"),
        ("3", "not_entered", "absent"),
        ("4", None, "present"),
        ("5", None, "absent"),
        ("6", None, "present"),
    ]
    assert find_attendance_changes(current, current) == []


MATH = CourseLink("7", tab_url("lecture_attend", "7"))
MATH_TAB = f"Open in AZTUSP: {FRONTEND}/courses/7?tab=attendance\nOpen on the university site: {tab_url('lecture_attend', '7')}"


def test_absence_message_names_the_course_and_each_meeting_that_changed_and_links_to_the_attendance_tab():
    changes = [AttendanceChange("Math", "2", "22.09", None, "absent", MATH), AttendanceChange("Math", "1", None, "absent", "present", MATH)]

    subject, body = format_attendance_message(changes, FRONTEND)

    assert subject == "AZTUSP: attendance updated for Math"
    assert body == f"Course: Math\nSession 2 (22.09): absent\nSession 1: present (was absent)\n{MATH_TAB}"


def test_absence_message_counts_courses_in_the_subject():
    physics = CourseLink("8", tab_url("lecture_attend", "8"))
    changes = [AttendanceChange("Math", "2", None, None, "absent", MATH), AttendanceChange("Physics", "1", None, None, "absent", physics)]

    subject, body = format_attendance_message(changes, FRONTEND)

    assert subject == "AZTUSP: attendance updated for 2 courses"
    assert f"{FRONTEND}/courses/8?tab=attendance" in body


def test_present_message_for_a_meeting_with_no_earlier_mark_says_only_present():
    changes = [AttendanceChange("Math", "4", "06.10", None, "present", MATH)]

    subject, body = format_attendance_message(changes, FRONTEND)

    assert subject == "AZTUSP: attendance updated for Math"
    assert body == f"Course: Math\nSession 4 (06.10): present\n{MATH_TAB}"


def test_absence_message_keeps_only_the_aztusp_link_for_a_snapshot_saved_before_links():
    # A course that could not be read at the last run keeps the entry it had before the links were kept.
    previous = [{"lec_open_idx": "7", "course": "Math", "sessions": [{"number": "1", "date": None, "mark": "present"}]}]
    current = [{"lec_open_idx": "7", "course": "Math", "sessions": [{"number": "1", "date": None, "mark": "absent"}]}]

    body = format_attendance_message(find_attendance_changes(previous, current), FRONTEND)[1]

    assert body == f"Course: Math\nSession 1: absent (was present)\nOpen in AZTUSP: {FRONTEND}/courses/7?tab=attendance"


def test_own_marks_are_read_for_each_course_and_other_students_are_ignored(campus):
    campus["M1"] = {
        "7": journal("Math", ("15.09", "present"), ("22.09", "absent"), ("29.09", None), others=("S2",)),
        "8": journal("Physics", ("16.09", "absent"), student="m1"),
    }

    snapshot = take_attendance_snapshot(SimpleNamespace(username="M1"), [])

    assert snapshot == [
        {
            "lec_open_idx": "7",
            "course": "Math",
            "site_url": tab_url("lecture_attend", "7"),
            "sessions": [
                {"number": "1", "date": "15.09", "mark": "present"},
                {"number": "2", "date": "22.09", "mark": "absent"},
                {"number": "3", "date": "29.09", "mark": None},
            ],
        },
        {
            "lec_open_idx": "8",
            "course": "Physics",
            "site_url": tab_url("lecture_attend", "8"),
            "sessions": [{"number": "1", "date": "16.09", "mark": "absent"}],
        },
    ]


def test_a_course_that_cannot_be_read_keeps_its_last_marks(campus):
    last = [
        {"lec_open_idx": "7", "course": "Math", "sessions": [{"number": "1", "date": "15.09", "mark": "present"}]},
        {"lec_open_idx": "8", "course": "Physics", "sessions": [{"number": "1", "date": "16.09", "mark": "absent"}]},
        {"lec_open_idx": "9", "course": "Old course", "sessions": []},  # no longer on the dashboard: dropped
    ]
    campus["M1"] = {"7": requests.ConnectionError("portal slow"), "8": journal("Physics", ("16.09", "present"), student="S9")}

    assert take_attendance_snapshot(SimpleNamespace(username="M1"), last) == last[:2]


def test_first_absence_check_saves_the_marks_without_notifying(portal, campus, sent):
    campus["M1"] = {"7": journal("Math", ("15.09", "absent")), "8": journal("Physics", ("16.09", "present"))}
    store = FakeStore([subscription(1, fields=(), attendance=True)])

    Watcher(SETTINGS, WATCH, store).check_all()

    assert sent == []
    assert store.course_snapshots[(1, "attendance")]["courses"][0]["sessions"] == [{"number": "1", "date": "15.09", "mark": "absent"}]
    assert store.statuses[1] == "ok"


def test_a_new_absence_goes_to_that_students_channels(portal, campus, sent):
    campus["M1"] = {"7": journal("Math", ("15.09", "present")), "8": journal("Physics", ("16.09", "present"))}
    watcher = Watcher(SETTINGS, WATCH, FakeStore([subscription(1, fields=(), attendance=True)]))
    watcher.check_all()

    campus["M1"]["7"] = journal("Math", ("15.09", "present"), ("22.09", "absent"))
    watcher.check_all()

    assert sent == [("student@example.com", "42", "AZTUSP: attendance updated for Math", f"Course: Math\nSession 2 (22.09): absent\n{MATH_TAB}")]


def test_a_meeting_marked_present_goes_to_that_students_channels(portal, campus, sent):
    campus["M1"] = {"7": journal("Math", ("15.09", "present")), "8": journal("Physics", ("16.09", "present"))}
    watcher = Watcher(SETTINGS, WATCH, FakeStore([subscription(1, fields=(), attendance=True)]))
    watcher.check_all()

    campus["M1"]["7"] = journal("Math", ("15.09", "present"), ("22.09", "present"))
    watcher.check_all()

    assert sent == [("student@example.com", "42", "AZTUSP: attendance updated for Math", f"Course: Math\nSession 2 (22.09): present\n{MATH_TAB}")]


def test_an_attendance_only_student_is_not_read_for_grades(portal, campus, sent):
    # The portal fixture has no scores pages for this student, so reading them would fail the check.
    campus["M1"] = {"7": journal("Math", ("15.09", "present")), "8": journal("Physics", ("16.09", "present"))}
    store = FakeStore([subscription(1, fields=(), attendance=True)])

    Watcher(SETTINGS, WATCH, store).check_all()

    assert store.statuses[1] == "ok"
    assert sent == []


def test_grade_and_absence_changes_are_sent_as_separate_messages(portal, campus, sent):
    portal.pages["M1"] = [scores_page(row("Algorithms", "70", "C")), scores_page(row("Algorithms", "78", "B"))]
    campus["M1"] = {"7": journal("Math", ("15.09", "present")), "8": journal("Physics", ("16.09", "present"))}
    watcher = Watcher(SETTINGS, WATCH, FakeStore([subscription(1, attendance=True)]))
    watcher.check_all()

    campus["M1"]["8"] = journal("Physics", ("16.09", "absent"))
    watcher.check_all()

    assert [subject for _, _, subject, _ in sent] == ["AZTUSP: result updated for Algorithms", "AZTUSP: attendance updated for Physics"]


def test_an_unreadable_course_is_not_reported_as_its_attendance_going_or_coming_back(portal, campus, sent):
    campus["M1"] = {"7": journal("Math", ("15.09", "absent")), "8": journal("Physics", ("16.09", "present"))}
    watcher = Watcher(SETTINGS, WATCH, FakeStore([subscription(1, fields=(), attendance=True)]))
    watcher.check_all()

    campus["M1"]["7"] = requests.ConnectionError("portal slow")
    watcher.check_all()
    campus["M1"]["7"] = journal("Math", ("15.09", "absent"))
    watcher.check_all()

    assert sent == []


def test_absence_watch_can_be_on_without_any_result_field():
    assert NotificationsIn(fields=[], attendance=True).fields == []


def test_the_old_absences_name_still_turns_the_attendance_watch_on():
    # A client that still sends the old name must not switch the watch off when it saves its other settings.
    assert NotificationsIn(fields=[], absences=True).attendance is True
    assert NotificationsIn(fields=["grade"], absences=False).attendance is False
    with pytest.raises(ValidationError, match="choose something to watch"):
        NotificationsIn(fields=[])


def test_final_score_and_grade_are_always_watched_together():
    assert NotificationsIn(fields=["grade"]).fields == ["final_score", "grade"]
    assert NotificationsIn(fields=["final_score"]).fields == ["final_score", "grade"]
    assert NotificationsIn(fields=["final_score", "grade"]).fields == ["final_score", "grade"]


@pytest.mark.parametrize("name", ["course_type", "credits", "retake", "nope"])
def test_fields_that_are_not_offered_are_refused(name):
    with pytest.raises(ValidationError, match="fields must be from"):
        NotificationsIn(fields=[name])


def test_settings_saved_before_a_field_was_removed_still_read_back():
    saved = NotificationsOut(email=None, fields=["course_type", "credits", "retake"], telegram_linked=True, status="ok")

    assert saved.fields == []


def work(task_type="Essay", end="27.10.2026", id="5", number="1", **extra):
    """An individual work as the Qiymətləndirmə tab reads it. Without an id, the row is identified by its content."""
    row = {"number": number, "task_type": task_type, "evaluation": "10", "subject": "Lecture 3", "start_date": "20.10.2026", "end_date": end, **extra}
    return {**row, "id": id} if id else row


def component(name, maximum, score):
    return {"name": name, "max": maximum, "score": score}


@pytest.fixture
def course_tabs(monkeypatch):
    """Fake individual work and score pages, per site username and course: `course_tabs.work["M1"]["7"]` and
    `course_tabs.scores["M1"]["7"]`. A course without a page has an empty tab; an exception in place of a page is raised."""
    pages = SimpleNamespace(work={}, scores={})

    def read(page, tab, idx):
        if isinstance(page, Exception):
            raise page
        return {"url": tab_url(tab, idx), **page}

    def items(scraper, idx, name, listing=None):
        return read(pages.work.get(scraper.username, {}).get(idx, {"course": None, "items": []}), "lecture_task", idx)

    def scores(scraper, idx, listing=None):
        return read(pages.scores.get(scraper.username, {}).get(idx, {"course": None, "components": [], "total": None}), "lecture_score", idx)

    monkeypatch.setattr(attendance.courses, "list_courses", lambda scraper: DASHBOARD)
    monkeypatch.setattr(attendance.courses, "course_items", items)
    monkeypatch.setattr(attendance.courses, "course_scores", scores)
    return pages


def test_watched_fields_keep_the_pair_together_and_drop_retired_names():
    assert watched_fields(("course_type", "credits", "retake")) == []
    assert watched_fields(("grade",)) == ["final_score", "grade"]
    assert watched_fields(("retake", "credits", "final_score")) == ["final_score", "grade"]


def test_work_snapshot_lists_every_work_of_each_course(course_tabs):
    course_tabs.work["M1"] = {"7": {"course": "Math", "items": [work()]}, "8": {"course": "Physics", "items": []}}

    assert take_work_snapshot(SimpleNamespace(username="M1"), []) == [
        {"lec_open_idx": "7", "course": "Math", "works": [work()], "site_url": tab_url("lecture_task", "7")},
        {"lec_open_idx": "8", "course": "Physics", "works": [], "site_url": tab_url("lecture_task", "8")},
    ]


def test_new_and_changed_work_are_reported_and_unchanged_work_is_not():
    previous = [{"lec_open_idx": "7", "course": "Math", "works": [work(id="5")]}]
    current = [{"lec_open_idx": "7", "course": "Math", "works": [work(id="5", end="03.11.2026"), work(id="6", task_type="Report", number="2")]}]

    changes = find_work_changes(previous, current)

    assert [(change.changed, change.work["id"]) for change in changes] == [({"end_date": ("27.10.2026", "03.11.2026")}, "5"), (None, "6")]
    assert find_work_changes(current, current) == []


def test_a_work_without_an_id_is_matched_by_its_content_not_its_row_number():
    previous = [{"lec_open_idx": "7", "course": "Math", "works": [work(id=None, number="1")]}]

    renumbered = [{"lec_open_idx": "7", "course": "Math", "works": [work(id=None, number="2")]}]
    assert find_work_changes(previous, renumbered) == []

    added_above = [{"lec_open_idx": "7", "course": "Math", "works": [work("Report", id=None, number="1"), work(id=None, number="2")]}]
    assert [change.work["task_type"] for change in find_work_changes(previous, added_above)] == ["Report"]


def test_a_course_seen_for_the_first_time_is_recorded_not_announced():
    known = [{"lec_open_idx": "7", "course": "Math", "works": []}]
    current = [{"lec_open_idx": "7", "course": "Math", "works": [work()]}, {"lec_open_idx": "8", "course": "Physics", "works": [work()]}]

    assert [change.course for change in find_work_changes(known, current)] == ["Math"]
    assert find_work_changes([], current) == []
    assert find_score_changes([], [{"lec_open_idx": "7", "course": "Math", "components": [component("a", "10", "5")], "total": "5"}]) == []
    assert find_attendance_changes([], [{"lec_open_idx": "7", "course": "Math", "sessions": [{"number": "1", "date": None, "mark": "absent"}]}]) == []


def test_a_work_message_names_each_new_and_changed_work_and_links_to_the_tasks_tab():
    link = CourseLink("7", tab_url("lecture_task", "7"))
    changes = [
        WorkChange("Math", work(), None, link),
        WorkChange("Math", work(end="03.11.2026"), {"end_date": ("27.10.2026", "03.11.2026")}, link),
    ]

    subject, body = format_work_message(changes, FRONTEND)

    assert subject == "AZTUSP: individual work updated for Math"
    assert body == (
        "Course: Math\nNew work\n  Type: Essay\n  Evaluation: 10\n  Topic: Lecture 3\n  Start: 20.10.2026\n  End: 27.10.2026\n"
        "Changed work\n  End: 03.11.2026 (was 27.10.2026)\n"
        f"Open in AZTUSP: {FRONTEND}/courses/7?tab=tasks\nOpen on the university site: {tab_url('lecture_task', '7')}"
    )


def test_a_course_that_cannot_be_read_keeps_its_work(course_tabs):
    last = [{"lec_open_idx": "7", "course": "Math", "works": [work()]}]
    course_tabs.work["M1"] = {"7": requests.ConnectionError("portal slow")}

    # Course 7 keeps its last entry; course 8 has no page here, so it is read as an empty tab.
    assert take_work_snapshot(SimpleNamespace(username="M1"), last) == [
        last[0],
        {"lec_open_idx": "8", "course": "Physics [M1]", "works": [], "site_url": tab_url("lecture_task", "8")},
    ]


def test_score_snapshot_keeps_each_component_and_the_total(course_tabs):
    course_tabs.scores["M1"] = {"7": {"course": "Math", "components": [component("sərbəst iş", "10", "5")], "total": "5"}}

    assert take_score_snapshot(SimpleNamespace(username="M1"), []) == [
        {
            "lec_open_idx": "7",
            "course": "Math",
            "components": [component("sərbəst iş", "10", "5")],
            "total": "5",
            "site_url": tab_url("lecture_score", "7"),
        },
        {"lec_open_idx": "8", "course": "Physics [M1]", "components": [], "total": None, "site_url": tab_url("lecture_score", "8")},
    ]


def test_changed_scores_and_total_are_reported_but_a_score_that_disappears_is_not():
    previous = [{"lec_open_idx": "7", "course": "Math", "components": [component("sərbəst iş", "10", "5"), component("Davamiyyət", None, "2")], "total": "7"}]
    current = [
        {
            "lec_open_idx": "7",
            "course": "Math",
            "components": [component("sərbəst iş", "10", "7"), component("Məşğələ", "30", "12"), component("Davamiyyət", None, None)],
            "total": "9",
        }
    ]

    changes = find_score_changes(previous, current)

    assert [(c.name, c.maximum, c.old, c.new) for c in changes] == [
        ("sərbəst iş", "10", "5", "7"),
        ("Məşğələ", "30", None, "12"),
        ("Total", None, "7", "9"),
    ]


def test_a_score_that_was_empty_and_now_shows_zero_is_reported():
    link = CourseLink("7", tab_url("lecture_score", "7"))
    empty = [{"lec_open_idx": "7", "course": "Math", "components": [component("Davamiyyət", None, None)], "total": None, "site_url": link.site_url}]
    zero = [{"lec_open_idx": "7", "course": "Math", "components": [component("Davamiyyət", None, "0")], "total": None, "site_url": link.site_url}]

    assert find_score_changes(empty, zero) == [ScoreChange("Math", "Davamiyyət", None, None, "0", link)]
    assert format_score_message(find_score_changes(empty, zero), FRONTEND)[1] == (
        f"Course: Math\nDavamiyyət: 0\nOpen in AZTUSP: {FRONTEND}/courses/7?tab=scores\nOpen on the university site: {link.site_url}"
    )
    assert find_score_changes(empty, empty) == []


def test_a_score_message_names_the_course_and_each_score_that_changed():
    link = CourseLink("7", tab_url("lecture_score", "7"))
    changes = [
        ScoreChange("Math", "sərbəst iş", "10", "5", "7", link),
        ScoreChange("Math", "Məşğələ", "30", None, "12", link),
        ScoreChange("Math", "Total", None, "7", "9", link),
    ]

    subject, body = format_score_message(changes, FRONTEND)

    assert subject == "AZTUSP: course scores updated for Math"
    assert body == (
        "Course: Math\nsərbəst iş (of 10): 7 (was 5)\nMəşğələ (of 30): 12\nTotal: 9 (was 7)\n"
        f"Open in AZTUSP: {FRONTEND}/courses/7?tab=scores\nOpen on the university site: {link.site_url}"
    )


def test_a_new_individual_work_goes_to_that_students_channels(portal, course_tabs, sent):
    course_tabs.work["M1"] = {"7": {"course": "Math", "items": []}, "8": {"course": "Physics", "items": []}}
    watcher = Watcher(SETTINGS, WATCH, FakeStore([subscription(1, fields=(), individual_work=True)]))
    watcher.check_all()

    course_tabs.work["M1"]["7"] = {"course": "Math", "items": [work()]}
    watcher.check_all()

    assert sent == [
        (
            "student@example.com",
            "42",
            "AZTUSP: individual work updated for Math",
            "Course: Math\nNew work\n  Type: Essay\n  Evaluation: 10\n  Topic: Lecture 3\n  Start: 20.10.2026\n  End: 27.10.2026\n"
            f"Open in AZTUSP: {FRONTEND}/courses/7?tab=tasks\nOpen on the university site: {tab_url('lecture_task', '7')}",
        )
    ]


def test_a_changed_course_score_goes_to_that_students_channels(portal, course_tabs, sent):
    course_tabs.scores["M1"] = {"7": {"course": "Math", "components": [component("sərbəst iş", "10", "5")], "total": "5"}, "8": {"course": "Physics", "components": [], "total": None}}
    watcher = Watcher(SETTINGS, WATCH, FakeStore([subscription(1, fields=(), course_scores=True)]))
    watcher.check_all()

    course_tabs.scores["M1"]["7"] = {"course": "Math", "components": [component("sərbəst iş", "10", "7")], "total": "7"}
    watcher.check_all()

    assert [(subject, body) for _, _, subject, body in sent] == [
        (
            "AZTUSP: course scores updated for Math",
            "Course: Math\nsərbəst iş (of 10): 7 (was 5)\nTotal: 7 (was 5)\n"
            f"Open in AZTUSP: {FRONTEND}/courses/7?tab=scores\nOpen on the university site: {tab_url('lecture_score', '7')}",
        )
    ]


def test_a_student_with_nothing_left_to_watch_is_not_signed_in(portal, sent):
    Watcher(SETTINGS, WATCH, FakeStore([subscription(1, fields=("course_type", "credits"))])).check_all()

    assert portal.logins == []
    assert sent == []


DATABASE_URL = os.getenv("TEST_DATABASE_URL")
needs_database = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def store():
    store = UserStore(DATABASE_URL, Fernet.generate_key().decode())
    with store._connect() as db:
        db.execute("TRUNCATE users CASCADE")
    return store


@needs_database
def test_notification_settings_round_trip_and_the_password_is_encrypted(store):
    user = store.get_by_token(store.upsert_login("M1", "{}", "site-pass"))

    store.set_notifications(user.id, Notifications("student@example.com", ["grade"]))

    saved = store.get_notifications(user.id)
    assert (saved.email, saved.fields, saved.status, saved.telegram_linked) == ("student@example.com", ["grade"], "ok", False)
    [subscription] = store.subscribers()
    assert (subscription.user_id, subscription.site_username, subscription.password, subscription.telegram_chat_id) == (
        user.id,
        "M1",
        "site-pass",
        None,
    )
    with store._connect() as db:
        stored = db.execute("SELECT password_enc FROM users").fetchone()[0]
    assert b"site-pass" not in stored


@needs_database
def test_a_connected_telegram_chat_is_used_for_notifications(store):
    user = store.get_by_token(store.upsert_login("M1", "{}", "site-pass"))
    store.set_notifications(user.id, Notifications(None, ["grade"]))

    store.set_telegram_chat(user.id, "42")

    assert store.get_notifications(user.id).telegram_linked is True
    [subscription] = store.subscribers()
    assert subscription.telegram_chat_id == "42"


@needs_database
def test_link_codes_work_once_and_expire(store):
    user = store.get_by_token(store.upsert_login("M1", "{}", "site-pass"))

    code, _ = store.create_link_code(user.id)
    assert store.redeem_link_code(code) == user.id
    assert store.redeem_link_code(code) is None

    code, _ = store.create_link_code(user.id)
    with store._connect() as db:
        db.execute("UPDATE telegram_link_codes SET expires_at = now() - interval '1 minute' WHERE code = %s", (code,))
    assert store.redeem_link_code(code) is None


@needs_database
def test_a_chat_belongs_to_one_account_at_a_time(store):
    first = store.get_by_token(store.upsert_login("M1", "{}", "site-pass"))
    second = store.get_by_token(store.upsert_login("M2", "{}", "site-pass"))

    store.set_telegram_chat(first.id, "42")
    store.set_telegram_chat(second.id, "42")

    assert store.get_telegram_chat(first.id) is None
    assert store.get_telegram_chat(second.id) == "42"


@needs_database
def test_a_wrong_password_status_clears_when_settings_are_saved_again(store):
    user = store.get_by_token(store.upsert_login("M1", "{}", "old-pass"))
    store.set_notifications(user.id, Notifications("student@example.com", ["grade"]))

    store.record_check(user.id, "wrong_password")
    assert store.get_notifications(user.id).status == "wrong_password"

    store.set_notifications(user.id, Notifications("student@example.com", ["grade"]))
    assert store.get_notifications(user.id).status == "ok"


@needs_database
def test_a_new_saved_password_clears_a_wrong_password_status(store):
    user = store.get_by_token(store.upsert_login("M1", "{}", "old-pass"))
    store.set_notifications(user.id, Notifications("student@example.com", ["grade"]))
    store.record_check(user.id, "wrong_password")

    store.save_password(user.id, "new-pass")

    assert store.get_notifications(user.id).status == "ok"
    assert store.saved_password(user.id) == "new-pass"
    assert [subscription.password for subscription in store.subscribers()] == ["new-pass"]


@needs_database
def test_turning_notifications_off_keeps_the_password_and_deletes_the_results(store):
    user = store.get_by_token(store.upsert_login("M1", "{}", "site-pass"))
    store.set_notifications(user.id, Notifications("student@example.com", ["grade"]))
    store.save_snapshot(user.id, {"fields": ["grade"], "courses": []})

    store.delete_notifications(user.id)

    assert store.get_notifications(user.id) is None
    assert store.load_snapshot(user.id) is None
    assert store.subscribers() == []
    assert store.saved_password(user.id) == "site-pass"


@needs_database
@needs_database
def test_course_watches_are_saved_and_turning_one_off_forgets_only_its_results(store):
    user = store.get_by_token(store.upsert_login("M1", "{}", "site-pass"))
    everything = Notifications("student@example.com", ["grade"], attendance=True, individual_work=True, course_scores=True)
    store.set_notifications(user.id, everything)
    for watch in ("attendance", "work", "scores"):
        store.save_course_snapshot(user.id, watch, {"courses": []})
    store.save_snapshot(user.id, {"fields": ["final_score", "grade"], "courses": []})

    [subscription] = store.subscribers()
    flags = (subscription.notifications.attendance, subscription.notifications.individual_work, subscription.notifications.course_scores)
    assert flags == (True, True, True)
    saved = store.get_notifications(user.id)
    assert (saved.attendance, saved.individual_work, saved.course_scores) == (True, True, True)

    store.set_notifications(user.id, Notifications("student@example.com", ["grade"], attendance=True))

    assert store.get_notifications(user.id).individual_work is False
    assert store.load_course_snapshot(user.id, "attendance") == {"courses": []}
    assert store.load_course_snapshot(user.id, "work") is None
    assert store.load_course_snapshot(user.id, "scores") is None
    assert store.load_snapshot(user.id) is not None  # the grades are still watched

    store.delete_notifications(user.id)
    assert store.load_course_snapshot(user.id, "attendance") is None


@needs_database
def test_absence_snapshots_from_the_old_table_move_to_course_snapshots(store):
    user = store.get_by_token(store.upsert_login("M1", "{}", "site-pass"))
    with store._connect() as db:
        # The table the previous version kept absence marks in.
        db.execute("CREATE TABLE absence_snapshots (user_id BIGINT PRIMARY KEY, data JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        db.execute("INSERT INTO absence_snapshots (user_id, data) VALUES (%s, %s)", (user.id, Jsonb({"courses": [{"lec_open_idx": "7"}]})))

    UserStore(DATABASE_URL, Fernet.generate_key().decode())  # starts up on the old database, as the API does

    assert store.load_course_snapshot(user.id, "attendance") == {"courses": [{"lec_open_idx": "7"}]}
    with store._connect() as db:
        assert db.execute("SELECT to_regclass('absence_snapshots')").fetchone()[0] is None


@needs_database
def test_the_absences_setting_and_snapshots_keep_their_values_under_the_attendance_name(store):
    user = store.get_by_token(store.upsert_login("M1", "{}", "site-pass"))
    store.set_notifications(user.id, Notifications("student@example.com", ["grade"], attendance=True))
    store.save_course_snapshot(user.id, "attendance", {"courses": [{"lec_open_idx": "7"}]})
    with store._connect() as db:
        # What the previous version stored under the old names.
        db.execute("ALTER TABLE notification_settings RENAME COLUMN attendance TO absences")
        db.execute("UPDATE course_snapshots SET watch = 'absences'")

    store._create_schema()  # starts up on the old database, as the API does

    assert store.get_notifications(user.id).attendance is True
    assert store.load_course_snapshot(user.id, "attendance") == {"courses": [{"lec_open_idx": "7"}]}
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM course_snapshots WHERE watch = 'absences'").fetchone()[0] == 0
        assert db.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'notification_settings' AND column_name = 'absences'"
        ).fetchone()[0] == 0


@needs_database
def test_an_old_notification_password_moves_to_the_user_on_start(store):
    """Databases from before the login password was stored kept the password per notification setting."""
    user = store.get_by_token(store.upsert_login("M1", "{}", "unused"))
    with store._connect() as db:
        db.execute("ALTER TABLE notification_settings ADD COLUMN password_enc BYTEA")
        db.execute("UPDATE users SET password_enc = NULL")
    store.set_notifications(user.id, Notifications("student@example.com", ["grade"]))
    with store._connect() as db:
        db.execute("UPDATE notification_settings SET password_enc = %s", (store.fernet.encrypt(b"old-pass"),))

    store._create_schema()

    assert store.saved_password(user.id) == "old-pass"
    with store._connect() as db:
        column = db.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'notification_settings' AND column_name = 'password_enc'"
        ).fetchone()
    assert column is None


@needs_database
def test_an_expired_token_leaves_the_saved_password_for_the_watcher(store):
    token = store.upsert_login("M1", "{}", "site-pass")
    user = store.get_by_token(token)
    store.set_notifications(user.id, Notifications("student@example.com", ["grade"]))
    with store._connect() as db:
        db.execute("UPDATE users SET token_expires_at = now() - interval '1 minute'")

    assert store.get_by_token(token) is None
    [subscription] = store.subscribers()
    assert subscription.password == "site-pass"
