"""
Settings router: view/update user settings, test SMTP, and Playwright browser
session management for LinkedIn / Naukri.
"""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import get_current_user
from app import crypto
from app.db import get_session
from app.config import BROWSERS_DIR
from app.models import Settings, User, json_decode, json_encode
from app.services.scheduler import user_tz

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
    name: str = ""
    base_url: str = ""
    model: str = ""
    key: str = ""  # empty or masked = keep the stored key for this id


def _merge_providers(s: Settings, incoming: List[ProviderIn]) -> List[Dict[str, str]]:
    """New provider list; blank keys keep the stored key of the same id."""
    stored = {p["id"]: p for p in _providers(s) if p.get("id")}
    merged = []
    for p in incoming:
        if p.kind == "openai" and not re.match(r"^(https://|http://(localhost|127\.0\.0\.1)[:/])", p.base_url):
            raise HTTPException(status_code=422, detail=f"Base URL for {p.name or 'provider'} must start with https://")
        if p.kind == "openai" and not p.model.strip():
            raise HTTPException(status_code=422, detail=f"Model is required for {p.name or 'provider'}")
        pid = p.id or secrets.token_hex(4)
        key = p.key.strip() if p.key.strip() and not p.key.startswith("…") else stored.get(pid, {}).get("key", "")
        if not key:
            raise HTTPException(status_code=422, detail=f"API key is required for {p.name or p.kind}")
        merged.append({"id": pid, "kind": p.kind, "name": p.name.strip() or p.kind.title(),
                       "base_url": p.base_url.strip(), "model": p.model.strip(), "key": key})
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
        "ai_providers": [{**{k: v for k, v in p.items() if k != "key"}, "key": f"…{p.get('key', '')[-4:]}"}
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
    return _settings_to_dict(s)


# ---------------------------------------------------------------------------
# PUT /
# ---------------------------------------------------------------------------

class SettingsUpdate(BaseModel):
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None   # plain text; will be encrypted
    smtp_from: Optional[str] = None
    smtp_tls: Optional[bool] = None
    gemini_key: Optional[str] = None      # plain text; will be encrypted
    ai_providers: Optional[List[ProviderIn]] = None
    standard_answers: Optional[Dict[str, str]] = None
    auto_send_email: Optional[bool] = None
    target_locations: Optional[List[str]] = None
    remote_ok: Optional[bool] = None
    min_salary: Optional[int] = None
    min_rating: Optional[float] = None
    excluded_companies: Optional[List[str]] = None
    excluded_keywords: Optional[List[str]] = None
    ignore_degree: Optional[bool] = None
    ignore_yoe_gap: Optional[bool] = None
    yoe_tolerance: Optional[int] = None
    partial_skills_ok: Optional[bool] = None
    score_threshold: Optional[int] = None
    use_gemini_scoring: Optional[bool] = None
    daily_cap: Optional[int] = None
    apply_start_hour: Optional[int] = None
    apply_end_hour: Optional[int] = None
    search_interval_hours: Optional[int] = None
    auto_apply_enabled: Optional[bool] = None
    timezone: Optional[str] = None


@router.put("", include_in_schema=False)
@router.put("/")
def update_settings(
    body: SettingsUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    s = _get_settings(current_user.id, session)

    if body.smtp_host is not None:
        s.smtp_host = body.smtp_host
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
# POST /connect-browser/{platform}
# ---------------------------------------------------------------------------

SUPPORTED_PLATFORMS = {"linkedin", "naukri"}


def _launch_browser_bg(user_id: int, platform: str) -> None:
    """Background task that opens the browser for the user to log in."""
    import asyncio
    from app.db import engine
    from app.services.browser_apply import launch_for_login

    if asyncio.run(launch_for_login(user_id, platform)):
        # Logged in again: resume auto-apply on this site
        with Session(engine) as session:
            s = _get_settings(user_id, session)
            pauses = json_decode(s.platform_pauses, {}) or {}
            if pauses.pop(platform, None):
                s.platform_pauses = json.dumps(pauses)
                session.add(s)
                session.commit()


class BrowserConnectBody(BaseModel):
    platform: str


@router.post("/browser/connect", include_in_schema=False)
def connect_browser_alias(
    body: BrowserConnectBody,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    return connect_browser(body.platform, background_tasks, current_user)


@router.post("/connect-browser/{platform}")
def connect_browser(
    platform: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported platform '{platform}'. Choose from: {SUPPORTED_PLATFORMS}",
        )
    background_tasks.add_task(_launch_browser_bg, current_user.id, platform)
    return {"launched": True, "platform": platform}


# ---------------------------------------------------------------------------
# GET /browser-status
# ---------------------------------------------------------------------------

@router.get("/browser/status", include_in_schema=False)
@router.get("/browser-status")
def browser_status(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    from datetime import datetime, timezone
    from app.services.scheduler import active_pauses

    uid = current_user.id
    linkedin_dir = BROWSERS_DIR / str(uid) / "linkedin"
    naukri_dir = BROWSERS_DIR / str(uid) / "naukri"

    def _has_session(path: Path) -> bool:
        # A Playwright persistent context stores its state in a directory;
        # we consider it valid if the directory exists and is non-empty.
        return path.is_dir() and any(path.iterdir())

    s = _get_settings(uid, session)
    return {
        "linkedin": _has_session(linkedin_dir),
        "naukri": _has_session(naukri_dir),
        # {platform: reason} — auto-apply skips these sites until the user reconnects (or 24 h after a captcha)
        "paused": active_pauses(s.platform_pauses, datetime.now(timezone.utc)),
    }
