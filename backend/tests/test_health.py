# test_health.py: smoke test that the app starts and /health answers.

from fastapi.testclient import TestClient

from app.main import app


def test_health():
    """/health should return HTTP 200 and {"status": "ok"}."""
    # Using the client as a context manager runs the app's startup and shutdown.
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
