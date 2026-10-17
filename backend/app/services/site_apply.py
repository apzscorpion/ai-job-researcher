"""
Company-site applications: find the application page behind a job, fill it and submit.

detect_ats(url) -> "greenhouse" | "lever" | "ashby" | "workable" | None
form_url(url, ats) -> the page that holds the application form
resolve_apply_url(job) -> application link ("mailto:..." when the posting says to email)
apply_company_site(job, profile, settings, cover_letter, email, submit=True) -> (status, note, url)
    status: applied | needs_manual | failed | email (the page only gives an address; url is it)
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx

from app.config import DATA_DIR
from app.models import Job, Profile, Settings
from app.services.browser_apply import fill_form, random_sleep

logger = logging.getLogger(__name__)

ATS_HOSTS = {
    "greenhouse": ("greenhouse.io",),
    "lever": ("jobs.lever.co",),
    "ashby": ("jobs.ashbyhq.com",),
    "workable": ("apply.workable.com",),
}
# Need an account or are built to stop automation: always handed to the user
ACCOUNT_SITES = ("myworkdayjobs.com", "workday.com", "taleo.net", "icims.com", "successfactors.",
                 "oraclecloud.com", "brassring.com", "jobvite.com")
_CONFIRM = re.compile(
    r"thank(s| you),? for (applying|your (application|interest|submission))|application (has been |was )?"
    r"(received|submitted|sent)|we('ve| have) received your application|successfully (applied|submitted)",
    re.I)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")


def detect_ats(url: str) -> Optional[str]:
    host = urlparse(url or "").netloc.lower()
    return next((ats for ats, hosts in ATS_HOSTS.items() if any(h in host for h in hosts)), None)


def form_url(url: str, ats: Optional[str]) -> str:
    """Lever, Ashby and Workable keep the form on a sub-page of the job."""
    base = url.split("?")[0].rstrip("/")
    if ats == "lever" and not base.endswith("/apply"):
        return base + "/apply"
    if ats == "ashby" and not base.endswith("/application"):
        return base + "/application"
    if ats == "workable" and not base.endswith("/apply"):
        return base + "/apply/"
    return url


async def resolve_apply_url(job: Job) -> str:
    """Application link: the one stored at search time, else the best link on the job page."""
    if job.apply_url:
        return job.apply_url
    if job.source in ("linkedin", "naukri", "remoteok") or not job.url.startswith("http"):
        return ""  # those pages need a login to reveal the employer's link; it comes from the search results
    try:
        from app.security import block_private_hook

        # scraped link: every hop (redirects too) must stay on the public internet
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers={"User-Agent": _UA},
                                     event_hooks={"request": [block_private_hook]}) as client:
            resp = await client.get(job.url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.info("resolve_apply_url: %s failed: %s", job.url, exc)
        return ""
    from bs4 import BeautifulSoup

    return best_link([(a.get_text(" ", strip=True).lower(), urljoin(str(resp.url), a["href"]))
                      for a in BeautifulSoup(resp.text, "html.parser").select("a[href]")])


def best_link(links) -> str:
    """Pick the application link from (lowercased text, absolute href) pairs: ATS or mailto first."""
    ats_link = next((href for _, href in links if detect_ats(href) or href.startswith("mailto:")), "")
    if ats_link:
        return ats_link
    # "Apply for this position" beats promo links that merely mention applying
    candidates = [(not text.startswith("apply"), href) for text, href in links
                  if "apply" in text and "applied" not in text and not href.endswith("#")
                  and href.startswith(("http", "mailto:"))]
    return min(candidates)[1] if candidates else ""


async def apply_company_site(
    job: Job,
    profile: Profile,
    settings: Settings,
    cover_letter: str,
    email: str,
    submit: bool = True,
) -> Tuple[str, str, str]:
    """Visit the company's application page, fill it and submit. Returns (status, note, url)."""
    from app.services.connections import site_browser

    url = await resolve_apply_url(job)
    async with site_browser(job.user_id) as context:  # the user's job-site logins (Settings → Job sites)
        page = await context.new_page()
        if not url and job.source not in ("linkedin", "naukri") and job.url.startswith("http"):
            # Some boards (RemoteOK, Indeed…) show the employer's link only to signed-in visitors
            try:
                await page.goto(job.url, wait_until="domcontentloaded", timeout=40_000)
                await random_sleep(2.0, 4.0)
                url = best_link(await page.eval_on_selector_all(
                    "a[href]", "els => els.map(a => [(a.innerText || '').trim().toLowerCase(), a.href])"))
            except Exception:
                logger.info("apply_company_site: couldn't open %s", job.url)
        if url.startswith("mailto:"):
            return "email", "The posting asks for applications by email.", url[7:].split("?")[0]
        if not url:
            return "needs_manual", (f"Couldn't find {job.company}'s application link — if the job board needs you "
                                    f"signed in, connect it in Settings → Job sites. Apply from the posting: "
                                    f"{job.url}"), job.url
        host = urlparse(url).netloc.lower()
        if any(s in host for s in ACCOUNT_SITES):
            return "needs_manual", (f"{job.company} uses {host}, which needs an account there. "
                                    f"Apply here with the cover letter below: {url}"), url
        return await _fill_and_submit(page, job, profile, settings, cover_letter, email, submit, url)


async def _fill_and_submit(page, job: Job, profile: Profile, settings: Settings, cover_letter: str,
                           email: str, submit: bool, url: str) -> Tuple[str, str, str]:
    ats = detect_ats(url)
    host = urlparse(url).netloc.lower()
    cover_path = DATA_DIR / "tmp" / f"cover_letter_{job.id}.txt"
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    cover_path.write_text(cover_letter or "", encoding="utf-8")
    extra = {"email": email, "resume_path": profile.resume_path, "cover_letter": cover_letter,
             "cover_letter_path": str(cover_path) if cover_letter else ""}
    where = f"{job.company}'s {ats.title() if ats else host} page"

    try:
        await page.goto(form_url(url, ats), wait_until="domcontentloaded", timeout=40_000)
        await random_sleep(2.0, 4.0)

        # Company careers pages often embed the ATS form in an iframe: open it directly
        embedded = page.locator("iframe[src*='greenhouse'], iframe[src*='lever.co'], "
                                "iframe[src*='ashbyhq'], iframe[src*='workable']")
        if await embedded.count() > 0:
            src = await embedded.first.get_attribute("src") or ""
            await page.goto(urljoin(page.url, src), wait_until="domcontentloaded", timeout=40_000)
            await random_sleep(1.5, 3.0)

        scope = "form:has(input[type=file]), form:has(input[type=email])"
        if await page.locator(scope).count() == 0:
            opener = page.locator("a:visible:has-text('Apply'), button:visible:has-text('Apply')").first
            if await opener.count() > 0:
                try:
                    await opener.click(timeout=10_000)
                    await random_sleep(2.0, 4.0)
                except Exception:
                    logger.info("apply_company_site: Apply opener not clickable on %s", page.url)
        if await page.locator(scope).count() == 0:
            return "needs_manual", (f"No application form we can fill on {where}. "
                                    f"Apply here with the cover letter below: {page.url}"), page.url
        # Pin the form: uploads remove its file inputs, so the selector above stops matching
        await page.locator(scope).first.evaluate("f => f.setAttribute('data-af-form', '1')")
        scope = "form[data-af-form]"

        unknown = await fill_form(page, scope, profile, settings, extra)
        if unknown:
            return "needs_manual", (f"{where} asks questions your résumé and standard answers don't cover: "
                                    + "; ".join(f"“{q}”" for q in unknown[:6])
                                    + f". Add them under Settings → Standard answers, or finish here: {page.url}"), page.url
        if not submit:
            return "needs_manual", f"Dry run: every field on {where} was filled; not submitted.", page.url

        form_page = page.url
        await random_sleep(1.0, 2.5)
        await page.locator(scope).first.locator(
            "button[type=submit], input[type=submit], button:has-text('Submit')").last.click()
        await random_sleep(5.0, 8.0)

        body = await page.locator("body").inner_text()
        if _CONFIRM.search(body) or any(w in page.url.lower() for w in ("confirmation", "thank", "success")):
            cl = " and cover letter" if cover_letter else ""
            return "applied", f"Applied on {where}: filled your details and uploaded your résumé{cl}.", form_page
        if await page.locator("iframe[src*='recaptcha/api2/bframe'], iframe[src*='hcaptcha']").count() > 0:
            return "needs_manual", f"{where} asked for a captcha. Finish it here: {form_page}", form_page
        return "needs_manual", (f"Submitted on {where}, but the site didn't confirm it. Check your inbox for a "
                                f"confirmation; if none arrives, apply here: {form_page}"), form_page
    except Exception:
        logger.exception("apply_company_site: error for job_id=%s url=%s", job.id, url)
        return "failed", f"Browser error on {where}. Apply here with the cover letter below: {url}", url
