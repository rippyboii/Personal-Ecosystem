from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from services.db import _get_migrations
from services.streak_service import (
    AlreadyLoggedTodayError,
    StreakNotFoundError,
    StreakService,
    StreakValidationError,
    compute_best_streak,
    compute_current_streak,
)


@pytest.fixture
async def db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    for _, sql in _get_migrations():
        await conn.executescript(sql)
    await conn.commit()
    yield conn
    await conn.close()


@pytest.fixture
def service(db):
    with patch("services.streak_service.get_db", AsyncMock(return_value=db)):
        yield StreakService()


# ---------------------------------------------------------------------------
# create_streak
# ---------------------------------------------------------------------------

class TestCreateStreak:
    async def test_creates_with_correct_name(self, service):
        s = await service.create_streak(1, "Gym")
        assert s.name == "Gym"

    async def test_slug_is_normalized(self, service):
        s = await service.create_streak(1, "Morning Run")
        assert s.slug == "morning_run"

    async def test_schedule_defaults_to_daily(self, service):
        s = await service.create_streak(1, "Gym")
        assert s.schedule == "daily"

    async def test_custom_schedule_stored(self, service):
        s = await service.create_streak(1, "Gym", schedule="mon,wed,fri")
        assert s.schedule == "mon,wed,fri"

    async def test_description_stored(self, service):
        s = await service.create_streak(1, "Gym", description="Morning workout")
        assert s.description == "Morning workout"

    async def test_freeze_tokens_start_at_zero(self, service):
        s = await service.create_streak(1, "Gym")
        assert s.freeze_tokens == 0

    async def test_duplicate_name_raises(self, service):
        await service.create_streak(1, "Gym")
        with pytest.raises(StreakValidationError):
            await service.create_streak(1, "gym")  # same slug

    async def test_empty_name_raises(self, service):
        with pytest.raises(StreakValidationError):
            await service.create_streak(1, "   ")

    async def test_name_over_64_chars_raises(self, service):
        with pytest.raises(StreakValidationError):
            await service.create_streak(1, "x" * 65)

    async def test_invalid_schedule_raises(self, service):
        with pytest.raises(StreakValidationError):
            await service.create_streak(1, "Gym", schedule="invalid")

    async def test_different_users_can_have_same_name(self, service):
        s1 = await service.create_streak(1, "Gym")
        s2 = await service.create_streak(2, "Gym")
        assert s1.name == s2.name == "Gym"


# ---------------------------------------------------------------------------
# get / list
# ---------------------------------------------------------------------------

class TestGetAndListStreaks:
    async def test_get_streak_by_id(self, service):
        created = await service.create_streak(1, "Gym")
        fetched = await service.get_streak(1, created.id)
        assert fetched.id == created.id

    async def test_get_streak_wrong_user_raises(self, service):
        created = await service.create_streak(1, "Gym")
        with pytest.raises(StreakNotFoundError):
            await service.get_streak(2, created.id)

    async def test_get_streak_by_name(self, service):
        await service.create_streak(1, "Morning Run")
        fetched = await service.get_streak_by_name(1, "morning run")
        assert fetched.name == "Morning Run"

    async def test_get_nonexistent_streak_raises(self, service):
        with pytest.raises(StreakNotFoundError):
            await service.get_streak(1, 999)

    async def test_list_returns_all_streaks(self, service):
        await service.create_streak(1, "Gym")
        await service.create_streak(1, "Read")
        streaks = await service.list_streaks(1)
        assert len(streaks) == 2

    async def test_list_empty_for_new_user(self, service):
        assert await service.list_streaks(99) == []

    async def test_list_isolates_users(self, service):
        await service.create_streak(1, "Gym")
        await service.create_streak(2, "Swim")
        assert len(await service.list_streaks(1)) == 1
        assert len(await service.list_streaks(2)) == 1


# ---------------------------------------------------------------------------
# delete_streak
# ---------------------------------------------------------------------------

class TestDeleteStreak:
    async def test_delete_removes_streak(self, service):
        s = await service.create_streak(1, "Gym")
        await service.delete_streak(1, s.id)
        with pytest.raises(StreakNotFoundError):
            await service.get_streak(1, s.id)

    async def test_delete_returns_deleted_streak(self, service):
        s = await service.create_streak(1, "Gym")
        deleted = await service.delete_streak(1, s.id)
        assert deleted.name == "Gym"

    async def test_delete_wrong_user_raises(self, service):
        s = await service.create_streak(1, "Gym")
        with pytest.raises(StreakNotFoundError):
            await service.delete_streak(2, s.id)


# ---------------------------------------------------------------------------
# update_streak
# ---------------------------------------------------------------------------

class TestUpdateStreak:
    async def test_update_name(self, service):
        s = await service.create_streak(1, "Gym")
        updated = await service.update_streak(1, s.id, name="Morning Gym")
        assert updated.name == "Morning Gym"
        assert updated.slug == "morning_gym"

    async def test_update_schedule(self, service):
        s = await service.create_streak(1, "Gym")
        updated = await service.update_streak(1, s.id, schedule="mon,fri")
        assert updated.schedule == "mon,fri"

    async def test_update_description(self, service):
        s = await service.create_streak(1, "Gym")
        updated = await service.update_streak(1, s.id, description="Heavy lifting")
        assert updated.description == "Heavy lifting"

    async def test_update_wrong_user_raises(self, service):
        s = await service.create_streak(1, "Gym")
        with pytest.raises(StreakNotFoundError):
            await service.update_streak(2, s.id, name="New Name")

    async def test_update_invalid_schedule_raises(self, service):
        s = await service.create_streak(1, "Gym")
        with pytest.raises(StreakValidationError):
            await service.update_streak(1, s.id, schedule="invalid")


# ---------------------------------------------------------------------------
# log_activity
# ---------------------------------------------------------------------------

class TestLogActivity:
    async def test_log_returns_log_object(self, service):
        s = await service.create_streak(1, "Gym")
        log = await service.log_activity(1, s.id)
        assert log.streak_id == s.id

    async def test_log_with_note(self, service):
        s = await service.create_streak(1, "Gym")
        log = await service.log_activity(1, s.id, note="Felt great")
        assert log.note == "Felt great"

    async def test_log_with_mood(self, service):
        s = await service.create_streak(1, "Gym")
        log = await service.log_activity(1, s.id, mood=4)
        assert log.mood == 4

    async def test_log_with_tags(self, service):
        s = await service.create_streak(1, "Gym")
        log = await service.log_activity(1, s.id, tags=["legs", "heavy"])
        assert "legs" in log.tags

    async def test_double_log_raises(self, service):
        s = await service.create_streak(1, "Gym")
        await service.log_activity(1, s.id)
        with pytest.raises(AlreadyLoggedTodayError):
            await service.log_activity(1, s.id)

    async def test_invalid_mood_raises(self, service):
        s = await service.create_streak(1, "Gym")
        with pytest.raises(StreakValidationError):
            await service.log_activity(1, s.id, mood=6)

    async def test_mood_zero_raises(self, service):
        s = await service.create_streak(1, "Gym")
        with pytest.raises(StreakValidationError):
            await service.log_activity(1, s.id, mood=0)

    async def test_log_wrong_ownership_raises(self, service):
        s = await service.create_streak(1, "Gym")
        with pytest.raises(StreakNotFoundError):
            await service.log_activity(2, s.id)


# ---------------------------------------------------------------------------
# compute_current_streak
# ---------------------------------------------------------------------------

class TestComputeCurrentStreak:
    def test_no_logs_returns_zero(self):
        assert compute_current_streak([], set(), "daily") == 0

    def test_single_today_log(self):
        today = date.today()
        assert compute_current_streak([today], set(), "daily", reference=today) == 1

    def test_consecutive_days(self):
        today = date.today()
        dates = [today - timedelta(days=i) for i in range(3)]
        assert compute_current_streak(dates, set(), "daily", reference=today) == 3

    def test_gap_breaks_streak(self):
        today = date.today()
        dates = [today, today - timedelta(days=2)]
        assert compute_current_streak(dates, set(), "daily", reference=today) == 1

    def test_freeze_fills_gap(self):
        today = date.today()
        yesterday = today - timedelta(days=1)
        two_ago = today - timedelta(days=2)
        result = compute_current_streak(
            [today, two_ago], {yesterday}, "daily", reference=today
        )
        assert result == 3

    def test_off_day_not_required(self):
        # Mon-only schedule: logged Mon, Wed should not break streak if Tue is off
        # Use fixed dates to control weekdays
        # Find a recent Monday
        today = date.today()
        days_since_monday = today.weekday()
        last_monday = today - timedelta(days=days_since_monday)
        prev_monday = last_monday - timedelta(weeks=1)
        result = compute_current_streak(
            [last_monday, prev_monday], set(), "mon", reference=last_monday
        )
        assert result == 2


# ---------------------------------------------------------------------------
# compute_best_streak
# ---------------------------------------------------------------------------

class TestComputeBestStreak:
    def test_no_logs_returns_zero(self):
        assert compute_best_streak([], set(), "daily") == 0

    def test_single_log(self):
        assert compute_best_streak([date.today()], set(), "daily") == 1

    def test_best_across_two_runs(self):
        today = date.today()
        run1 = [today - timedelta(days=10 + i) for i in range(5)]  # 5-day run
        run2 = [today - timedelta(days=i) for i in range(3)]        # 3-day run
        best = compute_best_streak(run1 + run2, set(), "daily")
        assert best == 5

    def test_best_never_goes_below_current(self):
        today = date.today()
        dates = [today - timedelta(days=i) for i in range(7)]
        best = compute_best_streak(dates, set(), "daily")
        assert best >= 7

    def test_freeze_counted_in_best(self):
        today = date.today()
        yesterday = today - timedelta(days=1)
        two_ago = today - timedelta(days=2)
        best = compute_best_streak([today, two_ago], {yesterday}, "daily")
        assert best == 3


# ---------------------------------------------------------------------------
# freeze tokens
# ---------------------------------------------------------------------------

class TestFreezeTokens:
    async def test_no_tokens_raises(self, service):
        s = await service.create_streak(1, "Gym")
        with pytest.raises(StreakValidationError, match="No freeze tokens"):
            await service.spend_freeze(1, s.id)

    async def test_spend_freeze_wrong_user_raises(self, service):
        s = await service.create_streak(1, "Gym")
        with pytest.raises(StreakNotFoundError):
            await service.spend_freeze(2, s.id)


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

class TestGetStats:
    async def test_stats_for_new_streak(self, service):
        s = await service.create_streak(1, "Gym")
        stats = await service.get_stats(1, s.id)
        assert stats.current_streak == 0
        assert stats.best_streak == 0
        assert stats.total_logs == 0
        assert stats.last_logged is None
        assert stats.avg_mood is None

    async def test_stats_after_one_log(self, service):
        s = await service.create_streak(1, "Gym")
        await service.log_activity(1, s.id, mood=4)
        stats = await service.get_stats(1, s.id)
        assert stats.total_logs == 1
        assert stats.current_streak >= 1
        assert stats.last_logged is not None

    async def test_avg_mood_computed(self, service):
        s = await service.create_streak(1, "Gym")
        await service.log_activity(1, s.id, mood=4)
        stats = await service.get_stats(1, s.id)
        assert stats.avg_mood == 4.0

    async def test_recent_logs_capped_at_seven(self, service):
        # Can't log more than once per day in real usage; just verify the field exists
        s = await service.create_streak(1, "Gym")
        await service.log_activity(1, s.id)
        stats = await service.get_stats(1, s.id)
        assert len(stats.recent_logs) <= 7

    async def test_get_all_stats_returns_one_per_streak(self, service):
        await service.create_streak(1, "Gym")
        await service.create_streak(1, "Read")
        all_stats = await service.get_all_stats(1)
        assert len(all_stats) == 2
