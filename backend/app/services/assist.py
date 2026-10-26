"""
Assisted applying: the AI agent fills an application page in a browser on this machine while the user
watches it live in the website (same live view as job-site sign-in) and can click/type at any time —
captchas, logins, answers the AI can't know. When the site confirms, the application becomes "applied".

start(user_id, app_id)   — open the job's application page in the right signed-in profile, run the agent
status(user_id)          — {state, app_id, url, log}
resume(user_id)          — after the user took over: let the agent continue
close(user_id)
reply(user_id, text) / add_file(user_id, name, path) — the chat: answer the AI's question or attach a file
States: starting → agent (AI is filling) → asking (the AI asked a question in the chat) / waiting_you (captcha,
login…: act in the window, then reply or press Continue) → applied | closed | error
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
    return {"state": s["state"], "app_id": s["app_id"], "log": s["log"][-30:], "chat": s["chat"][-80:],
            "question": s.get("question"), "title": s.get("title", ""),
            "url": (connections.current_page(s) or page).url if page and s["state"] not in ("closed", "error") else ""}


LIVE = ("starting", "agent", "asking", "waiting_you", "pausing", "paused")


def busy(user_id: int) -> bool:
    return _sessions.get(user_id, {}).get("state") in LIVE


def start(user_id: int, app_id: int, review: bool = False) -> None:
    session: Dict[str, Any] = {"app_id": app_id, "state": "starting", "log": [], "page": None, "review": review,
                               "chat": [], "answers": [], "files": {}, "question": None, "paused": False,
                               "unpaused": asyncio.Event(),
                               "done": asyncio.Event(), "resume": asyncio.Event(), "active": time.monotonic()}
    _sessions[user_id] = session
    session["task"] = asyncio.create_task(_run(user_id, app_id, session))


def resume(user_id: int) -> bool:
    s = _sessions.get(user_id)
    if not s or s["state"] not in ("waiting_you", "asking"):
        return False
    s["resume"].set()
    return True


def pause(user_id: int) -> bool:
    """Stop the AI before its next action; the live browser stays usable so the user can fix things."""
    s = _sessions.get(user_id)
    if not s or s["state"] not in ("starting", "agent"):
        return False
    s["paused"] = True
    s["unpaused"].clear()
    s["state"] = "pausing"  # until the AI's current step comes back (an AI answer can take ~30 s)
    _say(s, "⏸ Pausing — the AI stops after its current step. Fix anything in the window, then press Resume.")
    return True


def unpause(user_id: int) -> bool:
    s = _sessions.get(user_id)
    if not s or not s.get("paused"):
        return False
    s["paused"] = False
    s["unpaused"].set()
    if s["state"] == "pausing":  # resumed before the AI's current step even finished
        s["state"] = "agent"
    return True


async def _gate(session: Dict[str, Any]) -> bool:
    """Called by the agent between actions: blocks while paused. True if it did pause (re-read the page)."""
    if not session.get("paused"):
        return False
    session["state"] = "paused"
    while session.get("paused") and not session["done"].is_set():
        try:
            await asyncio.wait_for(session["unpaused"].wait(), timeout=2)
        except asyncio.TimeoutError:
            pass
    session["state"] = "agent"
    _say(session, "▶ Resumed — the AI is re-reading the page, including your changes.")
    return True


def reply(user_id: int, text: str) -> bool:
    """The user's chat message: the answer to the AI's question (or a note); the AI carries on with it."""
    s = _sessions.get(user_id)
    if not s or s["state"] not in LIVE:
        return False
    _chat(s, "you", text)
    q = s.get("question")
    s["answers"].append(f'Asked "{q}" — the candidate answered: {text}' if q else f"The candidate says: {text}")
    s["question"] = None
    s["resume"].set()  # a waiting AI continues; a working one reads it on its next step
    return True


def add_file(user_id: int, name: str, path: str) -> bool:
    s = _sessions.get(user_id)
    if not s or s["state"] not in LIVE:
        return False
    key = f"upload_{len(s['files']) + 1}"
    s["files"][key] = path
    _chat(s, "you", f"📎 {name}", file=name)
    q = s.get("question")
    s["answers"].append(f'The candidate attached the file "{name}" (upload it with file key "{key}")'
                        + (f' for: "{q}"' if q else ""))
    s["question"] = None
    s["resume"].set()
    return True


def close(user_id: int) -> None:
    """Stop right now — including an AI that is mid-form (it must never go on to submit after Close)."""
    s = _sessions.get(user_id)
    if s:
        s["done"].set()
        s["resume"].set()
        s["paused"] = False
        s.get("unpaused", asyncio.Event()).set()
        task = s.get("task")
        if task and not task.done():
            task.cancel()  # the browser closes in _run's finally blocks
        if s["state"] in LIVE:
            s["state"] = "closed"
            _say(s, "Closed by you — nothing more will be done on this page.")


def _chat(session: Dict[str, Any], who: str, text: str, kind: str = "info", file: str = "") -> None:
    session["chat"].append({"time": datetime.now().strftime("%H:%M:%S"), "from": who, "text": text,
                            "kind": kind, "file": file})
    session["active"] = time.monotonic()


def _say(session: Dict[str, Any], text: str, kind: str = "info") -> None:
    session["log"].append({"time": datetime.now().strftime("%H:%M:%S"), "text": text})
    _chat(session, "ai", text, kind)


async def _wait_for_user(session: Dict[str, Any]) -> bool:
    """Until the user replies / presses Continue (True) or goes idle or closes (False)."""
    session["resume"].clear()
    deadline = time.monotonic() + _IDLE_S
    while not session["resume"].is_set() and time.monotonic() < deadline:
        try:
            await asyncio.wait_for(session["resume"].wait(), timeout=2)
        except asyncio.TimeoutError:
            pass
    return session["resume"].is_set() and not session["done"].is_set()


async def _quick_fill(session: Dict[str, Any], page, profile, settings, extra: Dict[str, Any]) -> None:
    """The fast, rule-based filler first (Greenhouse/Lever/Ashby-style forms, dropdowns included): seconds
    instead of an AI round trip per field. Whatever it can't answer is asked in the chat right away."""
    from app.services.browser_apply import fill_form

    scope = "form:has(input[type=file]), form:has(input[type=email])"
    try:
        if await page.locator(scope).count() == 0:
            return  # no form yet (an "Apply" button first, a login…): the AI agent handles the page
        await page.locator(scope).first.evaluate("f => f.setAttribute('data-af-form', '1')")
        session["state"] = "agent"
        _say(session, "Filling the standard fields and dropdowns…")
        start = time.monotonic()
        unknown = await fill_form(page, "form[data-af-form]", profile, settings, extra)
        _say(session, f"Standard fields done in {int(time.monotonic() - start)} s.")
    except Exception as exc:
        logger.info("assist: quick fill skipped: %s", exc)
        return
    if unknown:
        session["question"] = ("I need your answers for: " + "; ".join(f"“{q}”" for q in unknown[:8])
                               + ". Reply with them (one per line), or attach a file if one is a document.")
        session["state"] = "asking"
        _say(session, session["question"], kind="question")
        await _wait_for_user(session)


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
        session["title"] = f"{job.title} — {job.company}"
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
                        connections._remote[user_id] = {"site": "assist", "page": page, "context": context,
                                                        "state": "open", "done": session["done"],
                                                        "active": time.monotonic()}
                        session["context"] = context
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
                        quick_extra = {"email": email, "resume_path": profile.resume_path,
                                       "job_location": job.location or "",
                                       "cover_letter": app.cover_letter or "", "cover_letter_path": cover_path}
                        quick_done: set = set()

                        async def on_page(pg) -> None:
                            if pg.url.split("?")[0] not in quick_done:
                                quick_done.add(pg.url.split("?")[0])
                                await _quick_fill(session, pg, profile, settings, quick_extra)
                                session["state"] = "agent"
                        while not session["done"].is_set():
                            session["state"] = "agent"
                            _say(session, "AI is reading the page and filling the form…")

                            def on_step(step: int, st: str, reason: str, url: str = "") -> None:
                                from urllib.parse import urlparse

                                where = urlparse(url).netloc.removeprefix("www.")
                                _say(session, f"Step {step + 1}{' on ' + where if where else ''}: {reason or st}")

                            result, note = await agent_apply(context.pages[-1], job, profile, settings, goal,
                                                             app.cover_letter or "", cover_path, email, on_step=on_step,
                                                             answers=session["answers"], extra_files=session["files"],
                                                             allow_ask=True, on_page=on_page,
                                                             gate=lambda: _gate(session))
                            page = context.pages[-1]
                            session["page"] = connections._remote[user_id]["page"] = page
                            session["paused"] = False  # the run ended on its own: a late Pause mustn't linger
                            session["unpaused"].set()
                            if result == "ask":
                                session["question"], session["state"] = note, "asking"
                                _say(session, note, kind="question")
                            else:
                                _say(session, note, kind="done" if result == "applied" else "need")
                            if result == "applied":
                                app.status, app.notes = "applied", note
                                app.sent_to, app.applied_at = page.url, datetime.utcnow()
                                app.method = app.method if app.method not in (None, "pending") else "site"
                                db.add(app)
                                db.commit()
                                session["state"] = "applied"
                                await asyncio.sleep(4)  # let the user see the confirmation page
                                break
                            from app.services.agent_apply import CLOSED_NOTE

                            if note.startswith(CLOSED_NOTE):  # nothing for the user to do in the window either
                                app.notes = note
                                db.add(app)
                                db.commit()
                                _say(session, "Stopped: this job doesn't take applications any more. Cancel it in "
                                              "Applications.", kind="need")
                                break
                            if result != "ask":
                                session["state"] = "waiting_you"
                                _say(session, "Your turn: do it in the window on the left, then reply here (or press "
                                              "“Continue with AI”). If you submitted it yourself, press “Mark as applied”.",
                                     kind="need")
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
