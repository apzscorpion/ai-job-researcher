"""
APScheduler-based background scheduler for AI Job Researcher.

Exposes:
    start_scheduler() — called from app lifespan startup
    stop_scheduler()  — called from app lifespan shutdown

Scheduled jobs:
    search_job  — runs hourly; searches for each user whose own
                  search_interval_hours has elapsed since their last search
    apply_job   — runs every 5 minutes; takes the best queued Application per
                  user (remote first, then score) and enforces cap, hours window
                  (in the user's time zone) and gap. Routes: recruiter email (AI-written,
                  sent via the user's SMTP), LinkedIn / Naukri (per-site caps, random
                  gaps, pause on login wall or captcha), else the company's own site.

The queue is the Application table (status "queued"), so it survives restarts.
Pacing logic is the pure function can_apply() so it can be unit-tested.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Gap between consecutive applications (seconds)
_MIN_GAP_S = 120

# Statuses that are not (yet) a real application attempt
NOT_ATTEMPTED = ("queued", "draft")

_scheduler = None  # APScheduler instance


# ---------------------------------------------------------------------------
# Pure pacing function (testable without DB)
# ---------------------------------------------------------------------------

def can_apply(
    applied_today: int,
    cap: int,
    last_apply_ts: Optional[datetime],
    now: datetime,
    start_hour: int,
    end_hour: int,
    min_gap_s: int = _MIN_GAP_S,
) -> bool:
    """Return True when all pacing constraints are satisfied.

    Args:
        applied_today:  Number of applications already sent today.
        cap:            Maximum applications allowed per day.
        last_apply_ts:  Aware timestamp of the last application (or None).
        now:            Current aware datetime in the user's time zone.
        start_hour:     Inclusive start hour for applying (user's local hour).
        end_hour:       Exclusive end hour for applying.
        min_gap_s:      Minimum seconds between consecutive applications.
    """
    if applied_today >= cap:
        return False

    if not (start_hour <= now.hour < end_hour):
        return False

    if last_apply_ts is not None:
        elapsed = (now - last_apply_ts).total_seconds()
        if elapsed < min_gap_s:
            return False

    return True


def user_tz(name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def local_day_start_utc(tz: ZoneInfo, now_utc: datetime) -> datetime:
    """Naive UTC datetime of the user's local midnight (DB timestamps are naive UTC)."""
    local_midnight = now_utc.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Scheduler job: periodic search
# ---------------------------------------------------------------------------

async def _search_due_users() -> None:
    """Run job search for every user whose search interval has elapsed."""
    from sqlmodel import Session, col, select
    from app.db import engine
    from app.models import ActivityLog, Settings
    from app.services import sources

    now = datetime.utcnow()
    with Session(engine) as session:
        for settings in session.exec(select(Settings)).all():
            last = session.exec(
                select(ActivityLog.ts)
                .where(ActivityLog.user_id == settings.user_id, ActivityLog.event == "search_completed")
                .order_by(col(ActivityLog.ts).desc())
            ).first()
            if last and now - last < timedelta(hours=max(1, settings.search_interval_hours)):
                continue
            try:
                await sources.run_search(settings.user_id, session)
            except Exception:
                logger.exception("scheduler: search failed for user_id=%s", settings.user_id)


# ---------------------------------------------------------------------------
# Scheduler job: apply queue drain
# ---------------------------------------------------------------------------

def _notify(session, user_id: int, event: str, job, message: str) -> None:
    from app.models import ActivityLog

    session.add(ActivityLog(
        user_id=user_id,
        event=event,
        detail=json.dumps({"job_id": job.id, "title": job.title, "company": job.company,
                           "message": message}, ensure_ascii=False),
    ))


# ---------------------------------------------------------------------------
# Account safety for LinkedIn / Naukri
# ---------------------------------------------------------------------------

# Far below the volumes that get accounts restricted; spread through the day by the gap below
PLATFORM_DAILY_CAP = {"linkedin": 15, "naukri": 10}
# Random wait between two applications on the same site (seconds)
PLATFORM_GAP_S = (8 * 60, 20 * 60)


def active_pauses(raw: Optional[str], now_utc: datetime) -> Dict[str, str]:
    """{platform: reason} for pauses that have not expired (timestamps are naive UTC ISO strings)."""
    from app.models import json_decode

    pauses = json_decode(raw, {}) or {}
    return {k: v.get("reason", "") for k, v in pauses.items()
            if datetime.fromisoformat(v["until"]) > now_utc.replace(tzinfo=None)}


def pause_platform(settings, platform: str, reason: str, now_utc: datetime) -> None:
    """Stop using *platform* until the user reconnects (login problems) or for 24 h (captcha)."""
    from app.models import json_decode

    pauses = json_decode(settings.platform_pauses, {}) or {}
    days = 30 if ("logged out" in reason or "security check" in reason) else 1
    until = now_utc.replace(tzinfo=None) + timedelta(days=days)
    pauses[platform] = {"until": until.isoformat(), "reason": reason}
    settings.platform_pauses = json.dumps(pauses)


def platform_ready(platform: str, paused: Dict[str, str], applied_today: int,
                   last_ts: Optional[datetime], now: datetime, gap_s: float) -> bool:
    """LinkedIn / Naukri pacing: not paused, under the daily cap, and the random gap has passed."""
    if platform in paused or applied_today >= PLATFORM_DAILY_CAP.get(platform, 10**6):
        return False
    return last_ts is None or (now - last_ts).total_seconds() >= gap_s


def route_for(job) -> str:
    """How a job gets applied to: email, linkedin, naukri or site."""
    from app.services.mailer import usable_recruiter_email

    if usable_recruiter_email(job.recruiter_email):
        return "email"
    return job.source if job.source in PLATFORM_DAILY_CAP else "site"


# ---------------------------------------------------------------------------
# Scheduler job: apply queue drain
# ---------------------------------------------------------------------------

async def _apply_email(app, job, profile, settings, providers, to: str) -> Tuple[str, str]:
    """Write the email with AI and send it from the user's mailbox. Returns (status, note)."""
    import asyncio

    from app import crypto
    from app.config import OLLAMA_MODEL, OLLAMA_URL
    from app.services import mailer

    subject, body = await mailer.draft_email(job, profile, providers, OLLAMA_URL, OLLAMA_MODEL)
    if not body:
        return "failed", f"Couldn't write the email to {to} (no AI replied). Apply from the posting: {job.url}"
    app.email_body = body
    smtp_ready = bool(settings.smtp_host and settings.smtp_from and settings.smtp_password_enc)
    if not smtp_ready or not settings.auto_send_email:
        why = "Set up SMTP in Settings to send these automatically" if not smtp_ready else "Auto-send is off in Settings"
        return "draft", f"Email to {to} is written — review and send it below. ({why}.)\nSubject: {subject}"
    if not app.cover_letter:
        app.cover_letter, _ = await mailer.write_cover_letter(job, profile, providers, OLLAMA_URL, OLLAMA_MODEL)
    extra = [mailer.cover_letter_docx(app.cover_letter, job, profile)] if app.cover_letter else []
    sent = await asyncio.to_thread(
        mailer.send_email, to, subject, body, profile.resume_path, settings.smtp_host, settings.smtp_port,
        settings.smtp_user or "", crypto.decrypt(settings.smtp_password_enc), settings.smtp_from, settings.smtp_tls,
        extra)
    if not sent:
        return "failed", (f"Sending to {to} failed — check Settings → Send Test Email. "
                          f"The email is saved below.\nSubject: {subject}")
    attached = "your résumé and cover letter" if extra else "your résumé"
    return "applied", f"Emailed {to} from {settings.smtp_from} with {attached} attached.\nSubject: {subject}"


async def _apply_site(app, job, profile, settings, providers, email: str) -> Tuple[str, str, Optional[str]]:
    """Cover letter + company application page. Returns (status, note, sent_to)."""
    from app.config import OLLAMA_MODEL, OLLAMA_URL
    from app.services import mailer, site_apply

    if not app.cover_letter:
        app.cover_letter, _ = await mailer.write_cover_letter(job, profile, providers, OLLAMA_URL, OLLAMA_MODEL)
    status, note, url = await site_apply.apply_company_site(job, profile, settings, app.cover_letter or "", email)
    if status == "email":
        app.method = "email"
        status, email_note = await _apply_email(app, job, profile, settings, providers, url)
        return status, f"{note} {email_note}", url
    return status, note, url


async def _apply_one(session, app, job, user_id: int, settings, profile, user_email: str,
                     route: str, now_utc: datetime) -> None:
    """Apply to one queued job and record a plain-English outcome."""
    from app.models import ActivityLog
    from app.services import browser_apply
    from app.services.llm import providers_for

    providers = providers_for(settings)
    app.method = route
    sent_to: Optional[str] = None
    try:
        if route == "email":
            status, note = await _apply_email(app, job, profile, settings, providers, job.recruiter_email)
            sent_to = job.recruiter_email
        elif route in PLATFORM_DAILY_CAP:
            apply = browser_apply.apply_linkedin if route == "linkedin" else browser_apply.apply_naukri
            status, note = await apply(job, profile, settings, user_id)
            if status == "paused":
                pause_platform(settings, route, note, now_utc)
                session.add(settings)
                _notify(session, user_id, "account_attention", job, note)
                session.commit()
                logger.warning("scheduler: %s paused for user_id=%s: %s", route, user_id, note)
                return  # job stays queued for when the account is back
            if status == "external":  # stays method=linkedin/naukri so the visit counts toward that site's cap
                status, site_note, sent_to = await _apply_site(app, job, profile, settings, providers, user_email)
                note = f"{note} {site_note}"
            elif status == "applied":
                sent_to = job.url
        else:
            status, note, sent_to = await _apply_site(app, job, profile, settings, providers, user_email)
    except Exception:
        logger.exception("scheduler: apply failed for user_id=%s job_id=%s", user_id, job.id)
        status, note = "failed", f"Unexpected error while applying. Apply here: {job.url}"

    if status == "needs_manual":
        _notify(session, user_id, "apply_needs_you", job, note)
    elif status == "draft":
        _notify(session, user_id, "email_draft_ready", job, note.split("\n")[0])

    app.status = status
    app.notes = note
    app.sent_to = sent_to
    app.applied_at = now_utc.replace(tzinfo=None)
    session.add(app)
    session.add(ActivityLog(user_id=user_id, event="application_attempted",
                            detail=f"job_id={job.id} method={app.method} status={status}"))
    session.commit()
    logger.info("scheduler: user_id=%s job_id=%s method=%s status=%s", user_id, job.id, app.method, status)


async def _process_apply_queue() -> None:
    """Apply to at most one queued job per user per run, respecting pacing and account safety."""
    import random

    from sqlmodel import Session, col, func, select
    from app.db import engine
    from app.models import Application, Job, Profile, Settings, User

    now_utc = datetime.now(tz=timezone.utc)

    with Session(engine) as session:
        queued = session.exec(
            select(Application, Job)
            .join(Job, Application.job_id == Job.id)  # type: ignore[arg-type]
            .where(Application.status == "queued")
            .order_by(col(Job.is_remote).desc(), col(Job.score).desc(), col(Application.applied_at))
        ).all()

        by_user: Dict[int, list] = {}
        for app, job in queued:
            by_user.setdefault(app.user_id, []).append((app, job))

        for user_id, items in by_user.items():
            settings = session.exec(select(Settings).where(Settings.user_id == user_id)).first()
            profile = session.exec(select(Profile).where(Profile.user_id == user_id)).first()
            if not settings or not profile:
                logger.warning("scheduler: missing settings/profile for user_id=%s", user_id)
                continue
            if not settings.auto_apply_enabled:
                continue

            tz = user_tz(settings.timezone)
            day_start = local_day_start_utc(tz, now_utc)
            attempted = (Application.user_id == user_id, col(Application.status).not_in(NOT_ATTEMPTED))

            def applied_since(*conds) -> int:
                return session.exec(select(func.count()).select_from(Application).where(*attempted, *conds)).one()

            def last_attempt(*conds) -> Optional[datetime]:
                ts = session.exec(select(func.max(Application.applied_at)).where(*attempted, *conds)).one()
                return ts.replace(tzinfo=timezone.utc) if ts else None

            if not can_apply(
                applied_today=applied_since(col(Application.applied_at) >= day_start),
                cap=settings.daily_cap,
                last_apply_ts=last_attempt(),
                now=now_utc.astimezone(tz),
                start_hour=settings.apply_start_hour,
                end_hour=settings.apply_end_hour,
            ):
                continue

            paused = active_pauses(settings.platform_pauses, now_utc)
            gap = random.uniform(*PLATFORM_GAP_S)
            for app, job in items:
                route = route_for(job)
                if route in PLATFORM_DAILY_CAP and not platform_ready(
                    route, paused,
                    applied_since(Application.method == route, col(Application.applied_at) >= day_start),
                    last_attempt(Application.method == route), now_utc, gap,
                ):
                    continue  # try the next job; this site waits
                user = session.get(User, user_id)
                email = settings.smtp_from or (user.email if user else "")
                await _apply_one(session, app, job, user_id, settings, profile, email, route, now_utc)
                break


# ---------------------------------------------------------------------------
# APScheduler setup
# ---------------------------------------------------------------------------

def start_scheduler() -> None:
    """Initialise and start the APScheduler AsyncIOScheduler."""
    global _scheduler

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
        from apscheduler.triggers.interval import IntervalTrigger  # type: ignore

        _scheduler = AsyncIOScheduler()

        _scheduler.add_job(
            _search_due_users,
            trigger=IntervalTrigger(hours=1),
            id="search_job",
            name="Periodic job search",
            replace_existing=True,
            misfire_grace_time=300,
            next_run_time=datetime.now() + timedelta(minutes=1),  # first check soon after startup
        )

        _scheduler.add_job(
            _process_apply_queue,
            trigger=IntervalTrigger(minutes=5),
            id="apply_job",
            name="Apply queue processor",
            replace_existing=True,
            misfire_grace_time=60,
        )

        _scheduler.start()
        logger.info("scheduler: started — search check hourly, apply check every 5min")

    except Exception:
        logger.exception("scheduler: failed to start")


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("scheduler: stopped")
        except Exception:
            logger.warning("scheduler: error during shutdown", exc_info=True)
        _scheduler = None
