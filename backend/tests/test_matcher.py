"""
Tests for app.services.matcher.hard_filter.

Uses SQLModel model_validate to build in-memory Job and Settings instances
without requiring a live database connection.
"""

from __future__ import annotations

import json
import pytest

from app.models import Job, Settings
from app.services.matcher import hard_filter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_job(**overrides) -> Job:
    """Build a Job instance with sensible defaults."""
    defaults = dict(
        id=1,
        user_id=1,
        title="Software Engineer",
        company="Acme Corp",
        location="Kochi",
        url="https://example.com/job/1",
        source="linkedin",
        description="We need a Python developer with FastAPI experience.",
        salary_min=None,
        salary_max=None,
        company_rating=None,
        recruiter_email=None,
        score=None,
        score_reason=None,
        embedding=None,
        status="new",
    )
    defaults.update(overrides)
    return Job.model_validate(defaults)


def make_settings(**overrides) -> Settings:
    """Build a Settings instance with permissive defaults."""
    defaults = dict(
        id=1,
        user_id=1,
        smtp_host=None,
        smtp_port=587,
        smtp_user=None,
        smtp_password_enc=None,
        smtp_from=None,
        smtp_tls=True,
        gemini_key_enc=None,
        target_titles="[]",
        target_locations="[]",
        remote_ok=True,
        min_salary=0,
        min_rating=0.0,
        excluded_companies="[]",
        excluded_keywords="[]",
        ignore_degree=False,
        ignore_yoe_gap=False,
        yoe_tolerance=2,
        partial_skills_ok=False,
        score_threshold=7,
        use_gemini_scoring=False,
        daily_cap=30,
        apply_start_hour=9,
        apply_end_hour=21,
        search_interval_hours=6,
        auto_apply_enabled=True,
    )
    defaults.update(overrides)
    return Settings.model_validate(defaults)


# ---------------------------------------------------------------------------
# Salary filter
# ---------------------------------------------------------------------------

class TestSalaryFilter:
    def test_salary_below_minimum_is_rejected(self):
        job = make_job(salary_min=30_000)
        settings = make_settings(min_salary=50_000)
        assert hard_filter(job, settings) is False

    def test_salary_at_minimum_passes(self):
        job = make_job(salary_min=50_000)
        settings = make_settings(min_salary=50_000)
        assert hard_filter(job, settings) is True

    def test_salary_above_minimum_passes(self):
        job = make_job(salary_min=80_000)
        settings = make_settings(min_salary=50_000)
        assert hard_filter(job, settings) is True

    def test_none_salary_passes_when_min_salary_zero(self):
        """If the job has no salary info, it should not be filtered out when min_salary=0."""
        job = make_job(salary_min=None)
        settings = make_settings(min_salary=0)
        assert hard_filter(job, settings) is True

    def test_none_salary_passes_when_min_salary_nonzero(self):
        """Job with no salary info should not be filtered out even when min_salary is set."""
        job = make_job(salary_min=None)
        settings = make_settings(min_salary=50_000)
        assert hard_filter(job, settings) is True


# ---------------------------------------------------------------------------
# Rating filter
# ---------------------------------------------------------------------------

class TestRatingFilter:
    def test_rating_below_threshold_is_rejected(self):
        job = make_job(company_rating=2.5)
        settings = make_settings(min_rating=3.0)
        assert hard_filter(job, settings) is False

    def test_rating_at_threshold_passes(self):
        job = make_job(company_rating=3.0)
        settings = make_settings(min_rating=3.0)
        assert hard_filter(job, settings) is True

    def test_none_rating_passes(self):
        job = make_job(company_rating=None)
        settings = make_settings(min_rating=3.0)
        assert hard_filter(job, settings) is True


# ---------------------------------------------------------------------------
# Excluded companies
# ---------------------------------------------------------------------------

class TestExcludedCompanies:
    def test_excluded_company_is_rejected(self):
        job = make_job(company="BadCorp")
        settings = make_settings(excluded_companies=json.dumps(["badcorp", "evilco"]))
        assert hard_filter(job, settings) is False

    def test_excluded_company_case_insensitive(self):
        job = make_job(company="BADCORP")
        settings = make_settings(excluded_companies=json.dumps(["badcorp"]))
        assert hard_filter(job, settings) is False

    def test_non_excluded_company_passes(self):
        job = make_job(company="GoodCorp")
        settings = make_settings(excluded_companies=json.dumps(["badcorp"]))
        assert hard_filter(job, settings) is True

    def test_empty_exclusion_list_passes_all(self):
        job = make_job(company="AnyCompany")
        settings = make_settings(excluded_companies="[]")
        assert hard_filter(job, settings) is True


# ---------------------------------------------------------------------------
# Excluded keywords
# ---------------------------------------------------------------------------

class TestExcludedKeywords:
    def test_keyword_in_description_is_rejected(self):
        job = make_job(description="This role requires C++ and Rust experience.")
        settings = make_settings(excluded_keywords=json.dumps(["C++"]))
        assert hard_filter(job, settings) is False

    def test_keyword_in_title_is_rejected(self):
        job = make_job(title="Java Backend Engineer")
        settings = make_settings(excluded_keywords=json.dumps(["java"]))
        assert hard_filter(job, settings) is False

    def test_keyword_case_insensitive(self):
        job = make_job(description="We use COBOL for everything.")
        settings = make_settings(excluded_keywords=json.dumps(["cobol"]))
        assert hard_filter(job, settings) is False

    def test_no_excluded_keyword_passes(self):
        job = make_job(description="Python FastAPI microservices.")
        settings = make_settings(excluded_keywords=json.dumps(["COBOL"]))
        assert hard_filter(job, settings) is True


# ---------------------------------------------------------------------------
# Relaxation flags do NOT affect hard_filter
# ---------------------------------------------------------------------------

class TestRelaxationFlags:
    def test_partial_skills_ok_does_not_affect_hard_filter(self):
        """partial_skills_ok controls LLM prompt only — hard_filter should still pass."""
        job = make_job(salary_min=None, company_rating=None)
        settings = make_settings(partial_skills_ok=True, min_salary=0, min_rating=0.0)
        assert hard_filter(job, settings) is True

    def test_ignore_degree_does_not_affect_hard_filter(self):
        job = make_job()
        settings = make_settings(ignore_degree=True)
        assert hard_filter(job, settings) is True

    def test_combined_pass(self):
        """A well-matching job with no exclusions should always pass."""
        job = make_job(salary_min=60_000, company_rating=4.2, company="TechCo")
        settings = make_settings(
            min_salary=50_000,
            min_rating=3.5,
            excluded_companies=json.dumps(["badcorp"]),
            excluded_keywords=json.dumps(["COBOL"]),
        )
        assert hard_filter(job, settings) is True
