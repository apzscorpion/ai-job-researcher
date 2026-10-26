"""
Applications router: list with rich filtering, single view, status update, and daily stats.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
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

# ---------------------------------------------------------------------------
# Fill with AI: the agent fills the application page while the user watches live (and can step in)
# ---------------------------------------------------------------------------

def _start_assist(user: User, app: Application, review: bool = False) -> Dict[str, Any]:
    from app.security import rate_limit
    from app.services import assist, connections

    rate_limit(f"assist:{user.id}", 20, 3600, "That's a lot of AI fills for one hour — try again later")
    if assist.busy(user.id) or connections._remote.get(user.id, {}).get("state") == "open":
        raise HTTPException(status_code=409, detail="A live window is already open — finish or close it first")
    if app.status in ("applied", "replied", "applying"):
        raise HTTPException(status_code=409, detail=f"Already {app.status}")
    assist.start(user.id, app.id, review)
    return {"started": True, "app_id": app.id, "review": review}


@router.post("/{app_id}/assist")
async def start_assist(
    app_id: int,
    review: bool = False,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    app = session.get(Application, app_id)
    if not app or app.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    return _start_assist(current_user, app, review)


class LinkBody(BaseModel):
    review: bool = False  # fill everything but leave the final Submit to the user
    url: str = Field(..., min_length=8, max_length=2000)
    title: str = Field("", max_length=200)
    company: str = Field("", max_length=200)


@router.post("/from-link")
async def apply_from_link(
    body: LinkBody,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Any job / application URL: add it and let the AI fill the application live."""
    from urllib.parse import urlparse

    from app.security import check_public_url, http_url_or_none

    url = body.url.strip()
    try:
        check_public_url(url, ("https", "http"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Link: {exc}")
    host = urlparse(url).netloc.removeprefix("www.")
    job = session.exec(select(Job).where(Job.user_id == current_user.id, Job.url == url)).first()
    if not job:
        job = Job(user_id=current_user.id, url=url, apply_url=http_url_or_none(url), source="link",
                  title=body.title.strip() or f"Application at {host}", company=body.company.strip() or host,
                  description="", status="scored")
        session.add(job)
        session.commit()
        session.refresh(job)
    app = session.exec(select(Application).where(Application.user_id == current_user.id,
                                                 Application.job_id == job.id)).first()
    if not app:
        app = Application(user_id=current_user.id, job_id=job.id, method="site", status="needs_manual",
                          notes="Added from a link — the AI fills it while you watch.", applied_at=datetime.utcnow())
        session.add(app)
        session.commit()
        session.refresh(app)
    return _start_assist(current_user, app, body.review)


@router.get("/assist/status")
def assist_status(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    from app.services import assist

    return assist.status(current_user.id)


@router.post("/assist/continue")
def assist_continue(current_user: User = Depends(get_current_user)) -> Dict[str, bool]:
    from app.services import assist

    if not assist.resume(current_user.id):
        raise HTTPException(status_code=409, detail="The AI isn't waiting for you right now")
    return {"ok": True}


class ReplyBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


@router.post("/assist/reply")
def assist_reply(body: ReplyBody, current_user: User = Depends(get_current_user)) -> Dict[str, bool]:
    from app.services import assist

    if not assist.reply(current_user.id, body.text.strip()):
        raise HTTPException(status_code=409, detail="No AI fill is running")
    return {"ok": True}


_ASSIST_FILE_TYPES = {".pdf", ".doc", ".docx", ".txt", ".rtf", ".png", ".jpg", ".jpeg", ".odt"}


@router.post("/assist/upload")
async def assist_upload(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """A file for the AI to attach to the form (certificate, portfolio, transcript…)."""
    import re
    from pathlib import Path

    from app.config import DATA_DIR
    from app.security import rate_limit
    from app.services import assist

    rate_limit(f"assist-upload:{current_user.id}", 20, 3600)
    name = Path(file.filename or "file").name
    suffix = Path(name).suffix.lower()
    if suffix not in _ASSIST_FILE_TYPES:
        raise HTTPException(status_code=422, detail=f"Unsupported file type {suffix or '(none)'}")
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Files must be under 10 MB")
    folder = DATA_DIR / "tmp" / "assist" / str(current_user.id)
    folder.mkdir(parents=True, exist_ok=True)
    safe = (re.sub(r"[^\w.-]+", "_", Path(name).stem)[:60].strip("._") or "file") + suffix
    path = folder / safe
    path.write_bytes(content)
    if not assist.add_file(current_user.id, safe, str(path)):
        raise HTTPException(status_code=409, detail="No AI fill is running")
    return {"ok": True, "name": safe}


@router.post("/assist/pause")
def assist_pause(current_user: User = Depends(get_current_user)) -> Dict[str, bool]:
    from app.services import assist

    if not assist.pause(current_user.id):
        raise HTTPException(status_code=409, detail="The AI isn't working right now")
    return {"ok": True}


@router.post("/assist/unpause")
def assist_unpause(current_user: User = Depends(get_current_user)) -> Dict[str, bool]:
    from app.services import assist

    if not assist.unpause(current_user.id):
        raise HTTPException(status_code=409, detail="Nothing is paused")
    return {"ok": True}


@router.post("/assist/close")
def assist_close(current_user: User = Depends(get_current_user)) -> Dict[str, bool]:
    from app.services import assist

    assist.close(current_user.id)
    return {"closed": True}


@router.post("/{app_id}/mark-applied")
def mark_applied(
    app_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """The user submitted it themselves (e.g. after a captcha): record it as applied."""
    from app.services import assist

    app = session.get(Application, app_id)
    if not app or app.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    st = assist.status(current_user.id)
    app.status, app.applied_at = "applied", datetime.utcnow()
    app.notes = "Marked as applied by you" + (f" ({st['url']})" if st.get("app_id") == app_id and st.get("url") else ".")
    session.add(app)
    session.commit()
    if st.get("app_id") == app_id:
        assist.close(current_user.id)
    return {"applied": app_id}


# Why an application stopped, in plain words. First match wins; ai_can = retrying lets the AI finish it.
REASONS = [
    ("unconfirmed", r"no confirmation", "Sent, but the site didn't confirm",
     "The AI pressed Submit but no 'thank you' page appeared.", "Check your email for a confirmation.", False),
    ("closed", r"expired|no longer accepting|not currently accepting|job is closed", "Job closed",
     "The employer stopped taking applications.", "Nothing to do — cancel it.", False),
    ("captcha", r"captcha|cloudflare|verify you are human|human verification", "Human check (captcha)",
     "The site asked to prove a person is there; the AI never solves these.",
     "Open it with 🤖 Fill with AI and solve the check in the live view.", False),
    ("account", r"sign.?up|account creation|create an account|needs an account|log ?in|password|authentication",
     "Needs an account on the site", "The site wants you to sign up or log in, and the AI never creates "
     "accounts or types passwords.", "Sign up there once, then retry — or apply there yourself.", False),
    ("ai_quota", r"AI quota|no usable reply", "AI ran out of quota",
     "Every AI key was used up while it was filling the form.", "Retry when the AI quota is back (see the header).", True),
    ("blank_page", r"about:blank|blank page|no job posting url|no url",
     "Lost the company link (fixed)", "The job applies on the company's site, but the link didn't come through "
     "in time, so the AI started on an empty page.", "Retry: the AI now clicks Apply itself and follows it.", True),
    ("no_button", r"no apply button|could not click|not clickable|apply button failed|no clear application",
     "Couldn't find the Apply button (fixed)", "The fixed rules missed the button.",
     "Retry: the AI now looks for the button itself.", True),
    ("too_long", r"couldn't finish in \d+ steps", "Form too long for one try",
     "The AI used all its steps before reaching Submit.", "Retry — or open it with 🤖 Fill with AI and watch.", True),
    ("qualified", r"years of experience|requires candidates|not supported|multiple |missing resume|face-to-face",
     "AI gave up on its own (fixed)", "The AI refused (e.g. 'needs 8+ years') or stopped on something "
     "it should have handled.", "Retry: it now applies anyway and only stops for captcha, login or payment.", True),
]


def reason_of(note: str) -> str:
    import re

    return next((key for key, rx, *_ in REASONS if re.search(rx, note or "", re.I)), "other")


def _stuck(user_id: int, session: Session):
    return session.exec(select(Application, Job).join(Job, Job.id == Application.job_id).where(
        Application.user_id == user_id, col(Application.status).in_(["needs_manual", "failed"]))).all()


@router.get("/report")
def needs_you_report(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """Applications that stopped, grouped by why, with what happens next."""
    info = {k: dict(key=k, title=t, why=w, next=n, ai_can=a) for k, _, t, w, n, a in REASONS}
    info["other"] = dict(key="other", title="Other errors", why="Something unexpected went wrong.",
                         next="Retry — or open it with 🤖 Fill with AI and watch.", ai_can=True)
    groups: Dict[str, Dict[str, Any]] = {}
    for app, job in _stuck(current_user.id, session):
        g = groups.setdefault(reason_of(app.notes), {**info[reason_of(app.notes)], "count": 0, "jobs": []})
        g["count"] += 1
        g["jobs"].append({"id": app.id, "title": job.title, "company": job.company})
    return sorted(groups.values(), key=lambda g: (not g["ai_can"], -g["count"]))


@router.post("/report/{key}/retry")
def retry_reason(
    key: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Queue again every stopped application with this reason."""
    apps = [app for app, _ in _stuck(current_user.id, session) if reason_of(app.notes) == key]
    return _requeue(apps, current_user.id, session)


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


VALID_STATUSES = {"queued", "draft", "applied", "needs_manual", "failed", "replied", "skipped", "cancelled"}


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


def _requeue(apps: List[Application], user_id: int, session: Session) -> Dict[str, Any]:
    for app in apps:
        app.status, app.method, app.notes, app.sent_to = "queued", "pending", "Retrying — queued again by you.", None
        app.cover_letter = app.email_body = None  # rewritten in the current (formal) style
        app.applied_at = datetime.utcnow()
        session.add(app)
    session.add(ActivityLog(user_id=user_id, event="applications_retried", detail=f"{len(apps)} re-queued"))
    session.commit()
    return {"requeued": len(apps)}


@router.post("/retry-all")
def retry_all(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Start over on everything not yet sent: back to the queue with a fresh cover letter and email."""
    apps = session.exec(select(Application).where(
        Application.user_id == current_user.id,
        col(Application.status).in_(["queued", "needs_manual", "failed", "draft"]))).all()
    return _requeue(apps, current_user.id, session)


@router.delete("/{app_id}")
def cancel_application(
    app_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Cancel an application that hasn't been sent: it stays as "cancelled" with the reason it stopped,
    and the job shows under "Cancelled apply" (never re-queued)."""
    app = session.get(Application, app_id)
    if not app or app.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.status in ("applied", "replied"):
        raise HTTPException(status_code=409, detail="Already sent — it can't be taken back from here")
    if app.status == "cancelled":
        return {"cancelled": app_id}
    if app.status == "applying":
        raise HTTPException(status_code=409, detail="It's being sent right now — wait a minute and check again")
    was = app.status
    app.status = "cancelled"
    app.notes = (app.notes or "").strip() or "Cancelled by you — no reason was recorded."  # the AI's own words
    session.add(app)
    session.add(ActivityLog(user_id=current_user.id, event="application_cancelled",
                            detail=f"Application {app_id} for job_id={app.job_id} was {was}"))
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
