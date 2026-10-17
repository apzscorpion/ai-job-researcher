"""
Résumé text extraction and LLM-based parsing service.

extract_text(path) — supports PDF (.pdf) and Word (.docx) files.
parse_resume(text, ollama_url, model) — calls Ollama chat API to extract
    structured data: skills, experience, education, total_years.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text(path: str) -> str:
    """Extract plain text from a PDF or DOCX file.

    Returns an empty string on any error.
    """
    file_path = Path(path)
    suffix = file_path.suffix.lower()

    try:
        if suffix == ".pdf":
            return _extract_pdf(file_path)
        elif suffix == ".docx":
            return _extract_docx(file_path)
        else:
            logger.warning("extract_text: unsupported file type '%s'", suffix)
            return ""
    except Exception:
        logger.exception("extract_text failed for path: %s", path)
        return ""


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: List[str] = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n".join(pages)


def _extract_docx(path: Path) -> str:
    import docx

    doc = docx.Document(str(path))
    paragraphs: List[str] = [para.text for para in doc.paragraphs if para.text.strip()]
    return "\n".join(paragraphs)


# ---------------------------------------------------------------------------
# LLM-based parsing
# ---------------------------------------------------------------------------

_PARSE_SYSTEM_PROMPT = """\
You are a résumé parser. Given the raw text of a résumé, extract and return ONLY valid JSON
with the following structure (no markdown, no extra text):
{
  "skills": ["skill1", "skill2", ...],
  "experience": [
    {"title": "...", "company": "...", "duration": "...", "description": "..."},
    ...
  ],
  "education": [
    {"degree": "...", "institution": "...", "year": "..."},
    ...
  ],
  "total_years": <float>
}
If a field cannot be determined, use an empty list or 0.0 for total_years.
"""


async def _resolve_ollama_model(ollama_url: str, preferred_model: str) -> str:
    """Check if preferred_model is available in Ollama; fallback to first available text model."""
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status_code == 200:
                models = [m.get("name", "") for m in resp.json().get("models", [])]
                if preferred_model in models:
                    return preferred_model
                for m in models:
                    if preferred_model.split(":")[0] in m:
                        return m
                for m in models:
                    if not any(k in m for k in ["embed", "nomic", "bge"]):
                        logger.info("parse_resume: preferred '%s' not found, using '%s'", preferred_model, m)
                        return m
    except Exception as exc:
        logger.debug("Failed to query Ollama tags: %s", exc)
    return preferred_model


import re

COMMON_TECH_SKILLS = [
    "Python", "JavaScript", "TypeScript", "React", "React.js", "React Native", "Vue", "Vue.js", "Nuxt", "Nuxt.js",
    "Angular", "Next.js", "Node.js", "Express", "Express.js", "FastAPI", "Django", "Flask", "SQL", "PostgreSQL",
    "MySQL", "MongoDB", "Redis", "Docker", "Kubernetes", "AWS", "GCP", "Google Cloud", "Azure", "Git", "GitHub", "GitLab",
    "HTML", "HTML5", "CSS", "CSS3", "SASS", "SCSS", "Tailwind CSS", "Bootstrap", "REST API", "GraphQL", "Java", "Spring Boot",
    "C++", "C#", ".NET", "PHP", "Laravel", "Ruby", "Ruby on Rails", "Go", "Golang", "Rust", "Linux", "CI/CD",
    "Machine Learning", "Deep Learning", "TensorFlow", "PyTorch", "Pandas", "NumPy", "Scikit-Learn", "NLP",
    "Selenium", "Playwright", "Cypress", "Jest", "Microservices", "Kafka", "RabbitMQ", "Elasticsearch", "Airflow",
    "Spark", "PySpark", "Tableau", "Power BI", "Firebase", "Pinia", "Vuex", "Storybook", "RxJS", "Web Components",
    "Webpack", "Vite", "Agile", "Scrum",
    "Redux", "Redux Toolkit", "MobX", "Zustand", "Svelte", "Remix", "Gatsby", "Micro-frontends",
    "Module Federation", "Material UI", "Chakra UI", "styled-components", "Babel", "ESLint", "Rollup",
    "Mocha", "Jasmine", "Karma", "Enzyme", "React Testing Library", "Vitest", "TDD", "Accessibility", "WCAG",
    "SEO", "PWA", "WebSockets", "Socket.io", "Three.js", "D3.js", "Chart.js", "Figma", "Electron",
    "NestJS", "Prisma", "TypeORM", "Sequelize", "Hibernate", "gRPC", "OpenAPI", "OAuth", "JWT",
    "Kotlin", "Flutter", "Dart", "Scala", "Terraform", "Ansible", "Jenkins", "GitHub Actions", "Nginx",
    "Serverless", "AWS Lambda", "DynamoDB", "Snowflake", "Jira",
]


def _rule_based_extract(text: str) -> Dict[str, Any]:
    """Fast, deterministic extraction using regex and skill lexicons (<0.02s)."""
    norm = re.sub(r"[\u2013\u2014\ufffd]", "-", text).replace("\r\n", "\n")
    lower_text = norm.lower()

    # 1. Contact info
    lines = [l.strip() for l in norm.splitlines() if l.strip()]
    name = ""
    for line in lines[:4]:
        if not any(k in line.lower() for k in ["@", "http", "curriculum", "resume", "developer", "engineer", "+"]):
            name = line.strip()
            break

    phone_match = re.search(r"(\+?\d{1,3}[\s-]?)?\(?\d{3,5}\)?[\s.-]?\d{3,5}[\s.-]?\d{3,5}", norm)
    phone = phone_match.group(0).strip() if phone_match else ""

    linkedin_match = re.search(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[a-zA-Z0-9_\-]+", norm)
    linkedin = linkedin_match.group(0).strip() if linkedin_match else ""

    loc_match = re.search(r"([A-Z][a-zA-Z\s]+,\s*(?:Kerala|Karnataka|Tamil Nadu|Maharashtra|Delhi|India|[A-Z]{2}))", norm)
    location = loc_match.group(1).strip() if loc_match else ""

    # 2. Skills
    found_skills: List[str] = []
    for skill in COMMON_TECH_SKILLS:
        pattern = r"(?<!\w)" + re.escape(skill.lower()) + r"(?!\w)"
        if re.search(pattern, lower_text):
            found_skills.append(skill)

    # 3. Years of experience
    years_match = re.search(r"(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)(?:\s+of)?(?:\s+experience)?", lower_text)
    total_years = float(years_match.group(1)) if years_match else 0.0

    # 4. Work Experience
    experience: List[Dict[str, str]] = []
    exp_heading = re.search(r"\n\s*(?:PROFESSIONAL\s+EXPERIENCE|WORK\s+EXPERIENCE)\s*\n(.*?)(?:\n\s*(?:KEY\s+PROJECTS|PROJECTS|EDUCATION)\s*\n|$)", norm, re.DOTALL | re.IGNORECASE)
    if exp_heading:
        block = exp_heading.group(1).strip()
        job_pattern = r"([^\n]+?)\s*-\s*([^\n]+?)\n([A-Za-z]+\s+\d{4}\s*-\s*(?:Present|[A-Za-z]+\s+\d{4}))(?:\s*\|\s*([^\n]+))?"
        for j in re.finditer(job_pattern, block):
            experience.append({
                "title": j.group(1).strip(),
                "company": j.group(2).strip(),
                "duration": j.group(3).strip(),
                "description": (j.group(4) or "").strip(),
            })

    # 5. Education
    education: List[Dict[str, str]] = []
    edu_heading = re.search(r"\n\s*EDUCATION\s*\n(.*?)(?:$)", norm, re.DOTALL | re.IGNORECASE)
    if edu_heading:
        eblock = edu_heading.group(1).strip()
        edu_pattern = r"([^\n]+?)\s*-\s*([^\n]+?)\n(\d{4}\s*-\s*\d{4}|\d{4})"
        for m in re.finditer(edu_pattern, eblock):
            education.append({
                "degree": m.group(1).strip(),
                "institution": m.group(2).strip(),
                "year": m.group(3).strip(),
            })

    if not education:
        matches = re.findall(r"\b(B\.?Tech|M\.?Tech|B\.?E\.?|M\.?E\.?|B\.?S\.?|M\.?S\.?|Bachelor|Master|Ph\.?D|BCA|MCA|Diploma)\b[^\n,.]*", norm, re.IGNORECASE)
        for m in matches[:3]:
            education.append({"degree": m.strip(), "institution": "", "year": ""})

    return {
        "contact": {
            "name": name,
            "phone": phone,
            "location": location,
            "linkedin_url": linkedin,
        },
        "skills": found_skills[:40],
        "experience": experience,
        "education": education,
        "total_years": total_years,
    }


async def parse_resume(text: str, ollama_url: str, model: str) -> Dict[str, Any]:
    """Call Ollama chat API with short timeout (<5s), instantly falling back to rule-based parser."""
    if not text.strip():
        logger.warning("parse_resume: extracted text is empty")
        return {"contact": {}, "skills": [], "experience": [], "education": [], "total_years": 0.0}

    # Step 1: Compute fast deterministic fallback (0.01s)
    rule_fallback = _rule_based_extract(text)
    logger.info("parse_resume: rule extractor ready (%d skills, %d exp, %.1f yrs, name='%s')",
                len(rule_fallback["skills"]), len(rule_fallback["experience"]),
                rule_fallback["total_years"], rule_fallback["contact"]["name"])

    # Step 2: Attempt Ollama refinement with 4.0s timeout
    try:
        effective_model = await _resolve_ollama_model(ollama_url, model)
        messages = [
            {"role": "system", "content": _PARSE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Parse the following résumé:\n\n{text[:2000]}",
            },
        ]
        payload = {
            "model": effective_model,
            "messages": messages,
            "stream": False,
            "format": "json",
        }
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.post(f"{ollama_url}/api/chat", json=payload)
            if resp.status_code == 200:
                data = resp.json()
                content: str = data.get("message", {}).get("content", "")
                parsed = json.loads(content)
                llm_skills = parsed.get("skills", [])
                merged_skills = list(dict.fromkeys(rule_fallback["skills"] + llm_skills))
                return {
                    "contact": rule_fallback["contact"],
                    "skills": merged_skills,
                    "experience": parsed.get("experience") or rule_fallback["experience"],
                    "education": parsed.get("education") or rule_fallback["education"],
                    "total_years": float(parsed.get("total_years") or rule_fallback["total_years"]),
                }
    except Exception as exc:
        logger.info("parse_resume: using instant rule-based parsing (%s)", type(exc).__name__)

    return rule_fallback
