# src/services/streak_service.py

from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

VALID_SCHEDULES = {"daily", "mon", "tue", "wed", "thu", "fri", "sat", "sun"}
WEEKDAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
MILESTONE_DAYS = {3, 7, 14, 30, 60, 100, 365}


class StreakServiceError(Exception):
    """Base error."""

class StreakValidationError(StreakServiceError):
    """Raised on invalid user input."""

class StreakNotFoundError(StreakServiceError):
    """Raised when a streak does not exist."""

class AlreadyLoggedTodayError(StreakServiceError):
    """Raised when the user tries to log twice in the same calendar day."""


@dataclass
class StreakRecord:
    id: int
    user_id: int
    name: str
    slug: str
    description: str | None
    schedule: str           # 'daily' or comma-separated weekday abbreviations
    freeze_tokens: int
    created_at: datetime


@dataclass
class StreakLog:
    id: int
    streak_id: int
    logged_at: datetime
    note: str | None
    mood: int | None        # 1–5
    tags: list[str] = field(default_factory=list)


@dataclass
class StreakStats:
    streak: StreakRecord
    current_streak: int
    best_streak: int
    total_logs: int
    last_logged: date | None
    avg_mood: float | None
    recent_logs: list[StreakLog]   # last 7


def _slugify(name: str) -> str:
    return re.sub(r"\s+", "_", name.strip().lower())


def _active_days(schedule: str) -> set[int] | None:
    """Returns a set of weekday integers (0=Mon) or None if schedule is 'daily'."""
    if schedule.strip().lower() == "daily":
        return None
    parts = [p.strip().lower() for p in schedule.split(",")]
    days = set()
    for p in parts:
        if p not in WEEKDAY_MAP:
            raise StreakValidationError(f"Unknown schedule day: '{p}'")
        days.add(WEEKDAY_MAP[p])
    return days


def _is_active_day(d: date, active_days: set[int] | None) -> bool:
    return active_days is None or d.weekday() in active_days


def compute_current_streak(
    log_dates: list[date],
    freeze_dates: set[date],
    schedule: str,
    reference: date | None = None,
) -> int:
    """
    Walk backwards from `reference` (today by default),
    counting consecutive active days that have a log OR a freeze.
    """
    if not log_dates:
        return 0

    ref = reference or date.today()
    active_days = _active_days(schedule)
    logged = set(log_dates)
    covered = logged | freeze_dates

    streak = 0
    cursor = ref

    # Allow a grace window: if today has no log yet, don't break the streak.
    # Start counting from the most recent active day that has coverage.
    while True:
        if not _is_active_day(cursor, active_days):
            cursor -= timedelta(days=1)
            continue
        if cursor in covered:
            streak += 1
            cursor -= timedelta(days=1)
        elif cursor == ref or cursor == ref - timedelta(days=1):
            # Grace: today or yesterday with no log doesn't immediately break
            cursor -= timedelta(days=1)
        else:
            break

    return streak


def compute_best_streak(
    log_dates: list[date],
    freeze_dates: set[date],
    schedule: str,
) -> int:
    """Scan the entire log history for the longest streak."""
    if not log_dates:
        return 0

    active_days = _active_days(schedule)
    covered = sorted(set(log_dates) | freeze_dates)
    best = 0
    current = 0
    prev: date | None = None

    for d in covered:
        if not _is_active_day(d, active_days):
            continue
        if prev is None:
            current = 1
        else:
            # Count how many active days lie between prev and d exclusively
            gap_active = 0
            cursor = prev + timedelta(days=1)
            while cursor < d:
                if _is_active_day(cursor, active_days):
                    gap_active += 1
                cursor += timedelta(days=1)
            if gap_active == 0:
                current += 1
            else:
                best = max(best, current)
                current = 1
        prev = d
        best = max(best, current)

    return best