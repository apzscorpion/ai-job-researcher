"""
SQLModel ORM models for AI Job Researcher.

JSON list/dict fields are stored as plain TEXT columns (serialised with json).
Use the `json_field` helpers to encode/decode them.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Field, SQLModel


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def json_encode(value: Any, default: str = "[]") -> str:
    """Encode a Python object to a JSON string."""
    if value is None:
        return default
    if isinstance(value, str):
        return value  # already serialised
    return json.dumps(value, ensure_ascii=False)


def json_decode(value: Optional[str], default: Any = None) -> Any:
    """Decode a JSON string back to a Python object."""
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class User(SQLModel, table=True):
    """Registered user account."""

    __tablename__ = "user"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

class Profile(SQLModel, table=True):
    """Extended user profile and parsed résumé data."""

    __tablename__ = "profile"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)

    name: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    linkedin_url: Optional[str] = None

    # File system path to the uploaded résumé
    resume_path: Optional[str] = None
    # Raw extracted text
    resume_text: Optional[str] = None

    # Stored as JSON strings
    parsed_skills: Optional[str] = Field(default="[]")
    parsed_experience: Optional[str] = Field(default="[]")
    parsed_education: Optional[str] = Field(default="[]")

    total_years: Optional[float] = None
    # JSON list: the skills used for matching, search and applications; None = all parsed skills
    apply_skills: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class Settings(SQLModel, table=True):
    """Per-user application settings."""

    __tablename__ = "settings"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", unique=True, index=True)

    # SMTP
    smtp_host: Optional[str] = None
    smtp_port: int = Field(default=587)
    smtp_user: Optional[str] = None
    smtp_password_enc: Optional[str] = None  # Fernet-encrypted
    smtp_from: Optional[str] = None
    smtp_tls: bool = Field(default=True)

    # Gemini
    gemini_key_enc: Optional[str] = None  # Fernet-encrypted
    gemini_account: Optional[str] = None  # the user's own note of which Google account the key belongs to
    # Extra AI providers, Fernet-encrypted JSON list of
    # {"id", "kind": "gemini"|"openai", "name", "key", "base_url", "model"}; tried after gemini_key
    ai_providers_enc: Optional[str] = None

    # JSON dict of answers for common application questions (notice period, salary, visa, ...)
    standard_answers: str = Field(default="{}")
    # Send recruiter emails without review when SMTP is set up
    auto_send_email: bool = Field(default=True)
    # JSON {"linkedin": {"until": iso-utc, "reason": str}}: platforms paused after a login wall or captcha
    platform_pauses: str = Field(default="{}")

    # Job search preferences (stored as JSON strings)
    target_titles: str = Field(default="[]")
    target_locations: str = Field(default="[]")
    remote_ok: bool = Field(default=True)

    # Salary / rating filters
    min_salary: int = Field(default=0)
    min_rating: float = Field(default=0.0)

    # Exclusion lists (stored as JSON strings)
    excluded_companies: str = Field(default="[]")
    excluded_keywords: str = Field(default="[]")

    # Relaxation flags
    ignore_degree: bool = Field(default=False)
    ignore_yoe_gap: bool = Field(default=False)
    yoe_tolerance: int = Field(default=2)
    partial_skills_ok: bool = Field(default=False)

    # Scoring
    score_threshold: int = Field(default=7)
    use_gemini_scoring: bool = Field(default=False)

    # Automation
    daily_cap: int = Field(default=30)
    apply_start_hour: int = Field(default=9)
    apply_end_hour: int = Field(default=21)
    search_interval_hours: int = Field(default=6)
    auto_apply_enabled: bool = Field(default=False)
    # IANA name; apply hours and "today" are evaluated in this zone
    timezone: str = Field(default="Asia/Kolkata")


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------

class Job(SQLModel, table=True):
    """A scraped job listing."""

    __tablename__ = "job"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)

    title: str
    company: str
    location: Optional[str] = None
    url: str = Field(index=True)
    source: str  # linkedin / indeed / naukri / glassdoor / infopark / technopark

    description: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    company_rating: Optional[float] = None
    recruiter_email: Optional[str] = None
    # Direct link to the company's own application page (ATS), when the source gives one
    apply_url: Optional[str] = None
    is_remote: bool = Field(default=False)

    # Scoring
    score: Optional[float] = None
    score_reason: Optional[str] = None
    # JSON list of résumé changes the scorer suggests for this job
    resume_tips: Optional[str] = None

    # Embedding stored as JSON string
    embedding: Optional[str] = None  # JSON list[float]

    found_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = Field(default="new")  # new / scored / skipped


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class Application(SQLModel, table=True):
    """A job application submitted on behalf of the user."""

    __tablename__ = "application"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    job_id: int = Field(foreign_key="job.id", index=True)

    method: str  # email / linkedin / naukri / site / manual / pending
    # queued / draft / applied / needs_manual / failed / replied
    status: str = Field(default="queued")

    applied_at: datetime = Field(default_factory=datetime.utcnow)
    email_body: Optional[str] = None
    cover_letter: Optional[str] = None
    # Where the application went: recruiter email address or the application page URL
    sent_to: Optional[str] = None
    # Plain-English account of what happened (shown to the user)
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# ActivityLog
# ---------------------------------------------------------------------------

class ActivityLog(SQLModel, table=True):
    """Audit / activity log entry."""

    __tablename__ = "activitylog"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)

    event: str
    detail: Optional[str] = None
    # Events in NOTIFY_EVENTS show on the dashboard until seen
    seen: bool = Field(default=False)
    ts: datetime = Field(default_factory=datetime.utcnow)


NOTIFY_EVENTS = ("resume_update_suggested", "email_draft_ready", "apply_needs_you", "account_attention")
