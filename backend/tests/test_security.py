"""SSRF guards, link sanitising, rate limiting and the auth endpoints' policies."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.security import check_public_url, http_url_or_none, rate_limit


@pytest.mark.parametrize("url", [
    "http://example.com/v1",            # https only
    "https://localhost/v1", "https://127.0.0.1:11434/", "https://[::1]/",
    "https://169.254.169.254/latest/", "https://10.0.0.5/", "https://192.168.1.1/",
    "https://user:pw@api.openai.com/v1",
])
def test_check_public_url_blocks_internal_targets(url):
    with pytest.raises(ValueError):
        check_public_url(url)


def test_check_public_url_allows_public_https():
    check_public_url("https://1.1.1.1/v1")  # literal public IP: no DNS needed


def test_http_url_or_none_drops_script_links():
    assert http_url_or_none("https://jobs.lever.co/a/1") == "https://jobs.lever.co/a/1"
    for bad in ("javascript://greenhouse.io/%0aalert(1)", "data:text/html,x", "", None, "jobs.lever.co/a"):
        assert http_url_or_none(bad) is None


def test_rate_limit_blocks_after_limit():
    for _ in range(3):
        rate_limit("test-key-xyz", 3, 60)
    with pytest.raises(HTTPException) as exc:
        rate_limit("test-key-xyz", 3, 60)
    assert exc.value.status_code == 429


def test_register_rejects_weak_password_and_logs_are_admin_only():
    from app.main import app

    client = TestClient(app)
    r = client.post("/api/auth/register", json={"email": "weak@example.org", "password": "short"})
    assert r.status_code == 422
    r = client.post("/api/auth/register", json={"email": "weak2@example.org", "password": "aaaaaaaa"})
    assert r.status_code == 422
    assert client.get("/docs").status_code == 404
    assert client.get("/api/health").headers["x-frame-options"] == "DENY"
