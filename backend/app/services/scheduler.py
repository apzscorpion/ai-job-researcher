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
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
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

# Only account-level problems (a platform paused) email the user; per-job "needs you" notes stay in the app
# Notes that mean a person has to step in (captcha, security check, login) — the user gets an email too
_ALERT_GAP_S = 30 * 60
_last_alert: Dict[int, float] = {}


async def _alert_user(settings, user_id: int, subject: str, body: str) -> None:
    """Email the user about something only they can do; at most one email per 30 minutes."""
    import asyncio
    import time

    from app import crypto
    from app.services import mailer

    if not (settings.smtp_host and settings.smtp_from and settings.smtp_password_enc):
        return
    now = time.monotonic()
    if now - _last_alert.get(user_id, -_ALERT_GAP_S) < _ALERT_GAP_S:
        return
    _last_alert[user_id] = now
    await asyncio.to_thread(
        mailer.send_email, settings.smtp_from, f"[AI Job Researcher] {subject}",
        body + "\n\nOpen the app → Applications (or Settings → Job sites) to take care of it.",
        None, settings.smtp_host, settings.smtp_port, settings.smtp_user or "",
        crypto.decrypt(settings.smtp_password_enc), settings.smtp_from, settings.smtp_tls)


# ---------------------------------------------------------------------------
# Account safety for LinkedIn / Naukri
# ---------------------------------------------------------------------------

# Far below the volumes that get accounts restricted; spread through the day by the gap below
PLATFORM_DAILY_CAP = {"linkedin": 15, "naukri": 10}
# Random wait between two applications on the same site (seconds)
PLATFORM_GAP_S = (60, 75)  # the user asked for ~1 minute between applications on one platform


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
    # restricted / logged out / security check: until the user reconnects; captcha or the site's limit: a day
    days = 30 if re.search(r"logged out|security check|restricted", reason) else 1
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

def apply_options(job) -> List[str]:
    """Every way this job can be applied to, best first (the user picks one on the Jobs page)."""
    from app.services.mailer import usable_recruiter_email

    opts = ["email"] if usable_recruiter_email(job.recruiter_email) else []
    if job.source in PLATFORM_DAILY_CAP:
        opts.append(job.source)
    if job.apply_url or job.source not in PLATFORM_DAILY_CAP:
        opts.append("site")
    return opts


def platform_block(session, user_id: int, settings, route: str, now_utc: datetime) -> Optional[str]:
    """Why LinkedIn/Naukri can't take an application right now; None when it can."""
    from sqlmodel import col, func, select

    from app.models import Application

    paused = active_pauses(settings.platform_pauses, now_utc)
    if route in paused:
        return paused[route]
    mine = (Application.user_id == user_id, Application.method == route,
            col(Application.status).not_in(NOT_ATTEMPTED))
    day_start = local_day_start_utc(user_tz(settings.timezone), now_utc)
    today = session.exec(select(func.count()).select_from(Application)
                         .where(*mine, col(Application.applied_at) >= day_start)).one()
    if today >= PLATFORM_DAILY_CAP[route]:
        return f"{route.title()} daily limit reached ({PLATFORM_DAILY_CAP[route]} today)."
    last = session.exec(select(func.max(Application.applied_at)).where(*mine)).one()
    wait = PLATFORM_GAP_S[0] - (now_utc.replace(tzinfo=None) - last).total_seconds() if last else 0
    if wait > 0:
        return f"The last {route.title()} application was {int((PLATFORM_GAP_S[0] - wait) // 60)} min ago; " \
               f"{route.title()} needs a {PLATFORM_GAP_S[0] // 60}-minute gap to keep your account safe."
    return None


async def apply_now(app_id: int, route: str) -> None:
    """Apply to one job right away (the user clicked Apply on its card)."""
    from sqlmodel import Session, select

    from app.db import engine
    from app.models import Application, Job, Profile, Settings, User

    now_utc = datetime.now(tz=timezone.utc)
    with Session(engine) as session:
        app = session.get(Application, app_id)
        job = session.get(Job, app.job_id) if app else None
        if not app or not job:
            return
        settings = session.exec(select(Settings).where(Settings.user_id == app.user_id)).first()
        profile = session.exec(select(Profile).where(Profile.user_id == app.user_id)).first()
        user = session.get(User, app.user_id)
        try:
            await _apply_one(session, app, job, app.user_id, settings, profile,
                             settings.smtp_from or (user.email if user else ""), route, now_utc)
        finally:
            session.refresh(app)
            if app.status == "applying":  # e.g. the site paused itself: keep it for the queue
                app.status = "queued"
                session.add(app)
                session.commit()


async def _apply_email(app, job, profile, settings, providers, to: str) -> Tuple[str, str]:
    """Write the email with AI and send it from the user's mailbox. Returns (status, note)."""
    import asyncio

    from app import crypto
    from app.config import OLLAMA_MODEL, OLLAMA_URL
    from app.services import mailer

    subject, body = await mailer.draft_email(job, profile, providers, OLLAMA_URL, OLLAMA_MODEL,
                                             email=settings.smtp_from or "")
    if not body:
        return "failed", f"Couldn't write the email to {to} (no AI replied). Apply from the posting: {job.url}"
    app.email_body = body
    smtp_ready = bool(settings.smtp_host and settings.smtp_from and settings.smtp_password_enc)
    if not smtp_ready or not settings.auto_send_email:
        why = "Set up SMTP in Settings to send these automatically" if not smtp_ready else "Auto-send is off in Settings"
        return "draft", f"Email to {to} is written — review and send it below. ({why}.)\nSubject: {subject}"
    if not app.cover_letter:
        app.cover_letter, _ = await mailer.write_cover_letter(job, profile, providers, OLLAMA_URL, OLLAMA_MODEL,
                                                              email=settings.smtp_from or "")
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
    """Company application page. Returns (status, note, sent_to)."""
    from app.services import site_apply

    # No cover letter for site applications (the user's choice: it costs an AI request each); the email
    # path below writes one if the site turns out to want an email
    status, note, url = await site_apply.apply_company_site(job, profile, settings, app.cover_letter or "", email)
    if status == "email":
        app.method = "email"
        status, email_note = await _apply_email(app, job, profile, settings, providers, url)
        return status, f"{note} {email_note}", url
    return status, note, url


def _already_applied(session, user_id: int, app, job) -> Optional[str]:
    """The same role is often listed twice (two boards, reposts): never send a second application."""
    from sqlmodel import col, func, select

    from app.models import Application, Job

    same = session.exec(
        select(Application, Job).join(Job, Job.id == Application.job_id)
        .where(Application.user_id == user_id, Application.id != app.id,
               col(Application.status).in_(["applied", "replied"]),
               func.lower(Job.company) == (job.company or "").lower(),
               func.lower(Job.title) == (job.title or "").lower())).first()
    if same:
        return (f"Skipped: you already applied to {job.title} at {job.company} "
                f"on {same[0].applied_at:%d %b %Y} (the same role was listed twice).")
    return None


async def _apply_one(session, app, job, user_id: int, settings, profile, user_email: str,
                     route: str, now_utc: datetime) -> None:
    """Apply to one queued job and record a plain-English outcome."""
    from app.models import ActivityLog
    from app.services import browser_apply
    from app.services.llm import providers_for

    providers = providers_for(settings)
    app.method = route
    sent_to: Optional[str] = None
    dupe = _already_applied(session, user_id, app, job)
    if dupe:
        app.status, app.notes = "skipped", dupe
        session.add(app)
        session.commit()
        return
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
                await _alert_user(settings, user_id, f"{route.title()} needs you", note)
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
        _notify(session, user_id, "apply_needs_you", job, note)  # in-app only: no email per job
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
    """Start queued applications: several platforms in parallel, one at a time per platform."""
    import asyncio
    import random
    import time

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

        # Browsers running right now across ALL users: the host PC runs them, so cap the total
        global_free = _MAX_TOTAL - session.exec(
            select(func.count()).select_from(Application).where(Application.status == "applying")).one()
        for user_id, items in by_user.items():
            if global_free <= 0:
                break
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

            applied_today = applied_since(col(Application.applied_at) >= day_start)  # in-flight ones count too
            if not can_apply(
                applied_today=applied_today,
                cap=settings.daily_cap,
                last_apply_ts=None,  # gaps are per platform (below), not across all channels
                now=now_utc.astimezone(tz),
                start_hour=settings.apply_start_hour,
                end_hour=settings.apply_end_hour,
            ):
                continue

            paused = active_pauses(settings.platform_pauses, now_utc)
            busy = {_group(j, a.method) for a, j in session.exec(
                select(Application, Job).join(Job, Application.job_id == Job.id)
                .where(Application.user_id == user_id, Application.status == "applying")).all()}
            chosen = []
            for app, job in items:
                if len(chosen) >= min(_MAX_PARALLEL, settings.daily_cap - applied_today, global_free):
                    break
                route = route_for(job)
                group = _group(job, route)
                if group in busy or time.monotonic() - _group_done.get((user_id, group), -1e9) < random.uniform(*PLATFORM_GAP_S):
                    continue  # this platform is busy or had an application under a minute ago
                if route in PLATFORM_DAILY_CAP and not platform_ready(
                    route, paused,
                    applied_since(Application.method == route, col(Application.applied_at) >= day_start),
                    None, now_utc, 0,
                ):
                    continue  # paused or at its daily cap; other platforms still go
                busy.add(group)
                chosen.append((app, route, group))

            global_free -= len(chosen)
            for app, route, _group_key in chosen:
                app.status, app.method, app.notes = "applying", route, "Applying now…"
                app.applied_at = now_utc.replace(tzinfo=None)
                session.add(app)
            session.commit()
            # Different platforms apply at the same time, each in its own browser and DB session
            for app, route, group in chosen:
                task = asyncio.create_task(apply_now(app.id, route))
                _tasks.add(task)
                task.add_done_callback(_tasks.discard)
                task.add_done_callback(lambda _t, k=(user_id, group): _group_done.__setitem__(k, time.monotonic()))
            if chosen:
                logger.info("scheduler: user_id=%s started %s", user_id, [(a.id, g) for a, _r, g in chosen])


async def _recheck_borderline() -> None:
    """Keep the local LLM busy on borderline jobs, one at a time (a run lasts ~2-3 min per job)."""
    from app.services.matcher import recheck_borderline

    try:
        for _ in range(3):
            if not await recheck_borderline():
                break
    except Exception:
        logger.exception("scheduler: borderline re-check failed")


def _group(job, route: str) -> str:
    """Platform an application runs on; one at a time per platform, different platforms in parallel."""
    from urllib.parse import urlparse

    if route != "site":
        return route
    return "site:" + urlparse(job.apply_url or job.url or "").netloc.lower().removeprefix("www.")


_MAX_PARALLEL = 4                       # browsers / AI agents running at once per user
_MAX_TOTAL = int(__import__("os").environ.get("MAX_PARALLEL_APPLIES", "6"))  # ...and for everyone together
_group_done: Dict[tuple, float] = {}    # (user_id, platform) -> when its last application finished
_tasks: set = set()                     # keeps running applications from being garbage-collected


# ---------------------------------------------------------------------------
# APScheduler setup
# ---------------------------------------------------------------------------

def start_scheduler() -> None:
    """Initialise and start the APScheduler AsyncIOScheduler."""
    global _scheduler

    # An "Apply now" cut off by a restart goes back to the queue instead of showing "Applying…" forever
    from sqlmodel import Session, update

    from app.db import engine
    from app.models import Application

    with Session(engine) as session:
        session.exec(update(Application).where(Application.status == "applying").values(status="queued"))
        session.commit()

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
            _recheck_borderline,
            trigger=IntervalTrigger(minutes=2),
            id="recheck_job",
            name="Local-LLM re-check of borderline scores",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        _scheduler.add_job(
            _process_apply_queue,
            trigger=IntervalTrigger(minutes=1),  # each platform waits ~1 min between its applications
            id="apply_job",
            name="Apply queue processor",
            replace_existing=True,
            misfire_grace_time=60,
        )

        _scheduler.start()
        logger.info("scheduler: started — search check hourly, apply check every minute")

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
