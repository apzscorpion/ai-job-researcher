"""Tests for skill selection/matching, résumé relevance, routing and account-safety pacing."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models import Job, Profile
from app.services.browser_apply import pick_option, profile_value, same_place
from app.services.mailer import usable_recruiter_email
from app.services.matcher import selected_skills, skill_match
from app.services.scheduler import active_pauses, pause_platform, platform_ready, route_for
from app.services.site_apply import detect_ats, form_url
from app.services.sources import relevant, resume_titles


def _profile(**kw) -> Profile:
    return Profile(user_id=1, **kw)


def test_selected_skills_defaults_to_all_and_drops_removed_ones():
    p = _profile(parsed_skills=json.dumps(["React", "Vue.js", "Jest", "Docker"]))
    assert selected_skills(p) == ["React", "Vue.js", "Jest", "Docker"]
    p.apply_skills = json.dumps(["react", "Docker", "Angular"])  # Angular was removed from the profile
    assert selected_skills(p) == ["React", "Docker"]
    assert selected_skills(None) == []


def test_skill_match_green_red_with_aliases():
    matched, missing = skill_match(["React.js", "TypeScript", "Jest"],
                                   "We need React, TypeScript, Redux and GraphQL. Go to our site.")
    assert matched == ["TypeScript", "React"]
    assert "Redux" in missing and "GraphQL" in missing
    assert "Jest" not in matched + missing  # not asked for by the job
    assert "Go" not in missing  # ambiguous English word is ignored
    matched, missing = skill_match(["HTML5", "CSS3"], "Strong HTML and CSS skills")
    assert matched == ["HTML", "CSS"] and missing == []  # shown in the job's own wording


def test_resume_titles_and_relevance():
    p = _profile(parsed_skills=json.dumps(["React", "TypeScript", "Vue.js", "Node.js"]),
                 parsed_experience=json.dumps([{"title": "Senior Frontend Developer"},
                                               {"title": "senior frontend developer"},
                                               {"title": "Web Developer"}, {"title": "Intern"}]))
    titles = resume_titles(p)
    assert titles == ["Senior Frontend Developer", "Web Developer"]
    skills = selected_skills(p)
    assert relevant({"title": "Front-End Engineer", "description": ""}, titles, skills)
    assert relevant({"title": "Software Engineer", "description": "React, TypeScript and Node.js daily"}, titles, skills)
    assert not relevant({"title": "Software Engineer", "description": "Java and Spring"}, titles, skills)
    assert resume_titles(_profile(parsed_skills=json.dumps(["Python"]))) == ["Python Developer"]


def test_usable_recruiter_email():
    assert usable_recruiter_email("careers@acme.io")
    assert usable_recruiter_email("jane.doe+jobs@acme.co.uk")
    for bad in ("noreply@acme.io", "privacy@acme.io", "do-not-reply@x.com", "not-an-email", None, "",
                "ethiquegroup@meritis.fr", ".external@agcocorp.com", "compliance@acme.io",
                "hiring@email.com", "hrbenefits@acme.io", "paramountaccommodations@paramount.com"):
        assert not usable_recruiter_email(bad)


def test_detect_ats_and_form_url():
    assert detect_ats("https://boards.greenhouse.io/acme/jobs/123") == "greenhouse"
    assert detect_ats("https://job-boards.greenhouse.io/acme/jobs/123") == "greenhouse"
    assert detect_ats("https://jobs.lever.co/acme/abc") == "lever"
    assert detect_ats("https://acme.com/careers") is None
    assert form_url("https://jobs.lever.co/acme/abc?src=x", "lever") == "https://jobs.lever.co/acme/abc/apply"
    assert form_url("https://jobs.ashbyhq.com/acme/abc/", "ashby") == "https://jobs.ashbyhq.com/acme/abc/application"
    assert form_url("https://boards.greenhouse.io/acme/jobs/1", "greenhouse") == "https://boards.greenhouse.io/acme/jobs/1"


def test_platform_pacing_and_pauses():
    now = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
    assert platform_ready("linkedin", {}, 0, None, now, 600)
    assert not platform_ready("linkedin", {}, 15, None, now, 600)  # daily cap
    assert not platform_ready("linkedin", {}, 3, now - timedelta(minutes=5), now, 600)  # gap not over
    assert platform_ready("linkedin", {}, 3, now - timedelta(minutes=11), now, 600)
    assert not platform_ready("linkedin", {"linkedin": "logged out"}, 0, None, now, 600)

    settings = SimpleNamespace(platform_pauses="{}")
    pause_platform(settings, "linkedin", "LinkedIn is logged out. Reconnect it.", now)
    pause_platform(settings, "naukri", "Naukri showed a captcha.", now)
    assert set(active_pauses(settings.platform_pauses, now)) == {"linkedin", "naukri"}
    assert set(active_pauses(settings.platform_pauses, now + timedelta(days=2))) == {"linkedin"}  # captcha: 24 h


def test_route_for():
    job = Job(user_id=1, title="t", company="c", url="u", source="linkedin")
    assert route_for(job) == "linkedin"
    job.recruiter_email = "privacy@c.com"
    assert route_for(job) == "linkedin"
    job.recruiter_email = "hr@c.com"
    assert route_for(job) == "email"
    assert route_for(Job(user_id=1, title="t", company="c", url="u", source="remotive")) == "site"


def test_same_place_rejects_namesakes():
    assert same_place("Kochi, Kerala, India", "Kochi, Kerala")
    assert same_place("Pathanamthitta, Kerala, India", "Pathanamthitta, Kerala")
    assert not same_place("Kerala, Gujarat, India", "Pathanamthitta, Kerala")
    assert not same_place("Kerala Estate, Kerala, India", "Pathanamthitta, Kerala")
    assert not same_place("Kōchi, Kochi Prefecture, Japan", "Kochi, Kerala")


def test_pick_option_and_profile_value():
    assert pick_option("Yes", ["Yes", "No"]) == "Yes"
    assert pick_option("no", ["Yes", "No"]) == "No"
    assert pick_option("5+ years", ["0-2 years", "3-4 years", "5+ years"]) == "5+ years"
    assert pick_option("maybe", ["Yes", "No"]) == ""
    p = _profile(name="Asha Rao", phone="+91 1", linkedin_url="https://linkedin.com/in/a", location="Kochi")
    extra = {"email": "a@b.co"}
    assert profile_value("First Name *", p, extra) == "Asha"
    assert profile_value("Last name", p, extra) == "Rao"
    assert profile_value("Full name *", p, extra) == "Asha Rao"
    assert profile_value("Email address", p, extra) == "a@b.co"
    assert profile_value("LinkedIn Profile", p, extra) == "https://linkedin.com/in/a"
    assert profile_value("Why do you want to join?", p, extra) == ""


def test_region_filters():
    from sqlmodel import Session, SQLModel, create_engine, select

    from app.services.regions import region_clause

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    rows = {"Kochi": "Kochi", "Hyd": "Hyderabad, Telangana, India", "Seoul": "Seoul, South Korea",
            "Indy": "Indianapolis, Indiana, USA", "SLC": "Salt Lake City, UT, USA", "TP": "Trivandrum"}
    with Session(engine) as s:
        for title, loc in rows.items():
            s.add(Job(user_id=1, title=title, company="c", url=title, source="x", location=loc,
                      description="Office at Technopark Phase III" if title == "TP" else "Salt Lake City office"))
        s.commit()

        def titles(region):
            return {j.title for j in s.exec(select(Job).where(region_clause(region))).all()}

        assert titles("india") == {"Kochi", "Hyd", "TP"}
        assert titles("international") == {"Seoul", "Indy", "SLC"}
        assert titles("state:Kerala") == {"Kochi", "TP"}
        assert titles("park:Technopark (Trivandrum)") == {"TP"}
        assert titles("park:Salt Lake Sector V (Kolkata)") == set()
    assert region_clause("state:Atlantis") is None


def test_email_attaches_resume_and_cover_letter(tmp_path, monkeypatch):
    import smtplib

    from app.services import mailer

    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF")
    monkeypatch.setattr("app.config.DATA_DIR", tmp_path)
    cover = mailer.cover_letter_docx("Dear team,\n\nI build UIs.", Job(id=7, user_id=1, title="t", company="c",
                                                                        url="u", source="x"), _profile(name="Asha Rao"))
    assert cover.endswith("Cover_Letter_Asha_Rao.docx")
    sent = {}

    class FakeSMTP:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, *a): pass
        def sendmail(self, frm, to, raw): sent["raw"] = raw

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr("app.security.check_public_host", lambda *a: None)
    assert mailer.send_email("hr@c.com", "s", "b", str(resume), "h", 587, "u", "p", "me@x.com", True, [cover])
    assert 'filename="cv.pdf"' in sent["raw"] and "Cover_Letter_Asha_Rao.docx" in sent["raw"]


def test_apply_options():
    from app.services.scheduler import apply_options

    job = Job(user_id=1, title="t", company="c", url="u", source="linkedin")
    assert apply_options(job) == ["linkedin"]
    job.recruiter_email, job.apply_url = "hr@c.com", "https://jobs.lever.co/c/1"
    assert apply_options(job) == ["email", "linkedin", "site"]
    assert apply_options(Job(user_id=1, title="t", company="c", url="u", source="remotive")) == ["site"]


def test_scraped_email_prefers_hiring_address_and_drops_trailing_dot():
    from app.services.sources import _job

    j = _job("Dev", "Acme", "u", "x", description="Questions: privacy@acme.io. Apply: jobs@acme.io.")
    assert j["recruiter_email"] == "jobs@acme.io"
    assert _job("Dev", "A", "u", "x", description="via @agcocorp.com or .external@agcocorp.com")["recruiter_email"] is None
