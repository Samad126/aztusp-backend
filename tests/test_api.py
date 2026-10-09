"""API tests. Need a Postgres: set TEST_DATABASE_URL, otherwise they are skipped."""

import os

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.api import deps
from app.config import Settings
from app.db import UserStore
from app.main import app
from app.scraping import courses
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


def test_expired_site_session_is_401(client, monkeypatch):
    headers = login(client)
    monkeypatch.setattr(SiteScraper, "scrape", lambda self, target: (_ for _ in ()).throw(LoginError("Site session expired")))
    assert client.get("/api/v1/me/scores", headers=headers).status_code == 401


def test_logout_invalidates_token(client):
    headers = login(client)
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
