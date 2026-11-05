"""
Location filters for the Jobs page: India, International, a state, or a tech park.

region values: "india" | "international" | "state:<name>" | "park:<name>"
region_clause(region) -> SQL condition on Job (None when the value is unknown)
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlmodel import and_, col, not_, or_

from app.models import Job

# State -> words that place a job there (state name plus cities often given without it)
STATES: Dict[str, List[str]] = {
    "Kerala": ["kerala", "kochi", "cochin", "ernakulam", "kakkanad", "trivandrum", "thiruvananthapuram",
               "kozhikode", "calicut", "thrissur", "kollam", "kottayam", "kannur", "pathanamthitta",
               "alappuzha", "palakkad", "malappuram"],
    "Karnataka": ["karnataka", "bengaluru", "bangalore", "mysuru", "mysore", "mangaluru", "mangalore"],
    "Tamil Nadu": ["tamil nadu", "chennai", "coimbatore", "madurai", "trichy", "tiruchirappalli"],
    "Telangana": ["telangana", "hyderabad", "secunderabad"],
    "Andhra Pradesh": ["andhra pradesh", "visakhapatnam", "vizag", "vijayawada"],
    "Maharashtra": ["maharashtra", "mumbai", "pune", "nagpur", "navi mumbai", "thane", "nashik"],
    "Delhi NCR": ["delhi", "new delhi", "gurugram", "gurgaon", "noida", "greater noida", "faridabad", "ghaziabad"],
    "Haryana": ["haryana", "gurugram", "gurgaon", "faridabad"],
    "Uttar Pradesh": ["uttar pradesh", "noida", "lucknow", "ghaziabad", "kanpur"],
    "Gujarat": ["gujarat", "ahmedabad", "gandhinagar", "surat", "vadodara"],
    "West Bengal": ["west bengal", "kolkata"],
    "Rajasthan": ["rajasthan", "jaipur", "udaipur"],
    "Madhya Pradesh": ["madhya pradesh", "indore", "bhopal"],
    "Punjab / Chandigarh": ["punjab", "chandigarh", "mohali", "ludhiana"],
    "Odisha": ["odisha", "bhubaneswar"],
    "Goa": ["goa"],
}

# Tech park -> words that name it in the location or description
PARKS: Dict[str, List[str]] = {
    "Technopark (Trivandrum)": ["technopark", "kazhakoottam", "kazhakuttam"],
    "Infopark (Kochi)": ["infopark", "kakkanad"],
    "SmartCity (Kochi)": ["smartcity kochi", "smart city kochi", "smartcity, kochi"],
    "Cyberpark (Kozhikode)": ["cyberpark", "cyber park"],
    "Technocity (Trivandrum)": ["technocity"],
    "KINFRA parks (Kerala)": ["kinfra"],
    "Electronic City (Bengaluru)": ["electronic city", "electronics city"],
    "Manyata Tech Park (Bengaluru)": ["manyata"],
    "ITPL / Whitefield (Bengaluru)": ["itpl", "whitefield"],
    "Embassy Tech Village (Bengaluru)": ["embassy tech village", "embassy techvillage"],
    "HITEC City (Hyderabad)": ["hitec city", "hitech city", "hi-tech city", "gachibowli"],
    "Hinjewadi (Pune)": ["hinjewadi", "hinjawadi"],
    "Magarpatta (Pune)": ["magarpatta"],
    "Tidel Park (Chennai)": ["tidel park"],
    "OMR / SIPCOT (Chennai)": ["sipcot", "old mahabalipuram road"],
    "DLF Cyber City (Gurugram)": ["cyber city", "cybercity", "cyber hub"],
    "Mindspace (Mumbai / Hyderabad)": ["mindspace"],
    "Salt Lake Sector V (Kolkata)": ["sector v,", "sector-v", "salt lake, kolkata", "saltlake"],  # not Salt Lake City, Utah
    "GIFT City (Gujarat)": ["gift city"],
}

_INDIA_WORDS = sorted({"india", *(w for words in STATES.values() for w in words)})


# Auto-apply priority groups, in the order the user ranks them (Settings.apply_priority)
PRIORITY_GROUPS: Dict[str, str] = {
    "remote_district": "Remote, based in my district",
    "district": "Office jobs in my district",
    "remote_india": "Remote in India",
    "hybrid": "Hybrid",
    "remote_abroad": "Remote outside India",
}
_HYBRID = re.compile(r"\bhybrid\b(?! app)", re.I)


def job_groups(job: Job, places: List[str]) -> set:
    """Which priority groups a job falls in. places: the user's district and its towns."""
    loc = (job.location or "").lower()
    remote = job.is_remote or "remote" in loc
    india = any(w in loc for w in _INDIA_WORDS) and "indiana" not in loc
    in_district = any(p.strip().lower() in loc for p in places if p.strip())
    groups = set()
    if in_district:
        groups.add("remote_district" if remote else "district")
    if remote:
        groups.add("remote_india" if india or in_district else "remote_abroad")
    if _HYBRID.search(f"{loc} {job.title or ''}"):
        groups.add("hybrid")
    return groups


def home_places(saved: List[str], profile_location: Optional[str]) -> List[str]:
    """The user's own district list, else the district from their profile ("Pathanamthitta, Kerala")."""
    district = (profile_location or "").split(",")[0].strip()
    return saved or ([district] if district else [])


def priority_rank(job: Job, order: List[str], places: List[str]) -> Optional[int]:
    """Position of the job's best group in the user's order; len(order) when it's in none, None if unranked."""
    if not order:
        return None
    hits = [order.index(g) for g in job_groups(job, places) if g in order]
    return min(hits) if hits else len(order)


def _any(text, words: List[str]):
    return or_(*(text.like(f"%{w}%") for w in words))


def region_clause(region: str) -> Optional[object]:
    loc = func.lower(func.coalesce(col(Job.location), ""))
    india = and_(_any(loc, _INDIA_WORDS), not_(loc.like("%indiana%")))  # "india" is inside "Indiana"
    if region == "india":
        return india
    if region == "international":
        return not_(india)
    kind, _, name = region.partition(":")
    if kind == "state" and name in STATES:
        return _any(loc, STATES[name])
    if kind == "park" and name in PARKS:
        desc = func.lower(func.coalesce(col(Job.description), ""))
        hit = or_(_any(loc, PARKS[name]), _any(desc, PARKS[name]))
        source = next((s for s in ("infopark", "technopark") if name.lower().startswith(s)), None)
        return or_(hit, col(Job.source) == source) if source else hit  # jobs scraped from the park's own site
    return None
