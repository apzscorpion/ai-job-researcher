"""
Settings router: view/update user settings, test SMTP, and Playwright browser
session management for LinkedIn / Naukri.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Annotated, Any, Dict, List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.auth import get_current_user
from app import crypto
from app.db import get_session
from app.models import Settings, User, json_decode, json_encode
from app.security import check_public_host, check_public_url
from app.services.llm import ai_mode
from app.services.scheduler import user_tz

Short = Annotated[str, Field(max_length=200)]
Long = Annotated[str, Field(max_length=2000)]

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _get_settings(user_id: int, session: Session) -> Settings:
    s = session.exec(select(Settings).where(Settings.user_id == user_id)).first()
    if not s:
        raise HTTPException(status_code=404, detail="Settings not found")
    return s


def _providers(s: Settings) -> List[Dict[str, str]]:
    return json_decode(crypto.decrypt(s.ai_providers_enc), []) if s.ai_providers_enc else []


class ProviderIn(BaseModel):
    id: Optional[str] = None
    kind: Literal["gemini", "openai"]
    name: Short = ""
    base_url: Short = ""
    model: Short = ""
    account: Short = ""  # which account the key belongs to (the user's label; APIs don't tell)
    active: bool = True  # inactive keys are kept but never used; at most one Gemini key is active
    key: Annotated[str, Field(max_length=500)] = ""  # empty or masked = keep the stored key for this id


def _fold_gemini_key(s: Settings, session: Session) -> None:
    """The old single "Gemini API Key" becomes the first row of the one AI-keys list."""
    if not s.gemini_key_enc:
        return
    name = "Gemini" + (f" – {s.gemini_account}" if s.gemini_account else "")
    rows = [{"id": "gemini-main", "kind": "gemini", "name": name, "base_url": "", "model": "",
             "account": s.gemini_account or "", "key": crypto.decrypt(s.gemini_key_enc)}]
    s.ai_providers_enc = crypto.encrypt(json.dumps(rows + [p for p in _providers(s) if p.get("id") != "gemini-main"]))
    s.gemini_key_enc = s.gemini_account = None
    session.add(s)
    session.commit()


def _merge_providers(s: Settings, incoming: List[ProviderIn]) -> List[Dict[str, str]]:
    """New provider list; blank keys keep the stored key of the same id."""
    stored = {p["id"]: p for p in _providers(s) if p.get("id")}
    merged = []
    gemini_active = False
    for p in incoming:
        if p.kind == "openai":
            # The server calls this URL with the key: only public https APIs, never the server's own network
            try:
                if any(c in p.base_url for c in "?#@"):
                    raise ValueError("no query, fragment or credentials allowed")
                check_public_url(p.base_url.strip(), ("https",))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"Base URL for {p.name or 'provider'}: {exc}")
        if p.kind == "openai" and not p.model.strip():
            raise HTTPException(status_code=422, detail=f"Model is required for {p.name or 'provider'}")
        pid = p.id or secrets.token_hex(4)
        key = p.key.strip() if p.key.strip() and not p.key.startswith("…") else stored.get(pid, {}).get("key", "")
        if not key:
            raise HTTPException(status_code=422, detail=f"API key is required for {p.name or p.kind}")
        # one Gemini key at a time: the first one marked active wins, the others are switched off
        active = p.active and not (p.kind == "gemini" and gemini_active)
        gemini_active |= p.kind == "gemini" and active
        merged.append({"id": pid, "kind": p.kind, "name": p.name.strip() or p.kind.title(),
                       "base_url": p.base_url.strip(), "model": p.model.strip(),
                       "account": p.account.strip(), "active": active, "key": key})
    return merged


def _settings_to_dict(s: Settings) -> Dict[str, Any]:
    return {
        "id": s.id,
        "user_id": s.user_id,
        "smtp_host": s.smtp_host,
        "smtp_port": s.smtp_port,
        "smtp_user": s.smtp_user,
        "smtp_password": "***" if s.smtp_password_enc else "",
        "smtp_from": s.smtp_from,
        "smtp_tls": s.smtp_tls,
        "gemini_key": "***" if s.gemini_key_enc else "",
        "gemini_account": s.gemini_account or "",
        "ai_providers": [{**{k: v for k, v in p.items() if k != "key"}, "active": p.get("active", True),
                          "key": f"…{p.get('key', '')[-4:]}"}
                         for p in _providers(s)],
        "standard_answers": json_decode(s.standard_answers, {}) or {},
        "auto_send_email": s.auto_send_email,
        "target_locations": json_decode(s.target_locations, []),
        "remote_ok": s.remote_ok,
        "min_salary": s.min_salary,
        "min_rating": s.min_rating,
        "excluded_companies": json_decode(s.excluded_companies, []),
        "excluded_keywords": json_decode(s.excluded_keywords, []),
        "ignore_degree": s.ignore_degree,
        "ignore_yoe_gap": s.ignore_yoe_gap,
        "yoe_tolerance": s.yoe_tolerance,
        "partial_skills_ok": s.partial_skills_ok,
        "score_threshold": s.score_threshold,
        "use_gemini_scoring": s.use_gemini_scoring,
        "ai_mode": ai_mode(s),
        "daily_cap": s.daily_cap,
        "apply_start_hour": s.apply_start_hour,
        "apply_end_hour": s.apply_end_hour,
        "search_interval_hours": s.search_interval_hours,
        "auto_apply_enabled": s.auto_apply_enabled,
        "timezone": s.timezone,
    }


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

@router.get("", include_in_schema=False)
@router.get("/")
def get_settings(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    s = _get_settings(current_user.id, session)
    _fold_gemini_key(s, session)
    return _settings_to_dict(s)


# ---------------------------------------------------------------------------
# PUT /
# ---------------------------------------------------------------------------

class SettingsUpdate(BaseModel):
    smtp_host: Optional[Short] = None
    smtp_port: Optional[Literal[25, 465, 587, 2525]] = None
    smtp_user: Optional[Short] = None
    smtp_password: Optional[Short] = None   # plain text; will be encrypted
    smtp_from: Optional[Short] = None
    smtp_tls: Optional[bool] = None
    gemini_key: Optional[Annotated[str, Field(max_length=500)]] = None      # plain text; will be encrypted
    gemini_account: Optional[Short] = None
    ai_providers: Optional[Annotated[List[ProviderIn], Field(max_length=10)]] = None
    standard_answers: Optional[Annotated[Dict[Short, Long], Field(max_length=50)]] = None
    auto_send_email: Optional[bool] = None
    target_locations: Optional[Annotated[List[Short], Field(max_length=20)]] = None
    remote_ok: Optional[bool] = None
    min_salary: Optional[Annotated[int, Field(ge=0, le=100_000_000)]] = None
    min_rating: Optional[Annotated[float, Field(ge=0, le=5)]] = None
    excluded_companies: Optional[Annotated[List[Short], Field(max_length=200)]] = None
    excluded_keywords: Optional[Annotated[List[Short], Field(max_length=200)]] = None
    ignore_degree: Optional[bool] = None
    ignore_yoe_gap: Optional[bool] = None
    yoe_tolerance: Optional[Annotated[int, Field(ge=0, le=20)]] = None
    partial_skills_ok: Optional[bool] = None
    score_threshold: Optional[Annotated[int, Field(ge=0, le=10)]] = None
    use_gemini_scoring: Optional[bool] = None
    ai_mode: Optional[Literal["full_ai", "semi_ai", "local_plus_ai", "local_only"]] = None
    daily_cap: Optional[Annotated[int, Field(ge=0, le=100)]] = None
    apply_start_hour: Optional[Annotated[int, Field(ge=0, le=23)]] = None
    apply_end_hour: Optional[Annotated[int, Field(ge=1, le=24)]] = None
    search_interval_hours: Optional[Annotated[int, Field(ge=1, le=168)]] = None
    auto_apply_enabled: Optional[bool] = None
    timezone: Optional[Short] = None


@router.put("", include_in_schema=False)
@router.put("/")
def update_settings(
    body: SettingsUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    s = _get_settings(current_user.id, session)
    _fold_gemini_key(s, session)  # before ai_providers is replaced, so the old key isn't lost

    if body.smtp_host:
        try:  # the server connects here: never to its own network
            check_public_host(body.smtp_host.strip(), body.smtp_port or s.smtp_port)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"SMTP host: {exc}")
    if body.smtp_host is not None:
        s.smtp_host = body.smtp_host.strip()
    if body.smtp_port is not None:
        s.smtp_port = body.smtp_port
    if body.smtp_user is not None:
        s.smtp_user = body.smtp_user
    if body.smtp_from is not None:
        s.smtp_from = body.smtp_from
    if body.smtp_tls is not None:
        s.smtp_tls = body.smtp_tls
    if body.smtp_password is not None:
        s.smtp_password_enc = crypto.encrypt(body.smtp_password) if body.smtp_password else None
    if body.gemini_account is not None:
        s.gemini_account = body.gemini_account.strip() or None
    if body.gemini_key is not None:
        s.gemini_key_enc = crypto.encrypt(body.gemini_key) if body.gemini_key else None
    if body.ai_providers is not None:
        merged = _merge_providers(s, body.ai_providers)
        s.ai_providers_enc = crypto.encrypt(json.dumps(merged)) if merged else None
    if body.standard_answers is not None:
        s.standard_answers = json.dumps({k: v.strip() for k, v in body.standard_answers.items()}, ensure_ascii=False)
    if body.auto_send_email is not None:
        s.auto_send_email = body.auto_send_email
    if body.target_locations is not None:
        s.target_locations = json_encode(body.target_locations)
    if body.remote_ok is not None:
        s.remote_ok = body.remote_ok
    if body.min_salary is not None:
        s.min_salary = body.min_salary
    if body.min_rating is not None:
        s.min_rating = body.min_rating
    if body.excluded_companies is not None:
        s.excluded_companies = json_encode(body.excluded_companies)
    if body.excluded_keywords is not None:
        s.excluded_keywords = json_encode(body.excluded_keywords)
    if body.ignore_degree is not None:
        s.ignore_degree = body.ignore_degree
    if body.ignore_yoe_gap is not None:
        s.ignore_yoe_gap = body.ignore_yoe_gap
    if body.yoe_tolerance is not None:
        s.yoe_tolerance = body.yoe_tolerance
    if body.partial_skills_ok is not None:
        s.partial_skills_ok = body.partial_skills_ok
    if body.score_threshold is not None:
        s.score_threshold = body.score_threshold
    if body.use_gemini_scoring is not None:
        s.use_gemini_scoring = body.use_gemini_scoring
        if body.ai_mode is None:  # the old scoring toggle: switch between full and semi, keep the local modes
            s.ai_mode = "full_ai" if body.use_gemini_scoring else (
                s.ai_mode if s.ai_mode in ("local_plus_ai", "local_only") else "semi_ai")
    if body.ai_mode is not None:
        s.ai_mode = body.ai_mode
        s.use_gemini_scoring = body.ai_mode == "full_ai"  # only full AI scores jobs with cloud keys
    if body.daily_cap is not None:
        s.daily_cap = body.daily_cap
    if body.apply_start_hour is not None:
        s.apply_start_hour = body.apply_start_hour
    if body.apply_end_hour is not None:
        s.apply_end_hour = body.apply_end_hour
    if body.search_interval_hours is not None:
        s.search_interval_hours = body.search_interval_hours
    queued = 0
    if body.auto_apply_enabled is not None:
        s.auto_apply_enabled = body.auto_apply_enabled
        if body.auto_apply_enabled:
            # Jobs scored while auto-apply was off would otherwise never be applied to
            from app.services.matcher import queue_matches
            queued = queue_matches(current_user.id, s.score_threshold, session)
    if body.timezone is not None:
        if user_tz(body.timezone).key != body.timezone:
            raise HTTPException(status_code=422, detail=f"Unknown time zone '{body.timezone}'")
        s.timezone = body.timezone

    session.add(s)
    session.commit()
    session.refresh(s)
    return {**_settings_to_dict(s), "queued": queued}


# ---------------------------------------------------------------------------
# POST /test-email
# ---------------------------------------------------------------------------

@router.post("/test-email")
def test_email(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    from app.services.mailer import test_smtp

    s = _get_settings(current_user.id, session)
    ok, error = test_smtp(s, crypto)
    return {"ok": ok, "error": error}


# ---------------------------------------------------------------------------
# AI keys: reveal (eye button) and live test
# ---------------------------------------------------------------------------

def _stored_key(s: Settings, key_id: str) -> str:
    if key_id == "gemini":
        return crypto.decrypt(s.gemini_key_enc) if s.gemini_key_enc else ""
    return next((p.get("key", "") for p in _providers(s) if p.get("id") == key_id), "")


@router.get("/local-model")
def local_model(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """This user's own trained job scorer (None until enough jobs have been AI-scored) and its re-checks."""
    from app.services import local_scorer
    from app.services.matcher import recheck_stats

    s = _get_settings(current_user.id, session)
    return {"model": local_scorer.stats(current_user.id), "min_examples": local_scorer.MIN_EXAMPLES,
            "recheck": recheck_stats(current_user.id, s.score_threshold, session)}


@router.post("/local-model/train")
async def train_local_model(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    from app.security import rate_limit
    from app.services import local_scorer

    rate_limit(f"train:{current_user.id}", 6, 3600, "Retrained recently — try again later")
    stats = await local_scorer.train(current_user.id, session)
    if stats is None:
        raise HTTPException(status_code=409, detail=f"Not enough AI-scored jobs yet (needs {local_scorer.MIN_EXAMPLES}) "
                                                    "— keep cloud scoring on for a few searches first")
    return {"model": stats, "min_examples": local_scorer.MIN_EXAMPLES}


@router.post("/keys/{key_id}/reveal")
def reveal_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, str]:
    """The full saved key, only when the owner clicks the eye button (never sent with the settings)."""
    from app.security import rate_limit

    rate_limit(f"reveal:{current_user.id}", 30, 600)
    key = _stored_key(_get_settings(current_user.id, session), key_id[:40])
    if not key:
        raise HTTPException(status_code=404, detail="No saved key")
    return {"key": key}


class KeyTest(BaseModel):
    id: Optional[Short] = None            # "gemini" or a provider id: use the saved key when key is blank
    kind: Literal["gemini", "openai"]
    base_url: Short = ""
    model: Short = ""
    key: Annotated[str, Field(max_length=500)] = ""


@router.post("/keys/test")
async def test_key(
    body: KeyTest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Send one tiny request with this key and report exactly what the provider answered."""
    from app.security import rate_limit
    from app.services.llm import test_provider

    rate_limit(f"keytest:{current_user.id}", 20, 600, "Too many key tests — wait a few minutes")
    key = body.key.strip() if body.key.strip() and not body.key.startswith("…") else         _stored_key(_get_settings(current_user.id, session), body.id or "")
    if not key:
        raise HTTPException(status_code=422, detail="Enter a key (or save one) to test")
    if body.kind == "openai":
        if not body.model.strip():
            raise HTTPException(status_code=422, detail="Enter the model name to test")
        try:
            check_public_url(body.base_url.strip(), ("https",))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Base URL: {exc}")
    p = {"kind": body.kind, "key": key, "base_url": body.base_url.strip(), "model": body.model.strip()}
    results = await test_provider(p)
    from app.services.llm import key_info

    return {"ok": any(r["ok"] for r in results), "results": results, "account": await key_info(p)}


# ---------------------------------------------------------------------------
# Job-site connections (Settings → Job sites)
# ---------------------------------------------------------------------------

def _resume_platform(user_id: int, platform: str) -> None:
    """Signed back in: auto-apply may use this site again."""
    from app.db import engine

    with Session(engine) as session:
        s = _get_settings(user_id, session)
        pauses = json_decode(s.platform_pauses, {}) or {}
        if pauses.pop(platform, None):
            s.platform_pauses = json.dumps(pauses)
            session.add(s)
            session.commit()


async def _connect_bg(user_id: int, site: str) -> None:
    from app.services import connections

    if await connections.connect(user_id, site) and site in ("linkedin", "naukri"):
        _resume_platform(user_id, site)


@router.get("/job-sites")
async def job_sites(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Every job site with its signed-in state, read from the saved browser profiles ("Sync all")."""
    from datetime import datetime, timezone

    from app.services import connections
    from app.services.scheduler import active_pauses

    states = await connections.check_all(current_user.id)
    for platform in ("linkedin", "naukri"):
        if states.get(platform) == "signed_in":
            _resume_platform(current_user.id, platform)
    session.expire_all()
    paused = active_pauses(_get_settings(current_user.id, session).platform_pauses, datetime.now(timezone.utc))
    return {
        "sites": [{"id": k, "name": v["name"], "url": v["url"], "state": states.get(k, "not_connected"),
                   "checks_login": bool(v["cookie"]), "paused": paused.get(k)}
                  for k, v in connections.SITES.items()],
    }


@router.post("/job-sites/{site}/connect")
async def connect_job_site(
    site: str,
    background_tasks: BackgroundTasks,
    remote: bool = False,
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    from app.services import connections

    from app.security import is_admin

    if site not in connections.SITES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown job site")
    if connections.busy(current_user.id, site):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="That browser is already open or applying — finish there, then try again")
    if not is_admin(current_user) or remote:
        # Everyone else signs in through a live view in the website; the browser (with their own
        # profile, never anyone else's) runs on the host machine
        connections.start_remote(current_user.id, site)
        return {"launched": True, "site": site, "remote": True}
    background_tasks.add_task(_connect_bg, current_user.id, site)
    return {"launched": True, "site": site, "remote": False}


@router.get("/job-sites/remote/status")
def remote_status(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    from app.services import connections

    st = connections.remote_status(current_user.id)
    if st["state"] == "signed_in" and st.get("site") in ("linkedin", "naukri"):
        _resume_platform(current_user.id, st["site"])
    return st


@router.get("/job-sites/remote/frame")
async def remote_frame(current_user: User = Depends(get_current_user)):
    from fastapi import Response

    from app.services import connections

    img = await connections.remote_frame(current_user.id)
    if img is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No sign-in window open")
    return Response(content=img, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


class RemoteInput(BaseModel):
    kind: Literal["click", "type", "key", "scroll"]
    x: Annotated[float, Field(ge=0, le=1)] = 0  # fraction of the live picture's width
    y: Annotated[float, Field(ge=0, le=1)] = 0  # fraction of its height
    text: Annotated[str, Field(max_length=500)] = ""
    key: Annotated[str, Field(max_length=20)] = ""
    dy: Annotated[float, Field(ge=-2000, le=2000)] = 0


@router.post("/job-sites/remote/input")
async def remote_input(body: RemoteInput, current_user: User = Depends(get_current_user)) -> Dict[str, bool]:
    from app.security import rate_limit
    from app.services import connections

    rate_limit(f"remote-input:{current_user.id}", 300, 60)
    ok = await connections.remote_input(current_user.id, body.kind, body.x, body.y, body.text, body.key, body.dy)
    if not ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The sign-in window is closed")
    return {"ok": True}


@router.post("/job-sites/remote/close")
def remote_close(current_user: User = Depends(get_current_user)) -> Dict[str, bool]:
    from app.services import connections

    connections.remote_close(current_user.id)
    return {"closed": True}
