"""Tests for score parsing, skill gaps, title relevance, column migration and time zones."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import inspect
from sqlmodel import Field, SQLModel, create_engine

from app.db import add_missing_columns
from app.services.matcher import parse_score_response, skill_gap
from app.services.scheduler import local_day_start_utc, user_tz
from app.services.sources import title_relevant


class TestParseScore:
    def test_plain_json(self):
        assert parse_score_response('{"score": 8, "reason": "ok", "resume_tips": ["a"]}') == (8.0, "ok", ["a"])

    def test_fenced_json_with_chatter(self):
        raw = 'Sure!\n```json\n{"score": 3, "reason": "x"}\n```'
        assert parse_score_response(raw) == (3.0, "x", [])

    def test_out_of_range_is_clamped(self):
        assert parse_score_response('{"score": 42}')[0] == 10.0

    def test_garbage_is_none(self):
        assert parse_score_response("no json here") is None
        assert parse_score_response('{"score": "high"}') is None


class TestSkillGap:
    def test_alias_counts_as_present(self):
        required, missing = skill_gap("Built apps in React and Node.js", "Need React.js and Node.js")
        assert missing == []
        assert len(required) == 2

    def test_missing_skill_reported(self):
        _, missing = skill_gap("React developer", "React, Kubernetes and Java required")
        assert set(missing) == {"Kubernetes", "Java"}

    def test_go_word_is_not_a_skill(self):
        required, _ = skill_gap("", "Ready to go live with us")
        assert required == []


class TestTitleRelevant:
    targets = ["Senior Frontend Developer"]
    skills = ["React", "Vue.js", "Scrum"]

    def test_role_keyword_with_hyphen(self):
        assert title_relevant("Front-End Engineer", self.targets, self.skills)

    def test_skill_in_title(self):
        assert title_relevant("Jr. React+Asp.net Developers", self.targets, self.skills)

    def test_unrelated(self):
        assert not title_relevant("Sales & Purchase Associate", self.targets, self.skills)
        assert not title_relevant("Jr. Java Developer", self.targets, self.skills)

    def test_generic_skill_does_not_match(self):
        assert not title_relevant("Scrum Master", self.targets, self.skills)


def test_add_missing_columns_adds_defaults():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE "widget" (id INTEGER PRIMARY KEY)')
        conn.exec_driver_sql('INSERT INTO "widget" (id) VALUES (1)')

    class Widget(SQLModel, table=True):
        __tablename__ = "widget"
        id: Optional[int] = Field(default=None, primary_key=True)
        flag: bool = Field(default=False)
        zone: str = Field(default="Asia/Kolkata")

    try:
        add_missing_columns(engine)
        cols = {c["name"] for c in inspect(engine).get_columns("widget")}
        assert {"flag", "zone"} <= cols
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT flag, zone FROM widget").one() == (0, "Asia/Kolkata")
    finally:
        SQLModel.metadata.remove(Widget.__table__)


def test_local_day_start_in_india():
    now = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)  # 01:30 on 14 Oct in IST
    assert local_day_start_utc(user_tz("Asia/Kolkata"), now) == datetime(2026, 10, 13, 18, 30)


def test_unknown_timezone_falls_back_to_utc():
    assert user_tz("Not/AZone").key == "UTC"


def test_queue_matches_queues_only_unapplied_jobs_above_threshold():
    from sqlmodel import Session, select

    from app.models import Application, Job, User
    from app.services.matcher import queue_matches

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(User(id=1, email="a@b.c", hashed_password="x"))
        for jid, score in [(1, 9.0), (2, 8.0), (3, 5.0), (4, None)]:
            s.add(Job(id=jid, user_id=1, title="t", company="c", url=f"u{jid}", source="remotive", score=score))
        s.add(Application(user_id=1, job_id=2, method="email", status="applied", applied_at=datetime.utcnow()))
        s.commit()

        assert queue_matches(1, 7, s) == 1
        s.commit()
        assert queue_matches(1, 7, s) == 0  # idempotent
        queued = s.exec(select(Application).where(Application.status == "queued")).all()
        assert [a.job_id for a in queued] == [1]


def test_provider_chain_skips_exhausted_keys_then_falls_back_to_ollama(monkeypatch):
    import asyncio

    from google.genai import errors

    from app.services import llm

    async def quota_exceeded(client, **kwargs):
        exc = errors.ClientError.__new__(errors.ClientError)  # real __init__ needs an HTTP response
        Exception.__init__(exc, "429 RESOURCE_EXHAUSTED. {'@type': 'RetryInfo', 'retryDelay': '120s'}")
        exc.code = 429
        raise exc

    async def fake_chat(messages, model, url, json_mode=False, timeout=300.0):
        return "local reply"

    openai_calls = []

    async def fake_openai(p, prompt, json_mode):
        openai_calls.append(p["name"])
        return "groq reply" if p["name"] == "Groq" else ""

    monkeypatch.setattr(llm, "_generate", quota_exceeded)
    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(llm, "_openai_chat", fake_openai)
    monkeypatch.setattr(llm, "GEMINI_MODELS", ["model-a"])
    monkeypatch.setattr(llm, "GEMINI_LITE_MODELS", ["model-b"])
    monkeypatch.setattr(llm, "_blocked", {})
    gemini = {"kind": "gemini", "name": "Gemini", "key": "key-111111"}
    down = {"kind": "openai", "name": "Down", "key": "k2", "base_url": "https://x", "model": "m"}
    groq = {"kind": "openai", "name": "Groq", "key": "k3", "base_url": "https://y", "model": "m"}

    # Gemini models out of quota -> skipped for retryDelay; next providers are tried in order
    assert asyncio.run(llm.generate("p", [gemini, down, groq], "m", "u")) == ("groq reply", "Groq")
    assert openai_calls == ["Down", "Groq"]
    left = llm._blocked["111111:model-b"] - __import__("time").monotonic()
    assert set(llm._blocked) == {"111111:model-a", "111111:model-b"} and 100 < left <= 120
    assert llm._usable("key-111111", ["model-a"]) == [] and llm._usable("other-key", ["model-a"]) == ["model-a"]

    # Nothing in the cloud answers -> the local model does
    assert asyncio.run(llm.generate("p", [gemini, down], "m", "u")) == ("local reply", "local model")


def test_light_tasks_try_lite_models_first(monkeypatch):
    import asyncio

    from app.services import llm

    tried = []

    async def fake_gemini(key, contents, config=None, models=None):
        tried.append(list(models))
        return "ok"

    monkeypatch.setattr(llm, "_gemini_generate", fake_gemini)
    monkeypatch.setattr(llm, "GEMINI_MODELS", ["flash"])
    monkeypatch.setattr(llm, "GEMINI_LITE_MODELS", ["lite"])
    g = {"kind": "gemini", "name": "Gemini", "key": "k"}
    asyncio.run(llm.generate("p", [g], "m", "u", light=True))
    asyncio.run(llm.generate("p", [g], "m", "u"))
    assert tried == [["lite", "flash"], ["flash", "lite"]]


def test_local_scorer_learns_a_simple_pattern():
    import numpy as np

    from app.services import local_scorer as L

    rng = np.random.default_rng(1)
    E = rng.normal(size=(120, 16))
    resume = rng.normal(size=16)
    model = {"resume_emb": resume.tolist(), "emb_mean": E.mean(0).tolist(),
             "components": np.linalg.svd(E - E.mean(0), full_matrices=False)[2][:8].tolist()}
    matched = rng.integers(0, 8, 120)
    y = np.clip(2 + matched * 1.0, 0, 10)  # score driven by skill matches
    X = np.vstack([L._features(E[i], model, int(matched[i]), 2, True, False) for i in range(120)])
    assert L._cv_mae(X, y, 0.1) < 0.3
    w, b, mu, sd = L._fit(X, y, 0.1)
    full = {**model, "w": w.tolist(), "b": b, "mu": mu.tolist(), "sd": sd.tolist(), "cal_x": [0, 10], "cal_y": [0, 10]}
    assert abs(L.predict(full, E[0].tolist(), int(matched[0]), 2, True, False) - y[0]) < 0.5


def test_ai_modes_route_work_between_cloud_and_local():
    from types import SimpleNamespace

    from app.services import llm

    def settings(mode, scoring=False):
        return SimpleNamespace(ai_mode=mode, use_gemini_scoring=scoring, gemini_key_enc=None, ai_providers_enc=None)

    assert llm.ai_mode(settings(None, True)) == "full_ai" and llm.ai_mode(settings(None)) == "semi_ai"
    assert llm.local_first(settings("local_plus_ai")) and not llm.local_first(settings("semi_ai"))
    real = llm.providers_for
    try:
        llm.providers_for = lambda s: real(s)
        assert llm.providers_for(settings("local_only")) == []
    finally:
        llm.providers_for = real


def test_needs_you_report_groups_real_notes():
    from app.routers.applications import reason_of

    assert reason_of("Not an Easy Apply job — ... Can't apply here: The page is blank (about:blank)") == "blank_page"
    assert reason_of("No Apply button found on LinkedIn. Check the job here: https://x") == "no_button"
    assert reason_of("Needs you: The site requires account creation or login to apply.") == "account"
    assert reason_of("Needs you: Cloudflare verification required Finish it here: https://x") == "captcha"
    assert reason_of("The AI agent submitted on naukri.com but the site showed no confirmation — verify here") == "unconfirmed"
    assert reason_of("The AI agent got no usable reply (AI quota?). Finish it here: https://x") == "ai_quota"
    assert reason_of("Can't apply here: The job requires candidates to have 8 to 12 years of experience") == "qualified"
    assert reason_of("This job is no longer accepting applications.") == "closed"
    assert reason_of("The AI agent couldn't finish in 14 steps. Finish it here: https://x") == "too_long"
    assert reason_of("Unexpected error while applying.") == "other"


def test_freehire_source_parses_and_skips_telegram(monkeypatch):
    import httpx
    from app.services import sources

    body = {"data": [
        {"title": "Frontend Developer (React)", "company": "Acme", "location": "Kochi", "url": "https://boards.greenhouse.io/acme/jobs/1",
         "description": "<p>React</p>", "work_mode": "onsite"},
        {"title": "React Dev", "company": "X", "location": "India", "url": "https://t.me/cs_algo/1"}]}
    seen = {}

    def handler(req):
        seen.update(req.url.params)
        return httpx.Response(200, json=body)

    real = httpx.Client
    monkeypatch.setattr(sources.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    jobs = sources._scrape_freehire("frontend developer", countries="IN")
    assert seen["countries"] == "IN" and seen["q"] == "frontend developer"
    assert [j["company"] for j in jobs] == ["Acme"] and jobs[0]["source"] == "freehire" and not jobs[0]["is_remote"]


def test_deepseek_web_json_part():
    from app.services.llm import _json_part

    assert _json_part('json\nCopy\n{"score": 8}\nDownload') == '{"score": 8}'
    assert _json_part('[1, 2]') == '[1, 2]'
    assert _json_part('no json') == 'no json'
