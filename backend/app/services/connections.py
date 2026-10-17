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
from typing import Dict, Optional

from app.config import BROWSERS_DIR

logger = logging.getLogger(__name__)

# cookie: set only while signed in (None when we can't tell, then the user's own check counts)
SITES: Dict[str, Dict[str, Optional[str]]] = {
    "linkedin":  {"name": "LinkedIn",  "url": "https://www.linkedin.com/login",       "domain": "linkedin.com",  "cookie": "li_at",   "profile": "linkedin"},
    "naukri":    {"name": "Naukri",    "url": "https://www.naukri.com/nlogin/login",  "domain": "naukri.com",    "cookie": "nauk_at", "profile": "naukri"},
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
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")

# A browser profile can only be open once: (user_id, profile) -> lock
_locks: Dict[tuple, asyncio.Lock] = {}


def _lock(user_id: int, profile: str) -> asyncio.Lock:
    return _locks.setdefault((user_id, profile), asyncio.Lock())


def busy(user_id: int, site: str) -> bool:
    return _lock(user_id, SITES[site]["profile"]).locked()


async def _open(p, user_id: int, profile: str, headless: bool):
    path = BROWSERS_DIR / str(user_id) / profile
    path.mkdir(parents=True, exist_ok=True)
    return await p.chromium.launch_persistent_context(
        user_data_dir=str(path), headless=headless, user_agent=_UA,
        args=["--disable-blink-features=AutomationControlled"], ignore_default_args=["--enable-automation"])


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
async def site_browser(user_id: int):
    """Headless browser on the shared "sites" profile, so the user's job-site logins apply."""
    from playwright.async_api import async_playwright  # type: ignore

    async with _lock(user_id, "sites"):
        async with async_playwright() as p:
            context = await _open(p, user_id, "sites", headless=True)
            await context.route("**/*", _block_private)
            try:
                yield context
            finally:
                await context.close()
