"""API tests. Need a Postgres: set TEST_DATABASE_URL, otherwise they are skipped."""

import os
from dataclasses import replace

import pytest
import requests
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.api import deps
from app.config import Settings
from app.db import UserStore
from app.main import app
from app.photos import MAX_PHOTO_BYTES
from app.scraping import courses, password_form
from app.scraping.client import LoginError, SiteScraper

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not set")

SETTINGS = Settings("k", "db", "example.com", "https://l.example.com/", "https://d.example.com/", "u", "p", 5.0)


@pytest.fixture
def store():
    store = UserStore(DATABASE_URL, Fernet.generate_key().decode())
    with store._connect() as db:
        db.execute("TRUNCATE users CASCADE")
    return store


@pytest.fixture
def client(store, monkeypatch):
    app.dependency_overrides[deps.get_settings] = lambda: SETTINGS
    app.dependency_overrides[deps.get_store] = lambda: store

    def fake_login(self, password):
        if password != "good":
            raise LoginError("bad password")

    monkeypatch.setattr(SiteScraper, "login", fake_login)
    monkeypatch.setattr(SiteScraper, "log_out", lambda self: None)
    monkeypatch.setattr(
        SiteScraper,
        "scrape",
        lambda self, target: {"name": target.name, "url": "u", "tables": {}, "pairs": {}, "totals": {}, "sections": {}, "fields": {}},
    )
    monkeypatch.setattr(courses, "list_courses", lambda scraper: [{"lec_open_idx": "7", "sem_code": None, "name": "Math", "path": "/p"}])
    monkeypatch.setattr(
        courses,
        "lecture_plan",
        lambda scraper, idx: {"params": {"lec_open_idx": idx}, "course": "Math", "semester": None, "info": None, "blocks": [{"title": "T", "text": "x"}]},
    )
    base = {"params": {"lec_open_idx": "7"}, "course": "Math ( M1 )"}
    monkeypatch.setattr(courses, "course_items", lambda scraper, idx, name: {**base, "items": [{"subject": name}]})
    monkeypatch.setattr(
        courses, "course_scores", lambda scraper, idx: {**base, "table": [{"Quiz(10)": "0"}], "components": [{"name": "Quiz", "max": "10", "score": "0"}], "total": "0", "notes": []}
    )
    monkeypatch.setattr(
        courses,
        "course_attendance",
        lambda scraper, idx: {
            **base,
            "info": {},
            "legend": {},
            "header": {"score": "0", "percent": "100%"},
            "sessions": [{"number": "1", "date": None, "journal_date": None}],
            "students": [
                {"number": "1", "student_id": "u1", "name": "N", "marks": [{"session": "1", "status": "i", "mark": "present"}], "score": "0", "percent": "100"}
            ],
        },
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def login(client) -> dict:
    token = client.post("/api/v1/auth/login", json={"username": "u1", "password": "good"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_login_rejects_bad_password(client):
    assert client.post("/api/v1/auth/login", json={"username": "u1", "password": "bad"}).status_code == 401


@pytest.mark.parametrize(
    ("path", "name"),
    [("profile", "student"), ("scores", "scores"), ("schedule", "schedule"), ("notices", "notices")],
)
def test_me_pages_scrape_on_request(client, path, name):
    response = client.get(f"/api/v1/me/{path}", headers=login(client))
    assert response.status_code == 200
    assert response.json()["name"] == name


def test_each_request_scrapes_again(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        SiteScraper,
        "scrape",
        lambda self, target: calls.append(target.name) or {"name": target.name, "url": "u", "tables": {}, "pairs": {}, "totals": {}, "sections": {}, "fields": {}},
    )
    headers = login(client)
    client.get("/api/v1/me/notices", headers=headers)
    client.get("/api/v1/me/notices", headers=headers)
    assert calls == ["notices", "notices"]


def test_courses(client):
    headers = login(client)
    assert client.get("/api/v1/courses", headers=headers).json()[0]["lec_open_idx"] == "7"
    assert client.get("/api/v1/courses/7/plan", headers=headers).json()["blocks"] == [{"title": "T", "text": "x"}]


@pytest.mark.parametrize("name", ["notices", "board", "materials", "tasks"])
def test_course_list_tabs(client, name):
    response = client.get(f"/api/v1/courses/7/{name}", headers=login(client))
    assert response.status_code == 200
    assert response.json()["items"] == [{"subject": name}]


def test_course_scores_and_attendance(client):
    headers = login(client)
    scores = client.get("/api/v1/courses/7/scores", headers=headers).json()
    assert scores["table"] == [{"Quiz(10)": "0"}] and scores["components"][0]["score"] == "0"
    attendance = client.get("/api/v1/courses/7/attendance", headers=headers).json()
    assert attendance["students"][0]["marks"][0]["mark"] == "present" and attendance["header"]["percent"] == "100%"


def test_expired_site_session_is_401(client, monkeypatch):
    headers = login(client)
    monkeypatch.setattr(SiteScraper, "scrape", lambda self, target: (_ for _ in ()).throw(LoginError("Site session expired")))
    assert client.get("/api/v1/me/scores", headers=headers).status_code == 401


def test_logout_invalidates_token(client):
    headers = login(client)
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/v1/me/scores", headers=headers).status_code == 401


def test_logout_also_logs_out_of_the_university_site(client, monkeypatch):
    headers = login(client)
    logged_out = []
    monkeypatch.setattr(SiteScraper, "log_out", lambda self: logged_out.append(self.username))

    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert logged_out == ["u1"]


def test_logout_ends_the_session_when_the_university_site_is_down(client, monkeypatch):
    headers = login(client)

    def site_down(self):
        raise requests.ConnectionError("site down")

    monkeypatch.setattr(SiteScraper, "log_out", site_down)

    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/v1/me/scores", headers=headers).status_code == 401


@pytest.mark.parametrize("path", ["/api/v1/me/scores", "/api/v1/courses", "/api/v1/courses/1/plan"])
def test_protected_routes_require_token(client, path):
    assert client.get(path).status_code == 401


def test_docs_expose_only_new_names(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {"/health", "/api/v1/auth/login", "/api/v1/me/scores", "/api/v1/courses"} <= paths
    assert not any(not p.startswith("/api/v1/") and p != "/health" for p in paths)
    assert not any("sync" in p for p in paths)



NOTIFICATIONS = {"email": "student@example.com", "fields": ["grade"]}


def user_of(store, headers):
    return store.get_by_token(headers["Authorization"].removeprefix("Bearer "))


def test_notifications_are_off_until_turned_on(client):
    assert client.get("/api/v1/me/notifications", headers=login(client)).status_code == 404


def test_turning_notifications_on_never_returns_the_password(client):
    headers = login(client)

    response = client.put("/api/v1/me/notifications", json=NOTIFICATIONS, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "student@example.com" and body["fields"] == ["grade"]
    assert body["status"] == "ok" and body["last_checked_at"] is None and body["telegram_linked"] is False
    assert "password" not in body and "good" not in response.text
    assert client.get("/api/v1/me/notifications", headers=headers).json() == body


def test_a_saved_password_the_site_rejects_blocks_notifications(client, store):
    headers = login(client)
    store.save_password(user_of(store, headers).id, "bad")  # as if the password was changed on the site

    assert client.put("/api/v1/me/notifications", json=NOTIFICATIONS, headers=headers).status_code == 401
    assert client.get("/api/v1/me/notifications", headers=headers).status_code == 404


def test_an_email_or_a_connected_telegram_is_required(client, store):
    headers = login(client)
    without_email = {**NOTIFICATIONS, "email": None}

    assert client.put("/api/v1/me/notifications", json=without_email, headers=headers).status_code == 422

    store.set_telegram_chat(user_of(store, headers).id, "42")
    response = client.put("/api/v1/me/notifications", json=without_email, headers=headers)
    assert response.status_code == 200 and response.json()["telegram_linked"] is True


@pytest.mark.parametrize("change", [{"fields": ["nope"]}, {"fields": []}, {"email": "not-an-address"}])
def test_invalid_notification_settings_are_rejected(client, change):
    response = client.put("/api/v1/me/notifications", json={**NOTIFICATIONS, **change}, headers=login(client))

    assert response.status_code == 422


def test_turning_notifications_off(client):
    headers = login(client)
    client.put("/api/v1/me/notifications", json=NOTIFICATIONS, headers=headers)

    assert client.delete("/api/v1/me/notifications", headers=headers).json() == {"ok": True}
    assert client.get("/api/v1/me/notifications", headers=headers).status_code == 404


def test_link_gives_a_one_time_telegram_url(client):
    app.dependency_overrides[deps.get_settings] = lambda: replace(SETTINGS, telegram_bot_username="AztuGradeBot")

    response = client.post("/api/v1/me/telegram/link", headers=login(client))

    assert response.status_code == 200
    assert response.json()["url"].startswith("https://t.me/AztuGradeBot?start=")


def test_link_needs_the_bot_username_on_the_server(client):
    assert client.post("/api/v1/me/telegram/link", headers=login(client)).status_code == 503


def test_telegram_status_and_disconnect(client, store):
    headers = login(client)
    assert client.get("/api/v1/me/telegram", headers=headers).json() == {"linked": False}

    store.set_telegram_chat(user_of(store, headers).id, "42")
    assert client.get("/api/v1/me/telegram", headers=headers).json() == {"linked": True}

    assert client.delete("/api/v1/me/telegram", headers=headers).json() == {"ok": True}
    assert client.get("/api/v1/me/telegram", headers=headers).json() == {"linked": False}


def test_disconnecting_telegram_needs_an_email_when_notifications_are_on(client, store):
    headers = login(client)
    store.set_telegram_chat(user_of(store, headers).id, "42")
    client.put("/api/v1/me/notifications", json={**NOTIFICATIONS, "email": None}, headers=headers)

    assert client.delete("/api/v1/me/telegram", headers=headers).status_code == 409


def test_notification_routes_require_a_token(client):
    assert client.put("/api/v1/me/notifications", json=NOTIFICATIONS).status_code == 401
    assert client.get("/api/v1/me/notifications").status_code == 401
    assert client.get("/api/v1/me/telegram").status_code == 401
    assert client.post("/api/v1/me/telegram/link").status_code == 401


def test_logout_keeps_change_notifications_running(client, store):
    headers = login(client)
    client.put("/api/v1/me/notifications", json=NOTIFICATIONS, headers=headers)

    client.post("/api/v1/auth/logout", headers=headers)

    assert [subscription.password for subscription in store.subscribers()] == ["good"]
    new_headers = login(client)
    assert client.get("/api/v1/me/notifications", headers=new_headers).json()["email"] == "student@example.com"


def test_logout_drops_the_site_session_cookies(client, store):
    headers = login(client)

    client.post("/api/v1/auth/logout", headers=headers)

    with store._connect() as db:
        (cookies,) = db.execute("SELECT cookies_enc FROM users").fetchone()
    assert cookies is None


PASSWORD_CHANGE = {"password": "better-pass", "confirm_password": "better-pass"}


def fake_change_form(changed: bool):
    """Stands in for the SSO change form, so these tests never reach the site."""

    def change(scraper, password):
        return {"changed": changed, "url": "https://sso.example.com/", "messages": [] if changed else ["rules"]}

    return change


def test_a_changed_password_replaces_the_saved_one_for_the_watcher(client, store, monkeypatch):
    headers = login(client)
    client.put("/api/v1/me/notifications", json=NOTIFICATIONS, headers=headers)
    store.record_check(user_of(store, headers).id, "wrong_password")
    monkeypatch.setattr(password_form, "change_password", fake_change_form(True))

    response = client.post("/api/v1/me/password", json=PASSWORD_CHANGE, headers=headers)

    assert response.status_code == 200 and response.json()["changed"] is True
    assert [subscription.password for subscription in store.subscribers()] == ["better-pass"]
    assert client.get("/api/v1/me/notifications", headers=headers).json()["status"] == "ok"


def test_a_changed_password_is_saved_even_without_notifications(client, store, monkeypatch):
    headers = login(client)
    monkeypatch.setattr(password_form, "change_password", fake_change_form(True))

    client.post("/api/v1/me/password", json=PASSWORD_CHANGE, headers=headers)

    assert store.saved_password(user_of(store, headers).id) == "better-pass"
    assert store.subscribers() == []


def test_a_rejected_change_keeps_the_saved_password(client, store, monkeypatch):
    headers = login(client)
    client.put("/api/v1/me/notifications", json=NOTIFICATIONS, headers=headers)
    monkeypatch.setattr(password_form, "change_password", fake_change_form(False))

    response = client.post("/api/v1/me/password", json=PASSWORD_CHANGE, headers=headers)

    assert response.json() == {"changed": False, "url": "https://sso.example.com/", "messages": ["rules"]}
    assert store.saved_password(user_of(store, headers).id) == "good"


def test_login_saves_the_password_encrypted(client, store):
    headers = login(client)

    assert store.saved_password(user_of(store, headers).id) == "good"
    with store._connect() as db:
        stored = db.execute("SELECT password_enc FROM users").fetchone()[0]
    assert b"good" not in stored


def test_notifications_use_the_saved_password(client, store):
    headers = login(client)

    response = client.put("/api/v1/me/notifications", json=NOTIFICATIONS, headers=headers)

    assert response.status_code == 200
    assert [subscription.password for subscription in store.subscribers()] == ["good"]


def test_logging_in_again_saves_the_password_the_site_now_accepts(client, store):
    headers = login(client)
    client.put("/api/v1/me/notifications", json=NOTIFICATIONS, headers=headers)
    user_id = user_of(store, headers).id
    store.save_password(user_id, "bad")  # as if the password was changed on the site
    assert client.put("/api/v1/me/notifications", json=NOTIFICATIONS, headers=headers).status_code == 401

    new_headers = login(client)

    assert store.saved_password(user_id) == "good"
    assert client.put("/api/v1/me/notifications", json=NOTIFICATIONS, headers=new_headers).status_code == 200


def test_notifications_need_a_saved_password(client, store):
    headers = login(client)
    with store._connect() as db:
        db.execute("UPDATE users SET password_enc = NULL")

    assert client.put("/api/v1/me/notifications", json=NOTIFICATIONS, headers=headers).status_code == 422


def test_turning_notifications_off_keeps_the_saved_password(client, store):
    headers = login(client)
    client.put("/api/v1/me/notifications", json=NOTIFICATIONS, headers=headers)

    client.delete("/api/v1/me/notifications", headers=headers)

    assert store.saved_password(user_of(store, headers).id) == "good"


def test_a_token_lasts_one_day(client, store):
    login(client)

    with store._connect() as db:
        (days_left,) = db.execute("SELECT EXTRACT(EPOCH FROM (token_expires_at - now())) / 86400 FROM users").fetchone()
    assert 0.99 < float(days_left) <= 1


def test_a_token_stops_working_when_it_expires(client, store):
    headers = login(client)
    assert client.get("/api/v1/me/notifications", headers=headers).status_code == 404  # signed in; notifications are just off

    with store._connect() as db:
        db.execute("UPDATE users SET token_expires_at = now() - interval '1 minute'")

    assert client.get("/api/v1/me/notifications", headers=headers).status_code == 401


def test_a_token_without_an_expiry_needs_a_login(client, store):
    headers = login(client)
    with store._connect() as db:
        db.execute("UPDATE users SET token_expires_at = NULL")

    assert client.get("/api/v1/me/notifications", headers=headers).status_code == 401


PHOTO = b"\xff\xd8\xff\xe0" + b"photo-bytes"
PNG_PHOTO = b"\x89PNG\r\n\x1a\n" + b"other-bytes"


def upload(client, headers, data: bytes, content_type: str = "image/jpeg"):
    return client.put("/api/v1/me/photo", content=data, headers={**headers, "Content-Type": content_type})


def test_no_photo_until_one_is_uploaded(client):
    assert client.get("/api/v1/me/photo", headers=login(client)).status_code == 404


def test_uploaded_photo_comes_back_as_an_image(client):
    headers = login(client)

    assert upload(client, headers, PHOTO).json() == {"ok": True}

    response = client.get("/api/v1/me/photo", headers=headers)
    assert response.status_code == 200
    assert response.content == PHOTO
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_format_comes_from_the_file_not_the_header(client):
    headers = login(client)

    upload(client, headers, PNG_PHOTO, content_type="image/jpeg")

    assert client.get("/api/v1/me/photo", headers=headers).headers["content-type"] == "image/png"


def test_a_new_upload_replaces_the_photo(client):
    headers = login(client)
    upload(client, headers, PHOTO)

    upload(client, headers, PNG_PHOTO, content_type="image/png")

    assert client.get("/api/v1/me/photo", headers=headers).content == PNG_PHOTO


def test_deleting_the_photo(client):
    headers = login(client)
    upload(client, headers, PHOTO)

    assert client.delete("/api/v1/me/photo", headers=headers).json() == {"ok": True}
    assert client.get("/api/v1/me/photo", headers=headers).status_code == 404
    assert client.delete("/api/v1/me/photo", headers=headers).status_code == 200  # nothing left, still fine


def test_a_file_that_is_not_an_image_is_refused_and_the_old_photo_stays(client):
    headers = login(client)
    upload(client, headers, PHOTO)

    response = upload(client, headers, b"<html>not a photo</html>", content_type="image/png")

    assert response.status_code == 415
    assert client.get("/api/v1/me/photo", headers=headers).content == PHOTO


def test_a_photo_over_the_limit_is_refused(client):
    headers = login(client)
    too_big = b"\xff\xd8\xff" + b"0" * (MAX_PHOTO_BYTES - 2)

    assert upload(client, headers, too_big).status_code == 413
    assert client.get("/api/v1/me/photo", headers=headers).status_code == 404


def test_a_photo_at_the_limit_is_kept(client):
    headers = login(client)
    at_limit = b"\xff\xd8\xff" + b"0" * (MAX_PHOTO_BYTES - 3)

    assert upload(client, headers, at_limit).status_code == 200
    assert len(client.get("/api/v1/me/photo", headers=headers).content) == MAX_PHOTO_BYTES


def test_logout_keeps_the_photo(client):
    headers = login(client)
    upload(client, headers, PHOTO)

    client.post("/api/v1/auth/logout", headers=headers)

    assert client.get("/api/v1/me/photo", headers=login(client)).content == PHOTO


def test_photo_routes_require_a_token(client):
    assert client.put("/api/v1/me/photo", content=PHOTO).status_code == 401
    assert client.get("/api/v1/me/photo").status_code == 401
    assert client.delete("/api/v1/me/photo").status_code == 401


def test_photo_upload_documents_the_image_body(client):
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/me/photo"]["put"]

    assert set(operation["requestBody"]["content"]) == {"image/jpeg", "image/png", "image/webp"}
