"""Validation errors must not send submitted passwords back. No database or site needed."""

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.main import app


@pytest.fixture
def client():
    # Validation runs before the route, so the database and the site session are stubbed out.
    app.dependency_overrides[deps.get_store] = lambda: None
    app.dependency_overrides[deps.get_settings] = lambda: None
    app.dependency_overrides[deps.current_scraper] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_a_login_with_a_misspelled_field_does_not_send_the_password_back(client):
    response = client.post("/api/v1/auth/login", json={"user": "M1", "password": "Secret-Pass-123"})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "username"]
    assert "Secret-Pass-123" not in response.text


def test_a_mismatched_password_change_does_not_send_either_password_back(client):
    response = client.post("/api/v1/me/password", json={"password": "New-Pass-1!", "confirm_password": "Other-Pass-2!"})

    assert response.status_code == 422
    assert "must match" in response.json()["detail"][0]["msg"]
    assert "New-Pass-1!" not in response.text and "Other-Pass-2!" not in response.text
