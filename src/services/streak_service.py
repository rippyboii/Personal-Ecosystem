# src/services/streak_service.py

from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Sequence
from services.db import get_db

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




class StreakService:

    # ------------------------------------------------------------------
    # Streak CRUD
    # ------------------------------------------------------------------

    async def create_streak(
        self,
        user_id: int,
        name: str,
        description: str | None = None,
        schedule: str = "daily",
    ) -> StreakRecord:
        name = name.strip()
        if not name:
            raise StreakValidationError("Streak name cannot be empty.")
        if len(name) > 64:
            raise StreakValidationError("Streak name is too long (max 64 chars).")

        schedule = schedule.strip().lower() or "daily"
        _active_days(schedule)   # validates; raises StreakValidationError if bad

        slug = _slugify(name)
        now = datetime.now(timezone.utc).isoformat()

        db = await get_db()
        try:
            async with db.execute(
                """
                INSERT INTO streaks (user_id, name, slug, description, schedule, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, name, slug, description, schedule, now),
            ) as cur:
                streak_id = cur.lastrowid
            await db.commit()
        except Exception as e:
            if "UNIQUE" in str(e):
                raise StreakValidationError(
                    f"You already have a streak called '{name}'."
                ) from e
            raise StreakServiceError("Failed to create streak.") from e

        return StreakRecord(
            id=streak_id,
            user_id=user_id,
            name=name,
            slug=slug,
            description=description,
            schedule=schedule,
            freeze_tokens=0,
            created_at=datetime.fromisoformat(now),
        )

    async def get_streak(self, user_id: int, streak_id: int) -> StreakRecord:
        db = await get_db()
        async with db.execute(
            "SELECT * FROM streaks WHERE id = ? AND user_id = ?",
            (streak_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise StreakNotFoundError(f"Streak #{streak_id} not found.")
        return _row_to_streak(row)

    async def get_streak_by_name(self, user_id: int, name: str) -> StreakRecord:
        slug = _slugify(name)
        db = await get_db()
        async with db.execute(
            "SELECT * FROM streaks WHERE user_id = ? AND slug = ?",
            (user_id, slug),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise StreakNotFoundError(f"No streak named '{name}' found.")
        return _row_to_streak(row)

    async def list_streaks(self, user_id: int) -> list[StreakRecord]:
        db = await get_db()
        async with db.execute(
            "SELECT * FROM streaks WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_streak(r) for r in rows]

    async def update_streak(
        self,
        user_id: int,
        streak_id: int,
        name: str | None = None,
        description: str | None = None,
        schedule: str | None = None,
    ) -> StreakRecord:
        streak = await self.get_streak(user_id, streak_id)
        db = await get_db()

        new_name = name.strip() if name else streak.name
        new_slug = _slugify(new_name)
        new_desc = description if description is not None else streak.description
        new_sched = schedule.strip().lower() if schedule else streak.schedule
        _active_days(new_sched)  # validate

        await db.execute(
            """
            UPDATE streaks
            SET name = ?, slug = ?, description = ?, schedule = ?
            WHERE id = ? AND user_id = ?
            """,
            (new_name, new_slug, new_desc, new_sched, streak_id, user_id),
        )
        await db.commit()
        return await self.get_streak(user_id, streak_id)

    async def delete_streak(self, user_id: int, streak_id: int) -> StreakRecord:
        streak = await self.get_streak(user_id, streak_id)
        db = await get_db()
        await db.execute(
            "DELETE FROM streaks WHERE id = ? AND user_id = ?",
            (streak_id, user_id),
        )
        await db.commit()
        return streak

    async def log_activity(
        self,
        user_id: int,
        streak_id: int,
        note: str | None = None,
        mood: int | None = None,
        tags: list[str] | None = None,
    ) -> StreakLog:
        await self.get_streak(user_id, streak_id)  # ownership check

        if mood is not None and not (1 <= mood <= 5):
            raise StreakValidationError("Mood must be between 1 and 5.")

        now = datetime.now(timezone.utc)
        today = now.date()

        # Prevent double-logging the same calendar day
        db = await get_db()
        async with db.execute(
            """
            SELECT id FROM streak_logs
            WHERE streak_id = ? AND DATE(logged_at) = ?
            """,
            (streak_id, today.isoformat()),
        ) as cur:
            if await cur.fetchone():
                raise AlreadyLoggedTodayError(
                    "You already logged this streak today."
                )

        tags_str = ",".join(t.strip().lower() for t in (tags or []) if t.strip())

        async with db.execute(
            """
            INSERT INTO streak_logs (streak_id, logged_at, note, mood, tags)
            VALUES (?, ?, ?, ?, ?)
            """,
            (streak_id, now.isoformat(), note, mood, tags_str or None),
        ) as cur:
            log_id = cur.lastrowid
        await db.commit()

        # Award freeze token every 7 consecutive days
        stats = await self.get_stats(user_id, streak_id)
        if stats.current_streak > 0 and stats.current_streak % 7 == 0:
            await db.execute(
                "UPDATE streaks SET freeze_tokens = freeze_tokens + 1 WHERE id = ?",
                (streak_id,),
            )
            await db.commit()

        return StreakLog(
            id=log_id,
            streak_id=streak_id,
            logged_at=now,
            note=note,
            mood=mood,
            tags=tags_str.split(",") if tags_str else [],
        )

    # ------------------------------------------------------------------
    # Freeze tokens
    # ------------------------------------------------------------------

    async def spend_freeze(self, user_id: int, streak_id: int) -> StreakRecord:
        streak = await self.get_streak(user_id, streak_id)
        if streak.freeze_tokens < 1:
            raise StreakValidationError("No freeze tokens available.")

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        db = await get_db()
        await db.execute(
            "INSERT INTO streak_freezes (streak_id, frozen_for) VALUES (?, ?)",
            (streak_id, yesterday),
        )
        await db.execute(
            "UPDATE streaks SET freeze_tokens = freeze_tokens - 1 WHERE id = ?",
            (streak_id,),
        )
        await db.commit()
        return await self.get_streak(user_id, streak_id)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def get_stats(self, user_id: int, streak_id: int) -> StreakStats:
        streak = await self.get_streak(user_id, streak_id)
        db = await get_db()

        async with db.execute(
            "SELECT DATE(logged_at) as d FROM streak_logs WHERE streak_id = ? ORDER BY d",
            (streak_id,),
        ) as cur:
            rows = await cur.fetchall()
        log_dates = [date.fromisoformat(r["d"]) for r in rows]

        async with db.execute(
            "SELECT frozen_for FROM streak_freezes WHERE streak_id = ?",
            (streak_id,),
        ) as cur:
            freeze_rows = await cur.fetchall()
        freeze_dates = {date.fromisoformat(r["frozen_for"]) for r in freeze_rows}

        async with db.execute(
            """
            SELECT AVG(mood) as avg_mood FROM streak_logs
            WHERE streak_id = ? AND mood IS NOT NULL
            """,
            (streak_id,),
        ) as cur:
            mood_row = await cur.fetchone()

        async with db.execute(
            """
            SELECT * FROM streak_logs WHERE streak_id = ?
            ORDER BY logged_at DESC LIMIT 7
            """,
            (streak_id,),
        ) as cur:
            recent_rows = await cur.fetchall()

        current = compute_current_streak(log_dates, freeze_dates, streak.schedule)
        best = compute_best_streak(log_dates, freeze_dates, streak.schedule)

        return StreakStats(
            streak=streak,
            current_streak=current,
            best_streak=best,
            total_logs=len(log_dates),
            last_logged=log_dates[-1] if log_dates else None,
            avg_mood=round(mood_row["avg_mood"], 1) if mood_row["avg_mood"] else None,
            recent_logs=[_row_to_log(r) for r in recent_rows],
        )

    async def get_all_stats(self, user_id: int) -> list[StreakStats]:
        streaks = await self.list_streaks(user_id)
        return [await self.get_stats(user_id, s.id) for s in streaks]


# ------------------------------------------------------------------
# Row helpers
# ------------------------------------------------------------------

def _row_to_streak(row: aiosqlite.Row) -> StreakRecord:
    return StreakRecord(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        schedule=row["schedule"],
        freeze_tokens=row["freeze_tokens"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )

def _row_to_log(row: aiosqlite.Row) -> StreakLog:
    tags_raw = row["tags"]
    return StreakLog(
        id=row["id"],
        streak_id=row["streak_id"],
        logged_at=datetime.fromisoformat(row["logged_at"]),
        note=row["note"],
        mood=row["mood"],
        tags=tags_raw.split(",") if tags_raw else [],
    )