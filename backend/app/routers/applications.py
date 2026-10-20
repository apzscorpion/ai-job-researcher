"""
Applications router: list with rich filtering, single view, status update, and daily stats.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, func, or_, select

from app.auth import get_current_user
from app.db import get_session
from app import crypto
from app.models import ActivityLog, Application, Job, Settings, User
from app.services.scheduler import NOT_ATTEMPTED, local_day_start_utc, user_tz

router = APIRouter(prefix="/api/applications", tags=["applications"])


def _user_skills(user_id: int, session: Session) -> List[str]:
    from app.models import Profile
    from app.services.matcher import selected_skills

    return selected_skills(session.exec(select(Profile).where(Profile.user_id == user_id)).first())


def _app_to_dict(app: Application, job: Optional[Job] = None, skills: Optional[List[str]] = None) -> Dict[str, Any]:
    from app.services.matcher import skill_match

    d: Dict[str, Any] = {
        "id": app.id,
        "user_id": app.user_id,
        "job_id": app.job_id,
        "method": app.method,
        "status": app.status,
        "applied_at": app.applied_at,
        "email_body": app.email_body,
        "cover_letter": app.cover_letter,
        "sent_to": app.sent_to,
        "notes": app.notes,
    }
    if job:
        matched, missing = skill_match(skills or [], f"{job.title}\n{job.description or ''}")
        d["job"] = {
            "matched_skills": matched,
            "missing_skills": missing,
            "apply_url": job.apply_url,
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "url": job.url,
            "source": job.source,
            "score": job.score,
            "salary_min": job.salary_min,
            "salary_max": job.salary_max,
            "company_rating": job.company_rating,
            "is_remote": job.is_remote,
            "recruiter_email": job.recruiter_email,
        }
    return d


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

@router.get("", include_in_schema=False)
@router.get("/")
def list_applications(
    q: Optional[str] = Query(None, description="Search in title/company/notes"),
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    max_score: Optional[float] = Query(None),
    min_salary: Optional[int] = Query(None),
    max_salary: Optional[int] = Query(None),
    min_rating: Optional[float] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    skip: Optional[int] = Query(None, ge=0),
    limit: Optional[int] = Query(None, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    # Join Application and Job
    stmt = (
        select(Application, Job)
        .join(Job, Application.job_id == Job.id)  # type: ignore[arg-type]
        .where(Application.user_id == current_user.id)
    )

    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                col(Job.title).ilike(like),
                col(Job.company).ilike(like),
                col(Application.notes).ilike(like),
            )
        )
    if status:
        stmt = stmt.where(Application.status == status)
    if source:
        stmt = stmt.where(Job.source == source)
    if min_score is not None:
        stmt = stmt.where(col(Job.score) >= min_score)
    if max_score is not None:
        stmt = stmt.where(col(Job.score) <= max_score)
    if min_salary is not None:
        stmt = stmt.where(col(Job.salary_min) >= min_salary)
    if max_salary is not None:
        stmt = stmt.where(col(Job.salary_max) <= max_salary)
    if min_rating is not None:
        stmt = stmt.where(col(Job.company_rating) >= min_rating)
    if date_from is not None:
        stmt = stmt.where(col(Application.applied_at) >= date_from)
    if date_to is not None:
        stmt = stmt.where(col(Application.applied_at) <= date_to)

    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()

    effective_limit = limit if limit is not None else page_size
    effective_offset = skip if skip is not None else (page - 1) * page_size

    stmt = stmt.offset(effective_offset).limit(effective_limit).order_by(
        col(Application.applied_at).desc()
    )
    rows = session.exec(stmt).all()
    skills = _user_skills(current_user.id, session)

    return {
        "total": total,
        "page": page,
        "page_size": effective_limit,
        "items": [_app_to_dict(app, job, skills) for app, job in rows],
    }


# ---------------------------------------------------------------------------
# GET /{app_id}
# ---------------------------------------------------------------------------

@router.get("/{app_id}")
def get_application(
    app_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    app = session.get(Application, app_id)
    if not app or app.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    job = session.get(Job, app.job_id)
    return _app_to_dict(app, job, _user_skills(current_user.id, session))


# ---------------------------------------------------------------------------
# PUT /{app_id}/status
# ---------------------------------------------------------------------------

class StatusUpdate(BaseModel):
    status: str


VALID_STATUSES = {"queued", "draft", "applied", "needs_manual", "failed", "replied", "skipped"}


@router.put("/{app_id}/status")
@router.patch("/{app_id}/status", include_in_schema=False)
def update_status(
    app_id: int,
    body: StatusUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    if body.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{body.status}'. Valid: {VALID_STATUSES}",
        )

    app = session.get(Application, app_id)
    if not app or app.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Application not found")

    old_status = app.status
    app.status = body.status
    session.add(app)

    log = ActivityLog(
        user_id=current_user.id,
        event="application_status_changed",
        detail=f"Application {app_id}: {old_status} → {body.status}",
    )
    session.add(log)
    session.commit()
    session.refresh(app)

    job = session.get(Job, app.job_id)
    return _app_to_dict(app, job, _user_skills(current_user.id, session))


# ---------------------------------------------------------------------------
# POST /{app_id}/send  (send a reviewed email draft)
# ---------------------------------------------------------------------------

class SendBody(BaseModel):
    email_body: Optional[str] = None  # edited text; defaults to the stored draft
    subject: Optional[str] = None


@router.post("/retry-all")
def retry_all(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Start over on everything not yet sent: back to the queue with a fresh cover letter and email."""
    apps = session.exec(select(Application).where(
        Application.user_id == current_user.id,
        col(Application.status).in_(["queued", "needs_manual", "failed", "draft"]))).all()
    for app in apps:
        app.status, app.method, app.notes, app.sent_to = "queued", "pending", "Retrying — queued again by you.", None
        app.cover_letter = app.email_body = None  # rewritten in the current (formal) style
        app.applied_at = datetime.utcnow()
        session.add(app)
    session.add(ActivityLog(user_id=current_user.id, event="applications_retried", detail=f"{len(apps)} re-queued"))
    session.commit()
    return {"requeued": len(apps)}


@router.delete("/{app_id}")
def cancel_application(
    app_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Cancel an application that hasn't been sent; the job goes back to "Not applied yet"."""
    app = session.get(Application, app_id)
    if not app or app.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.status in ("applied", "replied"):
        raise HTTPException(status_code=409, detail="Already sent — it can't be taken back from here")
    if app.status == "applying":
        raise HTTPException(status_code=409, detail="It's being sent right now — wait a minute and check again")
    session.delete(app)
    session.add(ActivityLog(user_id=current_user.id, event="application_cancelled",
                            detail=f"Application {app_id} for job_id={app.job_id} was {app.status}"))
    session.commit()
    return {"cancelled": app_id}


@router.post("/{app_id}/send")
def send_draft(
    app_id: int,
    body: SendBody,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    from app.models import Profile
    from app.services.mailer import cover_letter_docx, send_email, usable_recruiter_email

    app = session.get(Application, app_id)
    if not app or app.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    job = session.get(Job, app.job_id)
    to = next((a for a in (job.recruiter_email if job else None, app.sent_to) if usable_recruiter_email(a)), None)
    if app.status != "draft" or not job or not to:
        raise HTTPException(status_code=409, detail="Only email drafts can be sent")
    settings = session.exec(select(Settings).where(Settings.user_id == current_user.id)).first()
    if not settings or not settings.smtp_host or not settings.smtp_from:
        raise HTTPException(status_code=400, detail="Set up SMTP in Settings before sending")
    profile = session.exec(select(Profile).where(Profile.user_id == current_user.id)).first()

    text = (body.email_body or app.email_body or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Email body is empty")
    subject = (body.subject or "").strip() or f"Application for {job.title} at {job.company}"

    sent = send_email(
        to=to,
        subject=subject,
        body=text,
        resume_path=profile.resume_path if profile else None,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_user=settings.smtp_user or "",
        smtp_password=crypto.decrypt(settings.smtp_password_enc or ""),
        smtp_from=settings.smtp_from,
        use_tls=settings.smtp_tls,
        extra_files=[cover_letter_docx(app.cover_letter, job, profile)] if app.cover_letter and profile else None,
    )
    if not sent:
        raise HTTPException(status_code=502, detail="SMTP send failed — check Settings → Test Email")

    app.email_body = text
    app.status = "applied"
    app.sent_to = to
    attached = "your résumé and cover letter" if app.cover_letter else "your résumé"
    app.notes = f"Emailed {to} from {settings.smtp_from} with {attached} attached.\nSubject: {subject}"
    app.applied_at = datetime.utcnow()
    session.add(app)
    session.add(ActivityLog(user_id=current_user.id, event="application_sent",
                            detail=f"Application {app_id} emailed to {job.recruiter_email}"))
    session.commit()
    session.refresh(app)
    return _app_to_dict(app, job, _user_skills(current_user.id, session))


# ---------------------------------------------------------------------------
# GET /stats/today
# ---------------------------------------------------------------------------

@router.get("/stats/today")
def stats_today(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    settings = session.exec(select(Settings).where(Settings.user_id == current_user.id)).first()
    tz = user_tz(settings.timezone if settings else None)
    mine = Application.user_id == current_user.id
    attempted = col(Application.status).not_in(NOT_ATTEMPTED)

    from app.services.llm import providers_for
    from collections import Counter

    from app.services.llm import ai_mode, quota_status
    from app.services.scheduler import PLATFORM_DAILY_CAP, active_pauses, route_for

    now = datetime.now(timezone.utc)
    today = col(Application.applied_at) >= local_day_start_utc(tz, now)
    sent = col(Application.status).in_(("applied", "replied"))

    def count(*conds) -> int:
        return session.exec(select(func.count()).select_from(Application).where(mine, *conds)).one()

    def by_channel(*conds) -> Dict[str, int]:
        rows = session.exec(select(Application.method, func.count()).where(mine, sent, *conds)
                            .group_by(Application.method)).all()
        return {method: n for method, n in rows}

    return {
        "applied_today": count(attempted, today),
        "daily_cap": settings.daily_cap if settings else 30,
        "total_applications": count(attempted),
        "sent_today": count(sent, today),
        "sent_total": count(sent),
        "sent_today_by_channel": by_channel(today),
        "sent_total_by_channel": by_channel(),
        "needs_you_count": count(Application.status == "needs_manual"),
        "failed_count": count(Application.status == "failed"),
        "replied_count": count(Application.status == "replied"),
        "queued_count": count(Application.status == "queued"),
        "draft_count": count(Application.status == "draft"),
        "readiness": {
            "auto_apply": bool(settings and settings.auto_apply_enabled),
            "email_ready": bool(settings and settings.smtp_host and settings.smtp_from and settings.smtp_password_enc),
            "auto_send_email": bool(settings and settings.auto_send_email),
            "ai_providers": len(providers_for(settings)) if settings else 0,
            "paused": active_pauses(settings.platform_pauses if settings else None, now),
            # queued jobs per channel, so the UI can say "30 jobs are waiting for LinkedIn"
            "ai_quota": quota_status(settings) if settings else [],
            "ai_mode": ai_mode(settings) if settings else "semi_ai",
            # applications attempted today per platform, for "LinkedIn 7/15 today"
            "platform_today": {p: count(attempted, today, Application.method == p) for p in PLATFORM_DAILY_CAP},
            "queued_by_route": dict(Counter(route_for(j) for j in session.exec(
                select(Job).join(Application, Application.job_id == Job.id)
                .where(Application.user_id == current_user.id, Application.status == "queued")).all())),
            "platform_daily_cap": PLATFORM_DAILY_CAP,
            "apply_hours": [settings.apply_start_hour, settings.apply_end_hour] if settings else [9, 21],
            "timezone": tz.key,
        },
    }
