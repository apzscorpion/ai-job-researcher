"""
Profile router: view/update profile, upload/delete résumé, and full data reset.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import get_current_user
from app.db import get_session
from app.models import (
    ActivityLog,
    Application,
    Job,
    Profile,
    Settings,
    User,
    json_decode,
    json_encode,
)
from app.config import OLLAMA_MODEL, OLLAMA_URL, RESUMES_DIR
from app.services import resume as resume_service
from app.services.matcher import selected_skills

router = APIRouter(prefix="/api/profile", tags=["profile"])


def _get_profile(user_id: int, session: Session) -> Profile:
    profile = session.exec(select(Profile).where(Profile.user_id == user_id)).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


def _profile_to_dict(profile: Profile) -> Dict[str, Any]:
    skills = json_decode(profile.parsed_skills, [])
    experience = json_decode(profile.parsed_experience, [])
    education = json_decode(profile.parsed_education, [])
    filename = Path(profile.resume_path).name if profile.resume_path else None
    return {
        "id": profile.id,
        "user_id": profile.user_id,
        "name": profile.name,
        "full_name": profile.name,
        "phone": profile.phone,
        "location": profile.location,
        "linkedin_url": profile.linkedin_url,
        "resume_path": profile.resume_path,
        "resume_filename": filename,
        "resume_text": profile.resume_text,
        "parsed_skills": skills,
        "skills": skills,
        "apply_skills": selected_skills(profile),
        "parsed_experience": experience,
        "experience": experience,
        "parsed_education": education,
        "education": education,
        "total_years": profile.total_years,
        "updated_at": profile.updated_at,
    }


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

@router.get("", include_in_schema=False)
@router.get("/")
def get_profile(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    profile = _get_profile(current_user.id, session)
    return _profile_to_dict(profile)


# ---------------------------------------------------------------------------
# PUT /
# ---------------------------------------------------------------------------

class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    full_name: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    linkedin_url: Optional[str] = None
    parsed_skills: Optional[List[Any]] = None
    skills: Optional[List[Any]] = None
    parsed_experience: Optional[List[Any]] = None
    experience: Optional[List[Any]] = None
    parsed_education: Optional[List[Any]] = None
    education: Optional[List[Any]] = None
    total_years: Optional[float] = None
    apply_skills: Optional[List[str]] = None  # subset of skills used for matching and applying


@router.put("", include_in_schema=False)
@router.put("/")
def update_profile(
    body: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    profile = _get_profile(current_user.id, session)

    target_name = body.name or body.full_name
    if target_name is not None:
        profile.name = target_name
    if body.phone is not None:
        profile.phone = body.phone
    if body.location is not None:
        profile.location = body.location
    if body.linkedin_url is not None:
        profile.linkedin_url = body.linkedin_url
    
    target_skills = body.parsed_skills if body.parsed_skills is not None else body.skills
    if target_skills is not None:
        profile.parsed_skills = json_encode(target_skills)
        
    target_exp = body.parsed_experience if body.parsed_experience is not None else body.experience
    if target_exp is not None:
        profile.parsed_experience = json_encode(target_exp)
        
    target_edu = body.parsed_education if body.parsed_education is not None else body.education
    if target_edu is not None:
        profile.parsed_education = json_encode(target_edu)
        
    if body.total_years is not None:
        profile.total_years = body.total_years
    if body.apply_skills is not None:
        profile.apply_skills = json_encode(body.apply_skills)

    profile.updated_at = datetime.utcnow()
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return _profile_to_dict(profile)


# ---------------------------------------------------------------------------
# POST /resume
# ---------------------------------------------------------------------------

ALLOWED_EXTENSIONS = {".pdf", ".docx"}


@router.post("/resume")
async def upload_resume(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    suffix = Path(file.filename or "resume").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported file type '{suffix}'. Only PDF and DOCX are accepted.",
        )

    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{current_user.id}_{Path(file.filename).name}"
    dest_path = RESUMES_DIR / safe_name

    content = await file.read()
    dest_path.write_bytes(content)

    # Extract text
    text = resume_service.extract_text(str(dest_path))

    # Parse via LLM / rule-based extractor
    parsed = await resume_service.parse_resume(text, OLLAMA_URL, OLLAMA_MODEL)

    profile = _get_profile(current_user.id, session)
    profile.resume_path = str(dest_path)
    profile.resume_text = text
    profile.parsed_skills = json_encode(parsed.get("skills", []))
    profile.apply_skills = None  # new résumé: use all its skills until the user narrows them down
    profile.parsed_experience = json_encode(parsed.get("experience", []))
    profile.parsed_education = json_encode(parsed.get("education", []))
    profile.total_years = parsed.get("total_years")

    # Auto-fill contact details if missing
    contact = parsed.get("contact", {})
    if not profile.name and contact.get("name"):
        profile.name = contact["name"]
    if not profile.phone and contact.get("phone"):
        profile.phone = contact["phone"]
    if not profile.location and contact.get("location"):
        profile.location = contact["location"]
    if not profile.linkedin_url and contact.get("linkedin_url"):
        profile.linkedin_url = contact["linkedin_url"]

    profile.updated_at = datetime.utcnow()

    session.add(profile)
    session.commit()
    session.refresh(profile)

    # New résumé → search for matching jobs right away instead of waiting for the scheduler
    from app.routers.jobs import _run_search_bg
    background_tasks.add_task(_run_search_bg, current_user.id)

    return _profile_to_dict(profile)


# ---------------------------------------------------------------------------
# DELETE /resume
# ---------------------------------------------------------------------------

@router.delete("/resume")
def delete_resume(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, str]:
    profile = _get_profile(current_user.id, session)

    if profile.resume_path:
        try:
            Path(profile.resume_path).unlink(missing_ok=True)
        except Exception:
            pass

    profile.resume_path = None
    profile.resume_text = None
    profile.parsed_skills = "[]"
    profile.parsed_experience = "[]"
    profile.parsed_education = "[]"
    profile.total_years = None
    profile.updated_at = datetime.utcnow()

    session.add(profile)
    session.commit()
    return {"detail": "Resume deleted"}


# ---------------------------------------------------------------------------
# DELETE /all  (full data wipe for this user)
# ---------------------------------------------------------------------------

@router.delete("/all")
@router.delete("/data", include_in_schema=False)
def delete_all(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, str]:
    uid = current_user.id

    # Delete resume file
    profile = session.exec(select(Profile).where(Profile.user_id == uid)).first()
    if profile and profile.resume_path:
        try:
            Path(profile.resume_path).unlink(missing_ok=True)
        except Exception:
            pass

    # Delete ApplicationLog rows
    for log in session.exec(select(ActivityLog).where(ActivityLog.user_id == uid)).all():
        session.delete(log)

    # Delete Application rows
    for app in session.exec(select(Application).where(Application.user_id == uid)).all():
        session.delete(app)

    # Delete Job rows
    for job in session.exec(select(Job).where(Job.user_id == uid)).all():
        session.delete(job)

    # Reset Settings
    settings = session.exec(select(Settings).where(Settings.user_id == uid)).first()
    if settings:
        session.delete(settings)
        session.flush()
        session.add(Settings(user_id=uid))

    # Reset Profile
    if profile:
        session.delete(profile)
        session.flush()
        session.add(Profile(user_id=uid))

    session.commit()
    return {"detail": "All data deleted and reset"}
