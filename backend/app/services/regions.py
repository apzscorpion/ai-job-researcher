"""
Location filters for the Jobs page: India, International, a state, or a tech park.

region values: "india" | "international" | "state:<name>" | "park:<name>"
region_clause(region) -> SQL condition on Job (None when the value is unknown)
"""

from __future__ import annotations

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
