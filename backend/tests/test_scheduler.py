"""
Tests for the scheduler's pacing logic via the pure can_apply() function.

Tests cover:
- Daily cap enforcement
- Inter-apply gap enforcement
- Apply-hour window enforcement
- Combined scenarios
- Edge cases (exact boundary values)

No real database or APScheduler instance is needed.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

import pytest

from app.services.scheduler import can_apply


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(hour: int, minute: int = 0) -> datetime:
    """Build a UTC datetime for today at the given hour:minute."""
    return datetime.now(tz=timezone.utc).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


def _ago(seconds: int, base: Optional[datetime] = None) -> datetime:
    """Return a datetime that is *seconds* before base (defaults to now)."""
    ref = base if base is not None else datetime.now(tz=timezone.utc)
    return ref - timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Daily cap
# ---------------------------------------------------------------------------

class TestDailyCap:
    def test_at_cap_returns_false(self):
        """When applied_today equals cap, do not apply."""
        assert can_apply(
            applied_today=30,
            cap=30,
            last_apply_ts=None,
            now=_utc(10),
            start_hour=9,
            end_hour=21,
        ) is False

    def test_over_cap_returns_false(self):
        assert can_apply(
            applied_today=35,
            cap=30,
            last_apply_ts=None,
            now=_utc(10),
            start_hour=9,
            end_hour=21,
        ) is False

    def test_under_cap_returns_true(self):
        assert can_apply(
            applied_today=5,
            cap=30,
            last_apply_ts=None,
            now=_utc(10),
            start_hour=9,
            end_hour=21,
        ) is True

    def test_zero_applied_zero_cap_returns_false(self):
        """A cap of 0 should always prevent applying."""
        assert can_apply(
            applied_today=0,
            cap=0,
            last_apply_ts=None,
            now=_utc(10),
            start_hour=9,
            end_hour=21,
        ) is False


# ---------------------------------------------------------------------------
# Inter-apply gap
# ---------------------------------------------------------------------------

class TestApplyGap:
    def test_gap_too_short_returns_false(self):
        """If last application was < min_gap_s ago, do not apply."""
        now = _utc(10)
        assert can_apply(
            applied_today=1,
            cap=30,
            last_apply_ts=_ago(60, base=now),   # only 60s ago
            now=now,
            start_hour=9,
            end_hour=21,
            min_gap_s=120,
        ) is False

    def test_gap_exactly_at_minimum_returns_true(self):
        """Exactly at the minimum gap should be allowed."""
        now = _utc(10)
        assert can_apply(
            applied_today=1,
            cap=30,
            last_apply_ts=_ago(120, base=now),
            now=now,
            start_hour=9,
            end_hour=21,
            min_gap_s=120,
        ) is True

    def test_gap_above_minimum_returns_true(self):
        now = _utc(10)
        assert can_apply(
            applied_today=1,
            cap=30,
            last_apply_ts=_ago(300, base=now),
            now=now,
            start_hour=9,
            end_hour=21,
            min_gap_s=120,
        ) is True

    def test_no_previous_apply_skips_gap_check(self):
        """None last_apply_ts means no gap restriction."""
        assert can_apply(
            applied_today=0,
            cap=30,
            last_apply_ts=None,
            now=_utc(10),
            start_hour=9,
            end_hour=21,
        ) is True


# ---------------------------------------------------------------------------
# Apply hours window
# ---------------------------------------------------------------------------

class TestApplyHours:
    def test_before_start_hour_returns_false(self):
        assert can_apply(
            applied_today=0,
            cap=30,
            last_apply_ts=None,
            now=_utc(7),   # before 9
            start_hour=9,
            end_hour=21,
        ) is False

    def test_at_start_hour_returns_true(self):
        assert can_apply(
            applied_today=0,
            cap=30,
            last_apply_ts=None,
            now=_utc(9),
            start_hour=9,
            end_hour=21,
        ) is True

    def test_at_end_hour_returns_false(self):
        """end_hour is exclusive."""
        assert can_apply(
            applied_today=0,
            cap=30,
            last_apply_ts=None,
            now=_utc(21),
            start_hour=9,
            end_hour=21,
        ) is False

    def test_inside_window_returns_true(self):
        assert can_apply(
            applied_today=0,
            cap=30,
            last_apply_ts=None,
            now=_utc(14),
            start_hour=9,
            end_hour=21,
        ) is True

    def test_after_end_hour_returns_false(self):
        assert can_apply(
            applied_today=0,
            cap=30,
            last_apply_ts=None,
            now=_utc(23),
            start_hour=9,
            end_hour=21,
        ) is False


# ---------------------------------------------------------------------------
# Combined scenarios
# ---------------------------------------------------------------------------

class TestCombinedConstraints:
    def test_all_constraints_met(self):
        now = _utc(12)
        assert can_apply(
            applied_today=10,
            cap=30,
            last_apply_ts=_ago(200, base=now),
            now=now,
            start_hour=9,
            end_hour=21,
        ) is True

    def test_cap_blocks_even_if_others_pass(self):
        now = _utc(12)
        assert can_apply(
            applied_today=30,
            cap=30,
            last_apply_ts=_ago(200, base=now),
            now=now,
            start_hour=9,
            end_hour=21,
        ) is False

    def test_hours_blocks_even_if_others_pass(self):
        now = _utc(22)
        assert can_apply(
            applied_today=0,
            cap=30,
            last_apply_ts=_ago(200, base=now),
            now=now,
            start_hour=9,
            end_hour=21,
        ) is False

    def test_gap_blocks_even_if_others_pass(self):
        now = _utc(12)
        assert can_apply(
            applied_today=0,
            cap=30,
            last_apply_ts=_ago(30, base=now),
            now=now,
            start_hour=9,
            end_hour=21,
            min_gap_s=120,
        ) is False
