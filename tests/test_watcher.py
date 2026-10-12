"""Watcher tests: result changes and absence marks. The database tests need TEST_DATABASE_URL, otherwise they are skipped."""

import os
import ssl
from dataclasses import replace
from types import SimpleNamespace

import pytest
import requests
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet

from app.config import ConfigError, Settings
from app.db import Notifications, Subscription, UserStore
from app.schemas import NotificationSettings
from app.scraping.client import BadCredentials, SiteScraper
from app.scraping.targets import TARGETS_BY_NAME
from app.watcher import absences, checker
from app.watcher import notify as notify_module
from app.watcher.absences import AbsenceChange, find_absence_changes, format_absence_message, take_absence_snapshot
from app.watcher.checker import Watcher
from app.watcher.config import SmtpSettings, WatcherSettings
from app.watcher.notify import NotifyError, format_message, send_email, send_telegram
from app.watcher.results import Change, find_changes, take_snapshot

FIELDS = ("final_score", "grade")
SEMESTER = "2026 Tədris ili payiz Semestr"
SETTINGS = Settings("k", "db", "example.com", "https://login.example.com/", "https://d.example.com/app/", "u", "p", 5.0)
SMTP = SmtpSettings(host="smtp-relay.brevo.com", port=587, username="login@example.com", password="smtp-key", sender="sender@example.com")
WATCH = WatcherSettings(interval_seconds=1800, smtp=SMTP, telegram_bot_token="123:ABC")

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


def test_format_message_names_the_course_and_the_result():
    subject, body = format_message(
        [Change(SEMESTER, "Algorithms", "final_score", "70", "78"), Change(SEMESTER, "Algorithms", "grade", None, "B")]
    )

    assert subject == "AZTUSP: result updated for Algorithms"
    assert body == f"Course: Algorithms\nSemester: {SEMESTER}\nFinal score: 78 (was 70)\nGrade: B"


def test_format_message_counts_courses_in_the_subject():
    subject, body = format_message([Change(SEMESTER, "Algorithms", "grade", None, "B"), Change(SEMESTER, "Databases", "grade", None, "A")])

    assert subject == "AZTUSP: results updated for 2 courses"
    assert "Course: Algorithms" in body and "Course: Databases" in body


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
    for key in ("WATCH_INTERVAL_MINUTES", "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "MAIL_FROM", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_defaults_check_every_30_minutes_with_telegram_only(clean_env):
    clean_env.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")

    settings = WatcherSettings.from_env()

    assert settings.interval_seconds == 1800
    assert settings.smtp is None
    assert settings.telegram_bot_token == "123:ABC"


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
        self.absence_snapshots = {}
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

    def load_absence_snapshot(self, user_id):
        return self.absence_snapshots.get(user_id)

    def save_absence_snapshot(self, user_id, data):
        self.absence_snapshots[user_id] = data

    def record_check(self, user_id, status):
        self.statuses[user_id] = status


def subscription(
    user_id, username="M1", password="pw", fields=FIELDS, status="ok", email="student@example.com", chat="42", absences=False
):
    notifications = Notifications(email, list(fields), status, telegram_linked=chat is not None, absences=absences)
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
            f"Course: Algorithms\nSemester: {SEMESTER}\nFinal score: 78 (was 70)\nGrade: B (was C)",
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
        return page

    monkeypatch.setattr(absences.courses, "list_courses", lambda scraper: DASHBOARD)
    monkeypatch.setattr(absences.courses, "course_attendance", read)
    return journals


def test_new_absences_and_corrections_are_the_only_changes():
    previous = [
        {
            "lec_open_idx": "7",
            "course": "Math",
            "sessions": [
                {"number": "1", "date": "15.09", "mark": "present"},
                {"number": "2", "date": "22.09", "mark": "absent"},
                {"number": "3", "date": "29.09", "mark": "not_entered"},
                {"number": "4", "date": "06.10", "mark": None},
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
                {"number": "4", "date": "06.10", "mark": "present"},  # blank -> present: not reported
                {"number": "5", "date": "13.10", "mark": "absent"},  # a new meeting, absent
                {"number": "6", "date": "20.10", "mark": "present"},  # a new meeting, present: not reported
            ],
        }
    ]

    changes = find_absence_changes(previous, current)

    assert [(c.session, c.old, c.new) for c in changes] == [
        ("1", "present", "absent"),
        ("2", "absent", "present"),
        ("3", "not_entered", "absent"),
        ("5", None, "absent"),
    ]
    assert find_absence_changes(current, current) == []


def test_absence_message_names_the_course_and_each_meeting_that_changed():
    changes = [AbsenceChange("Math", "2", "22.09", None, "absent"), AbsenceChange("Math", "1", None, "absent", "present")]

    subject, body = format_absence_message(changes)

    assert subject == "AZTUSP: attendance updated for Math"
    assert body == "Course: Math\nSession 2 (22.09): absent\nSession 1: present (was absent)"


def test_absence_message_counts_courses_in_the_subject():
    changes = [AbsenceChange("Math", "2", None, None, "absent"), AbsenceChange("Physics", "1", None, None, "absent")]

    assert format_absence_message(changes)[0] == "AZTUSP: attendance updated for 2 courses"


def test_own_marks_are_read_for_each_course_and_other_students_are_ignored(campus):
    campus["M1"] = {
        "7": journal("Math", ("15.09", "present"), ("22.09", "absent"), ("29.09", None), others=("S2",)),
        "8": journal("Physics", ("16.09", "absent"), student="m1"),
    }

    snapshot = take_absence_snapshot(SimpleNamespace(username="M1"), [])

    assert snapshot == [
        {
            "lec_open_idx": "7",
            "course": "Math",
            "sessions": [
                {"number": "1", "date": "15.09", "mark": "present"},
                {"number": "2", "date": "22.09", "mark": "absent"},
                {"number": "3", "date": "29.09", "mark": None},
            ],
        },
        {"lec_open_idx": "8", "course": "Physics", "sessions": [{"number": "1", "date": "16.09", "mark": "absent"}]},
    ]


def test_a_course_that_cannot_be_read_keeps_its_last_marks(campus):
    last = [
        {"lec_open_idx": "7", "course": "Math", "sessions": [{"number": "1", "date": "15.09", "mark": "present"}]},
        {"lec_open_idx": "8", "course": "Physics", "sessions": [{"number": "1", "date": "16.09", "mark": "absent"}]},
        {"lec_open_idx": "9", "course": "Old course", "sessions": []},  # no longer on the dashboard: dropped
    ]
    campus["M1"] = {"7": requests.ConnectionError("portal slow"), "8": journal("Physics", ("16.09", "present"), student="S9")}

    assert take_absence_snapshot(SimpleNamespace(username="M1"), last) == last[:2]


def test_first_absence_check_saves_the_marks_without_notifying(portal, campus, sent):
    campus["M1"] = {"7": journal("Math", ("15.09", "absent")), "8": journal("Physics", ("16.09", "present"))}
    store = FakeStore([subscription(1, fields=(), absences=True)])

    Watcher(SETTINGS, WATCH, store).check_all()

    assert sent == []
    assert store.absence_snapshots[1]["courses"][0]["sessions"] == [{"number": "1", "date": "15.09", "mark": "absent"}]
    assert store.statuses[1] == "ok"


def test_a_new_absence_goes_to_that_students_channels(portal, campus, sent):
    campus["M1"] = {"7": journal("Math", ("15.09", "present")), "8": journal("Physics", ("16.09", "present"))}
    watcher = Watcher(SETTINGS, WATCH, FakeStore([subscription(1, fields=(), absences=True)]))
    watcher.check_all()

    campus["M1"]["7"] = journal("Math", ("15.09", "present"), ("22.09", "absent"))
    watcher.check_all()

    assert sent == [("student@example.com", "42", "AZTUSP: attendance updated for Math", "Course: Math\nSession 2 (22.09): absent")]


def test_an_absences_only_student_is_not_read_for_grades(portal, campus, sent):
    # The portal fixture has no scores pages for this student, so reading them would fail the check.
    campus["M1"] = {"7": journal("Math", ("15.09", "present")), "8": journal("Physics", ("16.09", "present"))}
    store = FakeStore([subscription(1, fields=(), absences=True)])

    Watcher(SETTINGS, WATCH, store).check_all()

    assert store.statuses[1] == "ok"
    assert sent == []


def test_grade_and_absence_changes_are_sent_as_separate_messages(portal, campus, sent):
    portal.pages["M1"] = [scores_page(row("Algorithms", "70", "C")), scores_page(row("Algorithms", "78", "B"))]
    campus["M1"] = {"7": journal("Math", ("15.09", "present")), "8": journal("Physics", ("16.09", "present"))}
    watcher = Watcher(SETTINGS, WATCH, FakeStore([subscription(1, absences=True)]))
    watcher.check_all()

    campus["M1"]["8"] = journal("Physics", ("16.09", "absent"))
    watcher.check_all()

    assert [subject for _, _, subject, _ in sent] == ["AZTUSP: result updated for Algorithms", "AZTUSP: attendance updated for Physics"]


def test_an_unreadable_course_is_not_reported_as_its_absences_going_or_coming_back(portal, campus, sent):
    campus["M1"] = {"7": journal("Math", ("15.09", "absent")), "8": journal("Physics", ("16.09", "present"))}
    watcher = Watcher(SETTINGS, WATCH, FakeStore([subscription(1, fields=(), absences=True)]))
    watcher.check_all()

    campus["M1"]["7"] = requests.ConnectionError("portal slow")
    watcher.check_all()
    campus["M1"]["7"] = journal("Math", ("15.09", "absent"))
    watcher.check_all()

    assert sent == []


def test_absence_watch_can_be_on_without_any_result_field():
    assert NotificationSettings(email=None, fields=[], absences=True).absences is True
    with pytest.raises(ValueError):
        NotificationSettings(email=None, fields=[])


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
def test_absence_watch_is_saved_and_turning_it_off_forgets_its_marks(store):
    user = store.get_by_token(store.upsert_login("M1", "{}", "site-pass"))
    store.set_notifications(user.id, Notifications("student@example.com", ["grade"], absences=True))
    store.save_absence_snapshot(user.id, {"courses": []})

    [subscription] = store.subscribers()
    assert subscription.notifications.absences is True
    assert store.get_notifications(user.id).absences is True
    assert store.load_absence_snapshot(user.id) == {"courses": []}

    store.set_notifications(user.id, Notifications("student@example.com", ["grade"]))

    assert store.get_notifications(user.id).absences is False
    assert store.load_absence_snapshot(user.id) is None


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
