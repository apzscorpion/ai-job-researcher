"""
Job sources aggregation service.

run_search(user_id, session) — fetches jobs from multiple sources, deduplicates
by URL, persists new rows, then triggers matcher.score_jobs.

Remote-first: when the user allows remote work, worldwide remote boards
(Remotive, RemoteOK) and LinkedIn's worldwide remote search run first.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from typing import Any, Dict, Iterable, List, Optional, Set

import httpx
from sqlmodel import Session, select

from app.config import EMBED_MODEL, OLLAMA_MODEL, OLLAMA_URL
from app.models import ActivityLog, Job, Profile, Settings, json_decode

logger = logging.getLogger(__name__)

_EMAIL_REGEX = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.]+")
_HTTP_TIMEOUT = httpx.Timeout(20.0)
_HEADERS = {"User-Agent": "ai-job-researcher/1.0 (personal job search)"}

_KERALA_HINTS = ("kerala", "kochi", "cochin", "trivandrum", "thiruvananthapuram", "kozhikode", "calicut")
_GENERIC_TITLE_WORDS = {
    "senior", "sr", "junior", "jr", "lead", "principal", "staff", "head", "associate", "trainee",
    "intern", "developer", "developers", "engineer", "engineers", "software", "and", "the", "of",
    "for", "ii", "iii", "i", "team", "manager", "specialist", "executive",
}
# Résumé skills too generic to prove a title is relevant
_NON_ROLE_SKILLS = {"agile", "scrum", "git", "github", "gitlab", "ci/cd", "rest api", "linux", "html", "html5", "css", "css3"}

# One search per user at a time (manual "Search Now" + scheduler share this)
_active_lock = threading.Lock()
_active_users: Set[int] = set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm_title(text: str) -> str:
    t = text.lower()
    t = re.sub(r"\b(front|back)[\s-]+end\b", r"\1end", t)
    return re.sub(r"\bfull[\s-]+stack\b", "fullstack", t)


def title_relevant(title: str, targets: Iterable[str], skills: Iterable[str]) -> bool:
    """True when the job title shares a role keyword or a résumé skill with the user."""
    t = _norm_title(title)
    terms = {w for target in targets for w in re.findall(r"[a-z][a-z+#.]*", _norm_title(target))}
    terms -= _GENERIC_TITLE_WORDS
    terms |= {s.lower() for s in skills if len(s) > 1} - _NON_ROLE_SKILLS
    return any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", t) for w in terms)


def _html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup

    return BeautifulSoup(html or "", "html.parser").get_text("\n", strip=True)


# Description containers on job pages (LinkedIn guest view, common ATS pages)
_JD_SELECTORS = (".show-more-less-html__markup", ".description__text", "[class*=job-description]",
                 "[class*=jobDescription]", "[class*=description]", "article", "main")


async def fetch_description(url: str) -> str:
    """Fetch a posting page and return its job-description text ("" on failure).

    shortcut: static HTML only; JS-rendered pages (e.g. Naukri) return "", use Playwright if needed.
    """
    if not url.startswith(("http://", "https://")):
        return ""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0"}) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.info("fetch_description: %s failed: %s", url, exc)
        return ""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    for sel in _JD_SELECTORS:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 300:
            return el.get_text("\n", strip=True)
    return ""


def _apply_link(html: str) -> str:
    """First employer application link (ATS page or mailto:) inside a description's HTML."""
    from bs4 import BeautifulSoup

    from app.services.site_apply import detect_ats

    for a in BeautifulSoup(html or "", "html.parser").select("a[href]"):
        href = a["href"].strip()
        if href.startswith("mailto:") or detect_ats(href):
            return href
    return ""


def _cell(row, key: str) -> Any:
    """Read a pandas row value, mapping NaN/None to None."""
    import pandas as pd

    value = row.get(key)
    return None if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)) else value


def _job(title: str, company: str, url: str, source: str, **extra: Any) -> Dict[str, Any]:
    description = extra.get("description") or ""
    emails = _EMAIL_REGEX.findall(description)
    return {
        "title": title,
        "company": company or "Unknown",
        "url": url,
        "source": source,
        "location": extra.get("location"),
        "description": description,
        "salary_min": extra.get("salary_min"),
        "salary_max": extra.get("salary_max"),
        "company_rating": extra.get("company_rating"),
        "recruiter_email": emails[0] if emails else None,
        "apply_url": extra.get("apply_url") or None,
        "is_remote": bool(extra.get("is_remote")),
    }


# ---------------------------------------------------------------------------
# Individual source scrapers (blocking — call through asyncio.to_thread)
# ---------------------------------------------------------------------------

def _scrape_jobspy(
    title: str,
    location: str,
    sites: List[str],
    is_remote: bool = False,
    country_indeed: str = "India",
) -> List[Dict[str, Any]]:
    """Use python-jobspy to scrape LinkedIn, Indeed, Naukri, and Glassdoor."""
    jobs: List[Dict[str, Any]] = []
    try:
        import jobspy  # type: ignore

        results = jobspy.scrape_jobs(
            site_name=sites,
            search_term=title,
            location=location or None,
            is_remote=is_remote,
            results_wanted=40,
            hours_old=72,
            country_indeed=country_indeed,
            linkedin_fetch_description=True,  # otherwise LinkedIn jobs are scored on an empty description
            enforce_annual_salary=True,
        )

        for _, row in results.iterrows():
            job_url = str(_cell(row, "job_url") or "").strip()
            if not job_url:
                continue

            def _num(key: str) -> Optional[int]:
                try:
                    return int(float(_cell(row, key) or 0)) or None
                except (TypeError, ValueError):
                    return None

            try:
                rating = float(_cell(row, "company_rating") or 0) or None
            except (TypeError, ValueError):
                rating = None

            row_location = str(_cell(row, "location") or location or "")
            jobs.append(_job(
                str(_cell(row, "title") or title),
                str(_cell(row, "company") or ""),
                job_url,
                str(_cell(row, "site") or "jobspy"),
                location=row_location,
                description=str(_cell(row, "description") or ""),
                salary_min=_num("min_amount"),
                salary_max=_num("max_amount"),
                company_rating=rating,
                apply_url=str(_cell(row, "job_url_direct") or ""),
                is_remote=is_remote or bool(_cell(row, "is_remote")) or "remote" in row_location.lower(),
            ))
    except Exception:
        logger.exception("_scrape_jobspy failed for title=%s location=%s", title, location)
    return jobs


def _scrape_remotive(title: str) -> List[Dict[str, Any]]:
    """Worldwide remote jobs from Remotive's public API."""
    jobs: List[Dict[str, Any]] = []
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            resp = client.get("https://remotive.com/api/remote-jobs", params={"search": title, "limit": 50})
            resp.raise_for_status()
        for item in resp.json().get("jobs", []):
            jobs.append(_job(
                item.get("title", ""), item.get("company_name", ""), item.get("url", ""), "remotive",
                location=item.get("candidate_required_location") or "Worldwide",
                description=_html_to_text(item.get("description", "")),
                apply_url=_apply_link(item.get("description", "")),
                is_remote=True,
            ))
    except Exception:
        logger.warning("_scrape_remotive failed for %s", title, exc_info=True)
    return jobs


def _scrape_remoteok(tags: List[str]) -> List[Dict[str, Any]]:
    """Remote jobs from RemoteOK's public API, one request per skill tag."""
    jobs: List[Dict[str, Any]] = []
    with httpx.Client(timeout=_HTTP_TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
        for tag in tags:
            try:
                resp = client.get("https://remoteok.com/api", params={"tag": tag})
                resp.raise_for_status()
                for item in resp.json()[1:]:  # first element is a legal notice
                    def _num(key: str) -> Optional[int]:
                        try:
                            return int(item.get(key) or 0) or None
                        except (TypeError, ValueError):
                            return None

                    jobs.append(_job(
                        item.get("position", ""), item.get("company", ""), item.get("url", ""), "remoteok",
                        location=item.get("location") or "Worldwide",
                        description=_html_to_text(item.get("description", "")),
                        salary_min=_num("salary_min"),
                        salary_max=_num("salary_max"),
                        # item["apply_url"] is RemoteOK's own redirect, which needs a RemoteOK login
                        apply_url=_apply_link(item.get("description", "")),
                        is_remote=True,
                    ))
            except Exception:
                logger.warning("_scrape_remoteok failed for tag=%s", tag, exc_info=True)
    return jobs


def _scrape_infopark() -> List[Dict[str, Any]]:
    """Scrape job listings from infopark.in (table: posted, title, company, deadline, link)."""
    jobs: List[Dict[str, Any]] = []
    try:
        from bs4 import BeautifulSoup

        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get("https://infopark.in/companies/job-search")
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for row in soup.select("tr"):
            cells = row.find_all("td")
            link = row.select_one("a[href]")
            if len(cells) < 3 or not link:
                continue
            title = cells[1].get_text(strip=True)
            href = link["href"]
            url = href if href.startswith("http") else f"https://infopark.in{href}"
            jobs.append(_job(title, cells[2].get_text(strip=True), url, "infopark", location="Kochi, Kerala"))
    except Exception:
        logger.warning("_scrape_infopark failed", exc_info=True)
    return jobs


def _scrape_technopark() -> List[Dict[str, Any]]:
    """Scrape job listings from technopark.org."""
    jobs: List[Dict[str, Any]] = []
    try:
        from bs4 import BeautifulSoup

        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get("https://technopark.org/job-listing")
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for card in soup.select(".job-listing-item, .job-card, .views-row"):
            title_el = card.select_one("h2, h3, .job-title, .views-field-title")
            company_el = card.select_one(".company, .field-name-field-company")
            link_el = card.select_one("a[href]")
            title = title_el.get_text(strip=True) if title_el else ""
            href = link_el["href"] if link_el else ""
            if not title or not href:
                continue
            url = href if href.startswith("http") else f"https://technopark.org{href}"
            company = company_el.get_text(strip=True) if company_el else ""
            jobs.append(_job(title, company, url, "technopark", location="Thiruvananthapuram, Kerala"))
    except Exception:
        logger.warning("_scrape_technopark failed", exc_info=True)
    return jobs


async def _gemini_search_jobs(title: str, where: str, api_key: str) -> List[Dict[str, Any]]:
    """Use Gemini grounded search to discover additional job postings.

    shortcut: every URL found gets the search title and the same summary text;
    a per-URL fetch would be needed for real descriptions.
    """
    jobs: List[Dict[str, Any]] = []
    try:
        from app.services.llm import gemini_search

        result_text = await gemini_search(f"{title} jobs {where} careers page apply", api_key)
        for url in re.findall(r"https?://[^\s\"'>)\]]+", result_text)[:10]:
            jobs.append(_job(title, "", url, "gemini_search", location=where,
                             description=result_text[:500], is_remote="remote" in where.lower()))
    except Exception:
        logger.warning("_gemini_search_jobs failed", exc_info=True)
    return jobs


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def resume_titles(profile: Optional[Profile]) -> List[str]:
    """Search titles from the résumé: up to two distinct most recent roles, else '<top skill> Developer'."""
    from app.services.matcher import selected_skills

    titles: List[str] = []
    for e in json_decode(profile.parsed_experience, []) if profile else []:
        title = str(e.get("title") or "").strip() if isinstance(e, dict) else ""
        if title and title.lower() not in {t.lower() for t in titles}:
            titles.append(title)
    skills = selected_skills(profile)
    return titles[:2] or ([f"{skills[0]} Developer"] if skills else [])


def relevant(job: Dict[str, Any], titles: List[str], skills: List[str]) -> bool:
    """Résumé relevance: the title shares a role word or skill, or the description names 3+ selected skills."""
    if title_relevant(job["title"], titles, skills):
        return True
    text = (job.get("description") or "").lower()
    hits = sum(1 for s in skills if s.lower() not in _NON_ROLE_SKILLS
               and re.search(r"(?<!\w)" + re.escape(s.lower()) + r"(?!\w)", text))
    return hits >= 3


def _resume_skill_tags(profile: Optional[Profile]) -> List[str]:
    from app.services.matcher import selected_skills

    skills = selected_skills(profile)
    tags = []
    for s in skills:
        tag = str(s).lower().replace(".js", "").replace(" ", "-")
        if tag not in tags and str(s).lower() not in _NON_ROLE_SKILLS:
            tags.append(tag)
    return tags[:3]


async def run_search(user_id: int, session: Session) -> None:
    """Aggregate jobs from all sources for a user and persist new entries."""
    with _active_lock:
        if user_id in _active_users:
            logger.info("run_search: search already running for user_id=%s", user_id)
            return
        _active_users.add(user_id)
    try:
        await _run_search(user_id, session)
    finally:
        with _active_lock:
            _active_users.discard(user_id)


async def _run_search(user_id: int, session: Session) -> None:
    from app.services import matcher

    settings = session.exec(select(Settings).where(Settings.user_id == user_id)).first()
    profile = session.exec(select(Profile).where(Profile.user_id == user_id)).first()

    if not settings:
        logger.warning("run_search: no settings found for user_id=%s", user_id)
        return

    from app.services.llm import providers_for

    # Strictly résumé-based: titles from the résumé's roles, filtering by the skills selected on Profile
    target_titles = resume_titles(profile)
    skills = matcher.selected_skills(profile)
    target_locations: List[str] = json_decode(settings.target_locations, [])

    if not target_titles:
        logger.info("run_search: no résumé roles or skills to search with for user_id=%s", user_id)
        return

    if not target_locations and profile and profile.location:
        target_locations = [profile.location]

    gemini_key = next((p["key"] for p in providers_for(settings) if p["kind"] == "gemini"), "")

    all_raw_jobs: List[Dict[str, Any]] = []

    # Remote first: worldwide remote boards + LinkedIn worldwide remote
    if settings.remote_ok:
        for title in target_titles:
            all_raw_jobs.extend(await asyncio.to_thread(_scrape_remotive, title))
            all_raw_jobs.extend(await asyncio.to_thread(
                _scrape_jobspy, title, "Worldwide", ["linkedin"], True))
            if gemini_key:
                all_raw_jobs.extend(await _gemini_search_jobs(title, "remote worldwide", gemini_key))
        tags = _resume_skill_tags(profile)
        if tags:
            all_raw_jobs.extend(await asyncio.to_thread(_scrape_remoteok, tags))

    # Local / on-site searches
    for title in target_titles:
        for location in target_locations or [""]:
            all_raw_jobs.extend(await asyncio.to_thread(
                _scrape_jobspy, title, location, ["linkedin", "indeed", "naukri", "glassdoor"]))
        if gemini_key and target_locations:
            all_raw_jobs.extend(await _gemini_search_jobs(title, target_locations[0], gemini_key))

    # Kerala tech parks only for users looking there; titles only, so filter by title
    where = " ".join(target_locations + [profile.location or "" if profile else ""]).lower()
    if any(h in where for h in _KERALA_HINTS):
        park_jobs = await asyncio.to_thread(_scrape_infopark) + await asyncio.to_thread(_scrape_technopark)
        all_raw_jobs.extend(park_jobs)

    # Every source returns loosely matched results; keep only jobs relevant to the résumé
    fetched = len(all_raw_jobs)
    all_raw_jobs = [j for j in all_raw_jobs if relevant(j, target_titles, skills)]
    logger.info("run_search: kept %d of %d results relevant to the résumé (titles %s)",
                len(all_raw_jobs), fetched, target_titles)

    existing_urls: Set[str] = set(session.exec(select(Job.url).where(Job.user_id == user_id)).all())
    new_job_ids: List[int] = []

    for raw in all_raw_jobs:
        url = (raw.get("url") or "").strip()
        if not url or url in existing_urls:
            continue
        existing_urls.add(url)

        job = Job(
            user_id=user_id,
            title=raw.get("title") or "Unknown",
            company=raw.get("company") or "Unknown",
            location=raw.get("location"),
            url=url,
            source=raw.get("source", "unknown"),
            description=raw.get("description"),
            salary_min=raw.get("salary_min"),
            salary_max=raw.get("salary_max"),
            company_rating=raw.get("company_rating"),
            recruiter_email=raw.get("recruiter_email"),
            apply_url=raw.get("apply_url"),
            is_remote=raw.get("is_remote", False),
        )
        session.add(job)
        session.flush()  # get job.id
        new_job_ids.append(job.id)

    session.add(ActivityLog(
        user_id=user_id,
        event="search_completed",
        detail=f"Found {len(new_job_ids)} new jobs for {', '.join(target_titles)} "
               f"({len(all_raw_jobs)} of {fetched} results matched your résumé)",
    ))
    session.commit()
    logger.info("run_search: user_id=%s found %d new jobs across all sources", user_id, len(new_job_ids))

    # Score new jobs plus any left unscored earlier (e.g. Ollama was down); remote first
    retry_ids = []
    for old in session.exec(
        select(Job).where(Job.user_id == user_id, Job.status == "new", Job.id.not_in(new_job_ids))
    ).all():
        # Found before the résumé-only search (or before the résumé/skills changed): drop instead of scoring
        if relevant({"title": old.title, "description": old.description or ""}, target_titles, skills):
            retry_ids.append(old.id)
        else:
            old.status, old.score_reason = "skipped", "Doesn't match your résumé's roles or selected skills"
            session.add(old)
    session.commit()
    to_score = sorted(
        new_job_ids + list(retry_ids),
        key=lambda jid: not (session.get(Job, jid).is_remote),
    )
    if to_score:
        await matcher.score_jobs(
            user_id=user_id,
            job_ids=to_score,
            session=session,
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
            embed_model=EMBED_MODEL,
        )
