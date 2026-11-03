"""Find a hiring email for a job that came without one, so it can be applied to by email.

find_hiring_email(job) -> address or None
    — the company's own site (from the job's links, else a web search for the company), its careers /
      contact pages, and only addresses published there on the company's domain (never guessed).
"""
import asyncio
import logging
import re
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)
logging.getLogger("ddgs").setLevel(logging.WARNING)  # it logs every failed search engine at INFO

_PAGES = ("", "careers", "jobs", "career", "contact", "contact-us", "about", "about-us")
# Hiring inboxes first, then the general one; sales@, marketing@, a person's address… are never used
_RANK = (r"career|job|recruit|hiring|talent|hr\b|^hr|people|apply|cv|resume",
         r"^(info|hello|contact|team|office|enquir|inquir)")
_SUFFIXES = {"inc", "ltd", "llc", "llp", "pvt", "private", "limited", "technologies", "technology", "tech",
             "solutions", "software", "systems", "services", "gmbh", "co", "corp", "corporation", "company",
             "group", "the", "and", "global", "india", "international", "labs", "digital", "consulting", "it"}
_NOT_COMPANY_SITES = ("linkedin.", "naukri.", "indeed.", "glassdoor.", "facebook.", "twitter.", "x.com",
                      "instagram.", "youtube.", "wikipedia.", "crunchbase.", "ambitionbox.", "zoominfo.",
                      "bloomberg.", "github.", "medium.", "whatjobs.", "jobleads.", "adzuna.", "reed.co",
                      "djinni.", "himalayas.", "startup.jobs", "wellfound.", "remoteok.", "remotive.",
                      "freehire.", "getmatch.", "tracxn.", "apollo.io", "rocketreach.", "signalhire.")


def company_name(company: str) -> str:
    """The company's name as a domain would spell it ("X-on Health Ltd" -> "xonhealth")."""
    words = re.sub(r"\(.*?\)", " ", (company or "").lower())
    return "".join(w for w in re.findall(r"[a-z0-9]+", words) if w not in _SUFFIXES)


def domain_matches(domain: str, company: str) -> bool:
    """The domain is the company's name (+ "group", "hq"…), so a search hit isn't a similarly named firm."""
    label = domain.lower().removeprefix("www.").split(".")[0].replace("-", "")
    name = company_name(company)
    return len(name) >= 3 and label.startswith(name) and label[len(name):] in _SUFFIXES | {"", "hq", "app", "ai", "io"}


def pick_email(emails: Iterable[str], domain: str) -> Optional[str]:
    """The best hiring inbox on the company's own domain (or a subdomain of it)."""
    from app.services.mailer import usable_recruiter_email

    domain = domain.lower().removeprefix("www.")
    mine = [e for e in dict.fromkeys(e.lower().rstrip(".") for e in emails)
            if usable_recruiter_email(e) and (e.split("@")[1] == domain or e.split("@")[1].endswith("." + domain))]
    for pattern in _RANK:
        hit = next((e for e in mine if re.search(pattern, e.split("@")[0])), None)
        if hit:
            return hit
    return None


def _company_host(url: Optional[str], company: str) -> Optional[str]:
    from app.services.site_apply import detect_ats

    host = urlparse(url or "").netloc.lower().removeprefix("www.")
    if not host or detect_ats(url) or any(s in host for s in _NOT_COMPANY_SITES):
        return None
    return host if domain_matches(host, company) else None


def _search_company_site(company: str) -> Tuple[Optional[str], List[str]]:
    """The company's site found by a web search, and the hit pages on it (often its careers page)."""
    from ddgs import DDGS  # type: ignore

    hits: list = []
    for _ in range(2):  # the search times out now and then
        try:
            hits = DDGS().text(f"{company} official website careers", max_results=8)
            break
        except Exception as exc:  # rate limits, network: the job just keeps its old route
            logger.info("email_finder: search for %r failed: %s", company, exc)
    urls = [r.get("href") or "" for r in hits]
    host = next((h for h in (_company_host(u, company) for u in urls) if h), None)
    return host, [u for u in urls if host and urlparse(u).netloc.lower().removeprefix("www.") == host]


def _has_mail_server(domain: str) -> bool:
    import dns.resolver  # type: ignore

    try:
        return bool(dns.resolver.resolve(domain, "MX", lifetime=8))
    except Exception:
        return False


async def _page_emails(client: httpx.AsyncClient, url: str) -> List[str]:
    from bs4 import BeautifulSoup

    from app.services.sources import _EMAIL_REGEX

    try:
        resp = await client.get(url)
        if resp.status_code >= 400 or "html" not in resp.headers.get("content-type", ""):
            return []
    except httpx.HTTPError:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    mailto = [a["href"][7:].split("?")[0] for a in soup.select("a[href^='mailto:' i]")]
    return mailto + _EMAIL_REGEX.findall(soup.get_text(" "))


async def find_hiring_email(job) -> Optional[str]:
    """A published hiring address at the job's company, or None."""
    from app.security import block_private_hook
    from app.services.site_apply import _UA

    host, pages = _company_host(job.apply_url, job.company) or _company_host(job.url, job.company), []
    if not host:
        host, pages = await asyncio.to_thread(_search_company_site, job.company)
    if not host:
        return None
    domain = host.removeprefix("careers.").removeprefix("jobs.")
    if not await asyncio.to_thread(_has_mail_server, domain):
        return None
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers={"User-Agent": _UA},
                                 event_hooks={"request": [block_private_hook]}) as client:
        found: List[str] = []
        for url in dict.fromkeys(pages + [urljoin(f"https://{host}/", p) for p in _PAGES]):
            found += await _page_emails(client, url)
            best = pick_email(found, domain)
            if best and re.search(_RANK[0], best.split("@")[0]):
                break  # a careers inbox: no need to read more pages
    email = pick_email(found, domain)
    logger.info("email_finder: %s (%s) -> %s", job.company, host, email)
    return email
