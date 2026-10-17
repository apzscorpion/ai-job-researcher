"""
Jobs router: list, filter, retrieve, trigger search, and delete job listings.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, or_, select

from app.auth import get_current_user
from app.db import get_session
from app.models import Application, Job, User, json_decode

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _user_skills(user_id: int, session: Session) -> List[str]:
    from app.models import Profile
    from app.services.matcher import selected_skills

    return selected_skills(session.exec(select(Profile).where(Profile.user_id == user_id)).first())


def _job_to_dict(job: Job, skills: Optional[List[str]] = None, app: Optional[Application] = None) -> Dict[str, Any]:
    from app.services.matcher import skill_match
    from app.services.scheduler import route_for

    matched, missing = skill_match(skills or [], f"{job.title}\n{job.description or ''}")
    return {
        "apply_via": route_for(job),  # email | linkedin | naukri | site
        "application_status": app.status if app else None,
        "application_id": app.id if app else None,
        "matched_skills": matched,  # job asks for them and they are among the user's selected skills
        "missing_skills": missing,
        "apply_url": job.apply_url,
        "id": job.id,
        "user_id": job.user_id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "url": job.url,
        "source": job.source,
        "description": job.description,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "company_rating": job.company_rating,
        "recruiter_email": job.recruiter_email,
        "score": job.score,
        "score_reason": job.score_reason,
        "resume_tips": json_decode(job.resume_tips, []),
        "is_remote": job.is_remote,
        "found_at": job.found_at,
        "status": job.status,
    }


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

@router.get("", include_in_schema=False)
@router.get("/")
def list_jobs(
    q: Optional[str] = Query(None, description="Text search in title/company/description"),
    source: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    max_score: Optional[float] = Query(None),
    remote: Optional[bool] = Query(None),
    region: Optional[str] = Query(None, description="india | international | state:<name> | park:<name>"),
    has_application: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    skip: Optional[int] = Query(None, ge=0),
    limit: Optional[int] = Query(None, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    stmt = select(Job).where(Job.user_id == current_user.id)

    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                col(Job.title).ilike(like),
                col(Job.company).ilike(like),
                col(Job.description).ilike(like),
            )
        )
    if source:
        stmt = stmt.where(Job.source == source)
    if status:
        stmt = stmt.where(Job.status == status)
    if min_score is not None:
        stmt = stmt.where(col(Job.score) >= min_score)
    if max_score is not None:
        stmt = stmt.where(col(Job.score) <= max_score)

    if remote is not None:
        stmt = stmt.where(Job.is_remote == remote)
    if has_application is not None:
        with_app = col(Job.id).in_(select(Application.job_id).where(Application.user_id == current_user.id))
        stmt = stmt.where(with_app if has_application else ~with_app)
    if region:
        from app.services.regions import region_clause

        clause = region_clause(region)
        if clause is None:
            raise HTTPException(status_code=400, detail=f"Unknown location filter: {region}")
        stmt = stmt.where(clause)

    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    effective_limit = limit if limit is not None else page_size
    effective_offset = skip if skip is not None else (page - 1) * page_size
    stmt = stmt.offset(effective_offset).limit(effective_limit).order_by(
        col(Job.is_remote).desc(), col(Job.score).desc().nulls_last(), col(Job.found_at).desc()
    )
    jobs = session.exec(stmt).all()
    skills = _user_skills(current_user.id, session)
    apps = {a.job_id: a for a in session.exec(select(Application).where(
        Application.user_id == current_user.id, col(Application.job_id).in_([j.id for j in jobs]))).all()}

    return {
        "total": total,
        "page": page,
        "page_size": effective_limit,
        "items": [_job_to_dict(j, skills, apps.get(j.id)) for j in jobs],
    }


@router.get("/regions")
def list_regions(current_user: User = Depends(get_current_user)) -> Dict[str, List[str]]:
    from app.services.regions import PARKS, STATES

    return {"states": list(STATES), "parks": list(PARKS)}


class ApplyBody(BaseModel):
    job_ids: List[int] = Field(..., min_length=1, max_length=200)


@router.post("/apply")
def apply_to_jobs(
    body: ApplyBody,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Queue the chosen jobs; the scheduler applies one at a time with the usual gaps and daily caps."""
    from datetime import datetime

    from app.models import Settings

    jobs = session.exec(select(Job).where(Job.user_id == current_user.id, col(Job.id).in_(body.job_ids))).all()
    existing = {a.job_id: a for a in session.exec(select(Application).where(
        Application.user_id == current_user.id, col(Application.job_id).in_([j.id for j in jobs]))).all()}
    queued = skipped = 0
    for job in jobs:
        app = existing.get(job.id)
        if app and app.status != "failed":  # already queued, sent, or waiting on the user
            skipped += 1
            continue
        app = app or Application(user_id=current_user.id, job_id=job.id, method="pending")
        app.status, app.notes, app.applied_at = "queued", "Picked by you on the Jobs page.", datetime.utcnow()
        session.add(app)
        queued += 1
    session.commit()

    settings = session.exec(select(Settings).where(Settings.user_id == current_user.id)).first()
    on = bool(settings and settings.auto_apply_enabled)
    msg = f"Queued {queued} job(s)" + (f"; {skipped} already had an application" if skipped else "") + ". "
    msg += ("They go out one at a time with gaps between them, within your apply hours and daily limits."
            if on else "Auto-apply is paused — turn it on from the Dashboard so they start going out.")
    return {"queued": queued, "skipped": skipped, "auto_apply_enabled": on, "message": msg}


# ---------------------------------------------------------------------------
# GET /{job_id}
# ---------------------------------------------------------------------------

@router.get("/{job_id}")
def get_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    job = session.get(Job, job_id)
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    app = session.exec(select(Application).where(Application.user_id == current_user.id,
                                                 Application.job_id == job.id)).first()
    return _job_to_dict(job, _user_skills(current_user.id, session), app)


# ---------------------------------------------------------------------------
# POST /{job_id}/resume-report
# ---------------------------------------------------------------------------

_SHORT_JD = 800  # below this the stored description is probably a snippet


_NO_AI = ("No AI replied: every cloud key may be out of quota for today and Ollama may not be running. "
          "Add another key in Settings → AI providers, or start Ollama and try again.")


async def _ai_context(job_id: int, user_id: int, session: Session):
    """(job with the fullest description we can get, profile, providers, where the description came from)."""
    from app.models import Profile, Settings
    from app.services import llm, sources

    job = session.get(Job, job_id)
    if not job or job.user_id != user_id:
        raise HTTPException(status_code=404, detail="Job not found")
    profile = session.exec(select(Profile).where(Profile.user_id == user_id)).first()
    if not profile or not profile.resume_text:
        raise HTTPException(status_code=400, detail="Upload your résumé on the Profile page first.")
    settings = session.exec(select(Settings).where(Settings.user_id == user_id)).first()
    providers = llm.providers_for(settings) if settings else []
    gemini_key = next((p["key"] for p in providers if p["kind"] == "gemini"), "")

    # Get the full JD from the posting when the stored one is only a snippet
    jd_source = "stored"
    if len(job.description or "") < _SHORT_JD:
        fetched = await sources.fetch_description(job.url)
        if fetched:
            jd_source = "posting page"
        elif gemini_key:
            # Page blocked us (LinkedIn often returns 429): let Gemini find it via Google Search
            fetched = await llm.gemini_search(
                f"Find the job posting '{job.title}' at '{job.company}' ({job.url}) and return its full "
                "job description text: responsibilities, requirements, nice-to-haves. Text only, no commentary.",
                gemini_key,
            )
            jd_source = "Google search via Gemini (may be summarised)"
        if len(fetched) > max(300, len(job.description or "")):
            job.description = fetched
            session.add(job)
            session.commit()
        else:
            jd_source = "stored"
    return job, profile, providers, jd_source


@router.post("/{job_id}/resume-report")
async def resume_report(
    job_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    from app.config import OLLAMA_MODEL, OLLAMA_URL
    from app.services import matcher

    job, profile, providers, jd_source = await _ai_context(job_id, current_user.id, session)
    report, used = await matcher.resume_report(job, profile.resume_text, providers, OLLAMA_URL, OLLAMA_MODEL)
    if not report:
        raise HTTPException(status_code=502, detail=_NO_AI)
    return {"report": report, "jd_source": jd_source, "model": used}


@router.post("/{job_id}/cover-letter")
async def cover_letter(
    job_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    from app.config import OLLAMA_MODEL, OLLAMA_URL
    from app.services import mailer

    job, profile, providers, jd_source = await _ai_context(job_id, current_user.id, session)
    text, used = await mailer.write_cover_letter(job, profile, providers, OLLAMA_URL, OLLAMA_MODEL)
    if not text:
        raise HTTPException(status_code=502, detail=_NO_AI)
    return {"report": text, "jd_source": jd_source, "model": used}


# ---------------------------------------------------------------------------
# POST /search  (trigger background job search)
# ---------------------------------------------------------------------------

def _run_search_bg(user_id: int) -> None:
    import asyncio
    from sqlmodel import Session as _Session
    from app.db import engine
    from app.services import sources

    with _Session(engine) as session:
        asyncio.run(sources.run_search(user_id, session))


@router.post("/search")
def trigger_search(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
) -> Dict[str, bool]:
    background_tasks.add_task(_run_search_bg, current_user.id)
    return {"queued": True}


# ---------------------------------------------------------------------------
# DELETE /{job_id}
# ---------------------------------------------------------------------------

@router.delete("/{job_id}")
def delete_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, str]:
    job = session.get(Job, job_id)
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    # Cascade delete applications
    for app in session.exec(select(Application).where(Application.job_id == job_id)).all():
        session.delete(app)

    session.delete(job)
    session.commit()
    return {"detail": "Job deleted"}
