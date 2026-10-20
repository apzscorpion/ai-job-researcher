"""
Assisted applying: the AI agent fills an application page in a browser on this machine while the user
watches it live in the website (same live view as job-site sign-in) and can click/type at any time —
captchas, logins, answers the AI can't know. When the site confirms, the application becomes "applied".

start(user_id, app_id)   — open the job's application page in the right signed-in profile, run the agent
status(user_id)          — {state, app_id, url, log}
resume(user_id)          — after the user took over: let the agent continue
close(user_id)
States: starting → agent (AI is filling) → waiting_you (needs the user; press Continue) → applied | closed | error
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict

from app.services import connections

logger = logging.getLogger(__name__)

_IDLE_S = 20 * 60
_sessions: Dict[int, Dict[str, Any]] = {}


def status(user_id: int) -> Dict[str, Any]:
    s = _sessions.get(user_id)
    if not s:
        return {"state": "none"}
    page = s.get("page")
    return {"state": s["state"], "app_id": s["app_id"], "log": s["log"][-30:],
            "url": page.url if page and s["state"] not in ("closed", "error") else ""}


def busy(user_id: int) -> bool:
    return _sessions.get(user_id, {}).get("state") in ("starting", "agent", "waiting_you")


def start(user_id: int, app_id: int, review: bool = False) -> None:
    session: Dict[str, Any] = {"app_id": app_id, "state": "starting", "log": [], "page": None, "review": review,
                               "done": asyncio.Event(), "resume": asyncio.Event(), "active": time.monotonic()}
    _sessions[user_id] = session
    session["task"] = asyncio.create_task(_run(user_id, app_id, session))


def resume(user_id: int) -> bool:
    s = _sessions.get(user_id)
    if not s or s["state"] != "waiting_you":
        return False
    s["resume"].set()
    return True


def close(user_id: int) -> None:
    """Stop right now — including an AI that is mid-form (it must never go on to submit after Close)."""
    s = _sessions.get(user_id)
    if s:
        s["done"].set()
        s["resume"].set()
        task = s.get("task")
        if task and not task.done():
            task.cancel()  # the browser closes in _run's finally blocks
        if s["state"] in ("starting", "agent", "waiting_you"):
            s["state"] = "closed"
            _say(s, "Closed by you — nothing more will be done on this page.")


def _say(session: Dict[str, Any], text: str) -> None:
    session["log"].append({"time": datetime.now().strftime("%H:%M:%S"), "text": text})
    session["active"] = time.monotonic()


async def _run(user_id: int, app_id: int, session: Dict[str, Any]) -> None:
    from playwright.async_api import async_playwright  # type: ignore
    from sqlmodel import Session, select

    from app.db import engine
    from app.models import Application, Job, Profile, Settings, User
    from app.services.agent_apply import agent_apply
    from app.security import http_url_or_none

    with Session(engine) as db:
        app = db.get(Application, app_id)
        job = db.get(Job, app.job_id) if app else None
        if not app or not job or app.user_id != user_id:
            session["state"] = "error"
            return
        profile = db.exec(select(Profile).where(Profile.user_id == user_id)).first()
        settings = db.exec(select(Settings).where(Settings.user_id == user_id)).first()
        user = db.get(User, user_id)
        email = (settings.smtp_from if settings else None) or (user.email if user else "")
        url = (http_url_or_none(job.apply_url) or http_url_or_none(app.sent_to) or job.url)
        # the user's own signed-in browser for that site (LinkedIn / Naukri / other job sites)
        browser_profile = job.source if job.source in ("linkedin", "naukri") else "sites"
        cover_path = ""
        if app.cover_letter:
            from app.services.mailer import cover_letter_docx

            cover_path = cover_letter_docx(app.cover_letter, job, profile)
        try:
            async with connections._lock(user_id, browser_profile):
                async with async_playwright() as p:
                    context = await connections._open(p, user_id, browser_profile, background=True)
                    if browser_profile == "sites":
                        await context.route("**/*", connections._block_private)
                    try:
                        page = context.pages[0] if context.pages else await context.new_page()
                        session["page"] = page
                        # the live view (frames + clicks/typing) uses the same machinery as job-site sign-in
                        connections._remote[user_id] = {"site": "assist", "page": page, "state": "open",
                                                        "done": session["done"], "active": time.monotonic()}
                        _say(session, f"Opening {url}")
                        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                        if job.source == "link" and job.title.startswith("Application at"):
                            title = (await page.title() or "").strip()[:200]  # name the pasted link after its page
                            if title:
                                job.title = title
                                db.add(job)
                                db.commit()
                        goal = (f"Apply for the job \"{job.title}\" at {job.company}: find the application form on this "
                                f"page, fill every field from the candidate's data, upload the résumé, and submit.")
                        if session["review"]:
                            goal = (f"Fill the application for \"{job.title}\" at {job.company} on this page: every field "
                                    f"from the candidate's data, upload the résumé. Do NOT click the final Submit / Apply "
                                    f"button: when everything is filled, stop with status \"need_human\" and reason "
                                    f"\"Everything is filled — review it and click Submit\".")
                        while not session["done"].is_set():
                            session["state"] = "agent"
                            _say(session, "AI is reading the page and filling the form…")

                            def on_step(step: int, st: str, reason: str) -> None:
                                _say(session, f"Step {step + 1}: {reason or st}")

                            result, note = await agent_apply(context.pages[-1], job, profile, settings, goal,
                                                             app.cover_letter or "", cover_path, email, on_step=on_step)
                            page = context.pages[-1]
                            session["page"] = connections._remote[user_id]["page"] = page
                            _say(session, note)
                            if result == "applied":
                                app.status, app.notes = "applied", note
                                app.sent_to, app.applied_at = page.url, datetime.utcnow()
                                app.method = app.method if app.method not in (None, "pending") else "site"
                                db.add(app)
                                db.commit()
                                session["state"] = "applied"
                                await asyncio.sleep(4)  # let the user see the confirmation page
                                break
                            session["state"] = "waiting_you"
                            _say(session, "Your turn: finish what's needed in the window, then press "
                                          "“Continue with AI” — or “Mark as applied” if you submitted it yourself.")
                            session["resume"].clear()
                            deadline = time.monotonic() + _IDLE_S
                            while not session["resume"].is_set() and time.monotonic() < deadline:
                                connections._remote[user_id]["page"] = context.pages[-1]  # follow pop-ups
                                try:
                                    await asyncio.wait_for(session["resume"].wait(), timeout=2)
                                except asyncio.TimeoutError:
                                    pass
                            if not session["resume"].is_set():
                                break
                    finally:
                        await context.close()
        except Exception as exc:
            logger.exception("assist: user_id=%s app_id=%s", user_id, app_id)
            _say(session, f"Browser error: {str(exc)[:160]}")
            session["state"] = "error"
        finally:
            remote = connections._remote.get(user_id)
            if remote and remote.get("site") == "assist":
                remote["state"], remote["page"] = "closed", None
    if session["state"] not in ("applied", "error"):
        session["state"] = "closed"
    session["page"] = None
