"""Browser preflight checks: the frontend must be allowed to call every method it uses. No database needed."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

ORIGIN = "https://aztu.alakbaroff.com"


@pytest.mark.parametrize(("method", "path"), [("PUT", "/api/v1/me/notifications"), ("DELETE", "/api/v1/me/notifications"), ("POST", "/api/v1/me/telegram/link"), ("DELETE", "/api/v1/me/telegram")])
def test_preflight_allows_the_frontend_methods(method, path):
    # Without `with`, the app's startup (database connection) does not run.
    response = TestClient(app).options(
        path,
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": method, "Access-Control-Request-Headers": "authorization,content-type"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert method in response.headers["access-control-allow-methods"].split(", ")
    assert "authorization" in response.headers["access-control-allow-headers"].lower()
