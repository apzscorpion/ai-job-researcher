"""
Job-site connections: the browser profiles the user signs into once, then reused for applying.

LinkedIn and Naukri keep their own profiles (their apply flows use them); every other job site
shares the "sites" profile, which company-site applications also use so logins carry over.

SITES                       — the sites shown in Settings → Job site connections
connect(user_id, site)      — opens a visible browser on the login page; returns True once signed in
check_all(user_id)          — {site: "signed_in" | "signed_out" | "saved" | "not_connected" | "busy"}
site_browser(user_id)       — async context manager: headless context on the shared "sites" profile
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from app.config import BROWSERS_DIR

logger = logging.getLogger(__name__)

# cookie: set only while signed in (None when we can't tell, then the user's own check counts)
SITES: Dict[str, Dict[str, Optional[str]]] = {
    "linkedin":  {"name": "LinkedIn",  "url": "https://www.linkedin.com/login",       "domain": "linkedin.com",  "cookie": "li_at",   "profile": "linkedin"},
    "naukri":    {"name": "Naukri",    "url": "https://www.naukri.com/nlogin/login",  "domain": "naukri.com",    "cookie": "nauk_rt", "profile": "naukri"},
    "indeed":    {"name": "Indeed",    "url": "https://secure.indeed.com/account/login", "domain": "indeed.com", "cookie": None,      "profile": "sites"},
    "glassdoor": {"name": "Glassdoor", "url": "https://www.glassdoor.co.in/index.htm", "domain": "glassdoor",    "cookie": None,      "profile": "sites"},
    "wellfound": {"name": "Wellfound (AngelList)", "url": "https://wellfound.com/login", "domain": "wellfound.com", "cookie": None, "profile": "sites"},
    "remoteok":  {"name": "RemoteOK",  "url": "https://remoteok.com/",                "domain": "remoteok.com",  "cookie": None,      "profile": "sites"},
    "foundit":   {"name": "Foundit (Monster)", "url": "https://www.foundit.in/",      "domain": "foundit.in",    "cookie": None,      "profile": "sites"},
    "instahyre": {"name": "Instahyre", "url": "https://www.instahyre.com/login/",     "domain": "instahyre.com", "cookie": None,      "profile": "sites"},
    "cutshort":  {"name": "Cutshort",  "url": "https://cutshort.io/",                 "domain": "cutshort.io",   "cookie": None,      "profile": "sites"},
    "hirist":    {"name": "Hirist",    "url": "https://www.hirist.tech/",             "domain": "hirist",        "cookie": None,      "profile": "sites"},
}
_LOGIN_WAIT_S = 15 * 60

# A browser profile can only be open once: (user_id, profile) -> lock
_locks: Dict[tuple, asyncio.Lock] = {}


def _lock(user_id: int, profile: str) -> asyncio.Lock:
    return _locks.setdefault((user_id, profile), asyncio.Lock())


def busy(user_id: int, site: str) -> bool:
    return _lock(user_id, SITES[site]["profile"]).locked()


async def _open(p, user_id: int, profile: str, headless: bool = False, background: bool = False):
    """The user's saved profile in real Google Chrome (bundled Chromium if Chrome isn't installed).

    Applying runs in a normal, *visible* window moved off-screen (background=True): LinkedIn and Naukri
    detect headless browsers and end the session ("asks to log in again"). The browser's own user agent is
    kept so the session matches the one the user signed in with. headless=True only reads cookies.
    """
    path = BROWSERS_DIR / str(user_id) / profile
    path.mkdir(parents=True, exist_ok=True)
    args = ["--disable-blink-features=AutomationControlled"]
    if background:
        args += ["--window-position=-32000,-32000", "--window-size=1366,900"]
    kw = dict(user_data_dir=str(path), headless=headless, args=args, no_viewport=True,
              ignore_default_args=["--enable-automation"])
    try:
        return await p.chromium.launch_persistent_context(channel="chrome", **kw)
    except Exception:
        logger.info("connections: Google Chrome not available, using bundled Chromium")
        return await p.chromium.launch_persistent_context(**kw)


def _state(cookies, site: Dict[str, Optional[str]]) -> str:
    """Signed-in state from the profile's cookies (expired cookies are already dropped by Chromium)."""
    mine = [c for c in cookies if site["domain"] in c.get("domain", "")]
    if site["cookie"]:
        return "signed_in" if any(c["name"] == site["cookie"] for c in mine) else (
            "signed_out" if mine else "not_connected")
    return "saved" if mine else "not_connected"


async def connect(user_id: int, site: str) -> bool:
    """Visible browser on the site's login page; waits until signed in or the window is closed."""
    from playwright.async_api import async_playwright  # type: ignore

    info = SITES[site]
    async with _lock(user_id, info["profile"]):
        async with async_playwright() as p:
            context = await _open(p, user_id, info["profile"], headless=False)
            closed = asyncio.Event()
            context.on("close", lambda *_: closed.set())
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(info["url"], wait_until="domcontentloaded")
                deadline = time.monotonic() + _LOGIN_WAIT_S
                while not closed.is_set() and time.monotonic() < deadline:
                    if info["cookie"] and _state(await context.cookies(), info) == "signed_in":
                        await asyncio.sleep(3)  # let the site finish setting its cookies
                        break
                    try:
                        await asyncio.wait_for(closed.wait(), timeout=3)
                    except asyncio.TimeoutError:
                        pass
                return info["cookie"] is None or (
                    not closed.is_set() and _state(await context.cookies(), info) == "signed_in")
            except Exception:
                logger.exception("connections.connect: user_id=%s site=%s", user_id, site)
                return False
            finally:
                if not closed.is_set():
                    await context.close()


async def check_all(user_id: int) -> Dict[str, str]:
    """Signed-in state of every site, reading each saved profile's cookies (headless, nothing visited)."""
    from playwright.async_api import async_playwright  # type: ignore

    result: Dict[str, str] = {}
    profiles = {info["profile"] for info in SITES.values()}
    async with async_playwright() as p:
        for profile in profiles:
            sites = {k: v for k, v in SITES.items() if v["profile"] == profile}
            if not (BROWSERS_DIR / str(user_id) / profile).is_dir():
                result.update({k: "not_connected" for k in sites})
                continue
            lock = _lock(user_id, profile)
            if lock.locked():
                result.update({k: "busy" for k in sites})
                continue
            async with lock:
                context = await _open(p, user_id, profile, headless=True)
                try:
                    cookies = await context.cookies()
                finally:
                    await context.close()
            result.update({k: _state(cookies, v) for k, v in sites.items()})
    return result


# --- Remote sign-in: the browser runs here, the user sees and drives it from the website ----------------
_REMOTE_IDLE_S = 10 * 60
_remote: Dict[int, Dict[str, Any]] = {}   # user_id -> {site, page, done, state, active}
_KEYS = {"Enter", "Tab", "Backspace", "Delete", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"}


def remote_status(user_id: int) -> Dict[str, Any]:
    s = _remote.get(user_id)
    if not s:
        return {"state": "none"}
    page = s.get("page")
    return {"state": s["state"], "site": s["site"], "name": SITES[s["site"]]["name"],
            "url": page.url if page and s["state"] == "open" else ""}


def start_remote(user_id: int, site: str) -> None:
    """Open *site*'s login in a browser on this machine for *user_id* (their own profile) and stream it."""
    if _remote.get(user_id, {}).get("state") == "open":
        return
    done = asyncio.Event()
    _remote[user_id] = {"site": site, "page": None, "done": done, "state": "starting", "active": time.monotonic()}
    task = asyncio.create_task(_remote_run(user_id, site, done))
    _remote[user_id]["task"] = task


async def _remote_run(user_id: int, site: str, done: asyncio.Event) -> None:
    from playwright.async_api import async_playwright  # type: ignore

    info = SITES[site]
    session = _remote[user_id]
    try:
        async with _lock(user_id, info["profile"]):
            async with async_playwright() as p:
                context = await _open(p, user_id, info["profile"], background=True)
                try:
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.goto(info["url"], wait_until="domcontentloaded")
                    session.update(page=page, state="open")
                    while not done.is_set() and time.monotonic() - session["active"] < _REMOTE_IDLE_S:
                        session["page"] = context.pages[-1] if context.pages else page  # follow pop-ups
                        if info["cookie"] and _state(await context.cookies(), info) == "signed_in":
                            await asyncio.sleep(3)
                            session["state"] = "signed_in"
                            break
                        try:
                            await asyncio.wait_for(done.wait(), timeout=2)
                        except asyncio.TimeoutError:
                            pass
                finally:
                    await context.close()
    except Exception:
        logger.exception("connections.remote: user_id=%s site=%s", user_id, site)
        session["state"] = "error"
    if session["state"] in ("open", "starting"):
        session["state"] = "closed"
    session["page"] = None


async def remote_frame(user_id: int) -> Optional[bytes]:
    s = _remote.get(user_id)
    if not s or s["state"] != "open" or not s.get("page"):
        return None
    s["active"] = time.monotonic()
    try:
        return await s["page"].screenshot(type="jpeg", quality=60, timeout=10_000)
    except Exception:
        return None


async def remote_input(user_id: int, kind: str, x: float = 0, y: float = 0, text: str = "", key: str = "",
                       dy: float = 0) -> bool:
    s = _remote.get(user_id)
    if not s or s["state"] != "open" or not s.get("page"):
        return False
    s["active"] = time.monotonic()
    page = s["page"]
    if kind == "click":
        await page.mouse.click(x, y)
    elif kind == "type":
        await page.keyboard.type(text[:500], delay=25)
    elif kind == "key" and key in _KEYS:
        await page.keyboard.press(key)
    elif kind == "scroll":
        await page.mouse.wheel(0, max(-2000, min(2000, dy)))
    else:
        return False
    return True


def remote_close(user_id: int) -> None:
    s = _remote.get(user_id)
    if s:
        s["done"].set()


_public_hosts: Dict[str, bool] = {}


async def _block_private(route) -> None:
    """Company pages come from scraped links: never let them steer the browser into the server's network."""
    from urllib.parse import urlparse

    from app.security import check_public_host

    url = urlparse(route.request.url)
    if url.scheme in ("data", "blob", "about"):
        return await route.continue_()
    host = url.hostname or ""
    if host not in _public_hosts:
        try:
            await asyncio.to_thread(check_public_host, host, url.port)
            _public_hosts[host] = True
        except ValueError:
            _public_hosts[host] = False
    if _public_hosts[host]:
        await route.continue_()
    else:
        logger.warning("site_browser: blocked request to %s", host)
        await route.abort("blockedbyclient")


@asynccontextmanager
async def fresh_browser():
    """A clean Chrome window (no saved logins) for company application pages; many can run at once."""
    from playwright.async_api import async_playwright  # type: ignore

    args = ["--disable-blink-features=AutomationControlled", "--window-position=-32000,-32000",
            "--window-size=1366,900"]
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="chrome", headless=False, args=args,
                                              ignore_default_args=["--enable-automation"])
        except Exception:
            browser = await p.chromium.launch(headless=False, args=args, ignore_default_args=["--enable-automation"])
        context = await browser.new_context(no_viewport=True)
        await context.route("**/*", _block_private)
        try:
            yield context
        finally:
            await browser.close()


@asynccontextmanager
async def site_browser(user_id: int):
    """Headless browser on the shared "sites" profile, so the user's job-site logins apply."""
    from playwright.async_api import async_playwright  # type: ignore

    async with _lock(user_id, "sites"):
        async with async_playwright() as p:
            context = await _open(p, user_id, "sites", background=True)
            await context.route("**/*", _block_private)
            try:
                yield context
            finally:
                await context.close()
