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


def test_key_errors_are_explained_with_provider_message():
    from app.services.llm import _explain

    assert _explain(401, "User not found.").startswith("the key was rejected")
    assert "HTTP 429" in _explain(429, "slow down") and "slow down" in _explain(429, "slow down")


def test_reveal_and_key_test_need_login():
    from app.main import app

    client = TestClient(app)
    assert client.post("/api/settings/keys/gemini/reveal").status_code == 401
    assert client.post("/api/settings/keys/test", json={"kind": "gemini"}).status_code == 401


def test_only_one_gemini_key_is_active_and_inactive_keys_are_unused():
    import json
    from types import SimpleNamespace

    from app import crypto
    from app.routers.settings import ProviderIn, _merge_providers
    from app.services.llm import providers_for

    s = SimpleNamespace(ai_providers_enc=None, gemini_key_enc=None)
    merged = _merge_providers(s, [
        ProviderIn(kind="gemini", name="G1", key="AIza-one", active=True),
        ProviderIn(kind="gemini", name="G2", key="AIza-two", active=True),
        ProviderIn(kind="openai", name="OR", key="sk-x", base_url="https://1.1.1.1/v1", model="m", active=False),
    ])
    assert [p["active"] for p in merged] == [True, False, False]
    s.ai_providers_enc = crypto.encrypt(json.dumps(merged))
    assert [p["name"] for p in providers_for(s)] == ["G1"]


def test_cancel_keeps_the_application_with_the_ai_reason():
    from types import SimpleNamespace

    from sqlmodel import Session, SQLModel, create_engine

    from app.models import Application
    from app.routers.applications import cancel_application

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        app = Application(user_id=1, job_id=7, method="site", status="needs_manual",
                          notes='This job is closed: the page says "Not currently accepting applications".')
        session.add(app)
        session.commit()
        cancel_application(app.id, SimpleNamespace(id=1), session)
        kept = session.get(Application, app.id)
        assert kept.status == "cancelled"  # kept, so the job isn't "Not applied yet" (or re-queued) again
        assert kept.notes.startswith("This job is closed: the page says")
