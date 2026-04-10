from datetime import datetime, timedelta, timezone

import pytest

from services.reminder_service import (
    ReminderItem,
    ReminderNotFoundError,
    ReminderService,
    ReminderServiceError,
    ReminderValidationError,
)


def future(hours: float = 2) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


# ---------------------------------------------------------------------------
# add_reminder
# ---------------------------------------------------------------------------

class TestAddReminder:
    def setup_method(self):
        self.service = ReminderService()

    def test_returns_item_with_correct_text(self):
        r = self.service.add_reminder(1, "Buy milk", future())
        assert r.reminder == "Buy milk"

    def test_id_starts_at_one(self):
        r = self.service.add_reminder(1, "Task", future())
        assert r.id == 1

    def test_ids_increment_per_user(self):
        r1 = self.service.add_reminder(1, "First", future())
        r2 = self.service.add_reminder(1, "Second", future())
        assert r1.id == 1
        assert r2.id == 2

    def test_ids_are_independent_across_users(self):
        r1 = self.service.add_reminder(1, "User 1", future())
        r2 = self.service.add_reminder(2, "User 2", future())
        assert r1.id == 1
        assert r2.id == 1

    def test_strips_whitespace(self):
        r = self.service.add_reminder(1, "  Buy milk  ", future())
        assert r.reminder == "Buy milk"

    def test_repeat_defaults_to_none(self):
        r = self.service.add_reminder(1, "Task", future())
        assert r.repeat == "none"

    def test_repeat_stored(self):
        r = self.service.add_reminder(1, "Task", future(), repeat="daily")
        assert r.repeat == "daily"

    def test_due_at_normalized_to_utc(self):
        from zoneinfo import ZoneInfo
        local_dt = datetime(2030, 6, 1, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        r = self.service.add_reminder(1, "Task", local_dt)
        assert r.due_at.tzinfo == timezone.utc

    def test_created_at_is_set(self):
        before = datetime.now(timezone.utc)
        r = self.service.add_reminder(1, "Task", future())
        after = datetime.now(timezone.utc)
        assert before <= r.created_at <= after

    def test_reminded_24h_at_defaults_to_none(self):
        r = self.service.add_reminder(1, "Task", future())
        assert r.reminded_24h_at is None

    def test_fired_at_defaults_to_none(self):
        r = self.service.add_reminder(1, "Task", future())
        assert r.fired_at is None

    def test_paused_repeat_defaults_to_none(self):
        r = self.service.add_reminder(1, "Task", future())
        assert r.paused_repeat is None

    def test_empty_text_raises(self):
        with pytest.raises(ReminderValidationError):
            self.service.add_reminder(1, "", future())

    def test_whitespace_only_text_raises(self):
        with pytest.raises(ReminderValidationError):
            self.service.add_reminder(1, "   ", future())

    def test_text_over_200_chars_raises(self):
        with pytest.raises(ReminderValidationError):
            self.service.add_reminder(1, "x" * 201, future())

    def test_text_at_200_chars_succeeds(self):
        r = self.service.add_reminder(1, "x" * 200, future())
        assert len(r.reminder) == 200

    def test_past_due_at_raises(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        with pytest.raises(ReminderValidationError):
            self.service.add_reminder(1, "Task", past)

    def test_invalid_repeat_raises(self):
        with pytest.raises(ReminderValidationError):
            self.service.add_reminder(1, "Task", future(), repeat="hourly")

    @pytest.mark.parametrize("repeat", ["none", "daily", "weekly", "monthly", "yearly"])
    def test_all_valid_repeat_values_accepted(self, repeat):
        r = self.service.add_reminder(1, "Task", future(), repeat=repeat)
        assert r.repeat == repeat

    def test_repeat_case_insensitive(self):
        r = self.service.add_reminder(1, "Task", future(), repeat="DAILY")
        assert r.repeat == "daily"


# ---------------------------------------------------------------------------
# list_reminders
# ---------------------------------------------------------------------------

class TestListReminders:
    def setup_method(self):
        self.service = ReminderService()

    def test_empty_for_new_user(self):
        assert self.service.list_reminders(1) == []

    def test_returns_all_added(self):
        self.service.add_reminder(1, "First", future())
        self.service.add_reminder(1, "Second", future())
        assert len(self.service.list_reminders(1)) == 2

    def test_isolated_per_user(self):
        self.service.add_reminder(1, "User 1", future())
        self.service.add_reminder(2, "User 2", future())
        assert len(self.service.list_reminders(1)) == 1
        assert len(self.service.list_reminders(2)) == 1

    def test_returns_a_copy(self):
        self.service.add_reminder(1, "Task", future())
        items = self.service.list_reminders(1)
        items.clear()
        assert len(self.service.list_reminders(1)) == 1

    def test_order_matches_insertion(self):
        self.service.add_reminder(1, "First", future())
        self.service.add_reminder(1, "Second", future())
        items = self.service.list_reminders(1)
        assert items[0].reminder == "First"
        assert items[1].reminder == "Second"


# ---------------------------------------------------------------------------
# delete_reminder
# ---------------------------------------------------------------------------

class TestDeleteReminder:
    def setup_method(self):
        self.service = ReminderService()

    def test_removes_the_reminder(self):
        r = self.service.add_reminder(1, "Task", future())
        self.service.delete_reminder(1, r.id)
        assert self.service.list_reminders(1) == []

    def test_returns_the_removed_item(self):
        r = self.service.add_reminder(1, "Task", future())
        removed = self.service.delete_reminder(1, r.id)
        assert removed.reminder == "Task"

    def test_only_removes_target(self):
        self.service.add_reminder(1, "Keep A", future())
        r2 = self.service.add_reminder(1, "Remove", future())
        self.service.add_reminder(1, "Keep B", future())
        self.service.delete_reminder(1, r2.id)
        remaining = self.service.list_reminders(1)
        assert len(remaining) == 2
        assert all(r.id != r2.id for r in remaining)

    def test_not_found_raises(self):
        with pytest.raises(ReminderNotFoundError):
            self.service.delete_reminder(1, 999)

    def test_wrong_user_raises(self):
        r = self.service.add_reminder(1, "Task", future())
        with pytest.raises(ReminderNotFoundError):
            self.service.delete_reminder(2, r.id)

    def test_deleting_last_reminder_resets_id_counter(self):
        r = self.service.add_reminder(1, "Task", future())
        self.service.delete_reminder(1, r.id)
        new_r = self.service.add_reminder(1, "New", future())
        assert new_r.id == 1


# ---------------------------------------------------------------------------
# mark_fired
# ---------------------------------------------------------------------------

class TestMarkFired:
    def setup_method(self):
        self.service = ReminderService()

    def test_stamps_fired_at(self):
        r = self.service.add_reminder(1, "Task", future())
        before = datetime.now(timezone.utc)
        self.service.mark_fired(1, r.id)
        after = datetime.now(timezone.utc)
        assert before <= r.fired_at <= after

    def test_with_explicit_timestamp(self):
        r = self.service.add_reminder(1, "Task", future())
        ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.service.mark_fired(1, r.id, fired_at=ts)
        assert r.fired_at == ts

    def test_not_found_raises(self):
        with pytest.raises(ReminderNotFoundError):
            self.service.mark_fired(1, 999)

    def test_returns_the_reminder(self):
        r = self.service.add_reminder(1, "Task", future())
        result = self.service.mark_fired(1, r.id)
        assert result is r


# ---------------------------------------------------------------------------
# mark_24h_reminded
# ---------------------------------------------------------------------------

class TestMark24hReminded:
    def setup_method(self):
        self.service = ReminderService()

    def test_stamps_reminded_24h_at(self):
        r = self.service.add_reminder(1, "Task", future())
        before = datetime.now(timezone.utc)
        self.service.mark_24h_reminded(1, r.id)
        after = datetime.now(timezone.utc)
        assert before <= r.reminded_24h_at <= after

    def test_with_explicit_timestamp(self):
        r = self.service.add_reminder(1, "Task", future())
        ts = datetime(2025, 3, 1, tzinfo=timezone.utc)
        self.service.mark_24h_reminded(1, r.id, reminded_at=ts)
        assert r.reminded_24h_at == ts

    def test_not_found_raises(self):
        with pytest.raises(ReminderNotFoundError):
            self.service.mark_24h_reminded(1, 999)

    def test_returns_the_reminder(self):
        r = self.service.add_reminder(1, "Task", future())
        result = self.service.mark_24h_reminded(1, r.id)
        assert result is r


# ---------------------------------------------------------------------------
# update_reminder
# ---------------------------------------------------------------------------

class TestUpdateReminder:
    def setup_method(self):
        self.service = ReminderService()
        self.r = self.service.add_reminder(1, "Original", future(hours=10))
        self.service.mark_24h_reminded(1, self.r.id)
        self.service.mark_fired(1, self.r.id)

    def test_updates_text(self):
        self.service.update_reminder(1, self.r.id, reminder_text="New text")
        assert self.r.reminder == "New text"

    def test_updates_repeat(self):
        self.service.update_reminder(1, self.r.id, repeat="weekly")
        assert self.r.repeat == "weekly"

    def test_new_due_at_clears_reminded_and_fired(self):
        self.service.update_reminder(1, self.r.id, due_at=future(hours=48))
        assert self.r.reminded_24h_at is None
        assert self.r.fired_at is None

    def test_same_due_at_does_not_clear_flags(self):
        original_fired = self.r.fired_at
        self.service.update_reminder(1, self.r.id, due_at=self.r.due_at)
        assert self.r.fired_at == original_fired

    def test_due_at_in_past_raises(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        with pytest.raises(ReminderValidationError):
            self.service.update_reminder(1, self.r.id, due_at=past)

    def test_invalid_repeat_raises(self):
        with pytest.raises(ReminderValidationError):
            self.service.update_reminder(1, self.r.id, repeat="hourly")

    def test_not_found_raises(self):
        with pytest.raises(ReminderNotFoundError):
            self.service.update_reminder(1, 999, reminder_text="x")

    def test_returns_the_reminder(self):
        result = self.service.update_reminder(1, self.r.id, reminder_text="Updated")
        assert result is self.r

    def test_no_args_keeps_existing_values(self):
        original_text = self.r.reminder
        self.service.update_reminder(1, self.r.id)
        assert self.r.reminder == original_text


# ---------------------------------------------------------------------------
# reschedule_reminder
# ---------------------------------------------------------------------------

class TestRescheduleReminder:
    def setup_method(self):
        self.service = ReminderService()

    def _make(self, repeat: str, due: datetime) -> ReminderItem:
        r = self.service.add_reminder(1, "Task", future(), repeat=repeat)
        r.due_at = due
        r.reminded_24h_at = datetime.now(timezone.utc)
        r.fired_at = datetime.now(timezone.utc)
        return r

    def test_daily_advances_one_day(self):
        due = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
        r = self._make("daily", due)
        self.service.reschedule_reminder(1, r.id)
        assert r.due_at == datetime(2026, 3, 11, 12, 0, tzinfo=timezone.utc)

    def test_weekly_advances_seven_days(self):
        due = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
        r = self._make("weekly", due)
        self.service.reschedule_reminder(1, r.id)
        assert r.due_at == datetime(2026, 3, 17, 12, 0, tzinfo=timezone.utc)

    def test_monthly_advances_one_month(self):
        due = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
        r = self._make("monthly", due)
        self.service.reschedule_reminder(1, r.id)
        assert r.due_at == datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)

    def test_monthly_clamps_day_for_short_month(self):
        due = datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc)
        r = self._make("monthly", due)
        self.service.reschedule_reminder(1, r.id)
        assert r.due_at == datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc)

    def test_monthly_wraps_december_to_january(self):
        due = datetime(2026, 12, 15, 12, 0, tzinfo=timezone.utc)
        r = self._make("monthly", due)
        self.service.reschedule_reminder(1, r.id)
        assert r.due_at == datetime(2027, 1, 15, 12, 0, tzinfo=timezone.utc)

    def test_monthly_clamps_march31_to_apr30(self):
        due = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        r = self._make("monthly", due)
        self.service.reschedule_reminder(1, r.id)
        assert r.due_at == datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)

    def test_yearly_advances_one_year(self):
        due = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
        r = self._make("yearly", due)
        self.service.reschedule_reminder(1, r.id)
        assert r.due_at == datetime(2027, 6, 15, 12, 0, tzinfo=timezone.utc)

    def test_yearly_clamps_feb29_on_non_leap_year(self):
        # Feb 29 2028 (leap) → Feb 28 2029 (non-leap)
        due = datetime(2028, 2, 29, 12, 0, tzinfo=timezone.utc)
        r = self._make("yearly", due)
        self.service.reschedule_reminder(1, r.id)
        assert r.due_at == datetime(2029, 2, 28, 12, 0, tzinfo=timezone.utc)

    def test_yearly_feb29_stays_clamped_after_non_leap_intermediate(self):
        # Feb 29 2024 → Feb 28 2025 (clamp) → Feb 28 2026 → Feb 28 2027 → Feb 28 2028
        # Once clamped to Feb 28 the implementation keeps it at Feb 28 each year
        due = datetime(2024, 2, 29, 12, 0, tzinfo=timezone.utc)
        r = self._make("yearly", due)
        self.service.reschedule_reminder(1, r.id)  # → 2025
        assert r.due_at.month == 2
        assert r.due_at.day == 28  # clamped on first non-leap advance

    def test_clears_reminded_24h_at(self):
        r = self._make("daily", future(hours=1))
        self.service.reschedule_reminder(1, r.id)
        assert r.reminded_24h_at is None

    def test_clears_fired_at(self):
        r = self._make("daily", future(hours=1))
        self.service.reschedule_reminder(1, r.id)
        assert r.fired_at is None

    def test_repeat_none_raises(self):
        r = self.service.add_reminder(1, "Task", future())
        with pytest.raises(ReminderServiceError):
            self.service.reschedule_reminder(1, r.id)

    def test_not_found_raises(self):
        with pytest.raises(ReminderNotFoundError):
            self.service.reschedule_reminder(1, 999)


# ---------------------------------------------------------------------------
# toggle_recurring
# ---------------------------------------------------------------------------

class TestToggleRecurring:
    def setup_method(self):
        self.service = ReminderService()

    def test_active_repeat_gets_paused(self):
        r = self.service.add_reminder(1, "Task", future(), repeat="weekly")
        self.service.toggle_recurring(1, r.id)
        assert r.repeat == "none"
        assert r.paused_repeat == "weekly"

    def test_paused_repeat_gets_restored(self):
        r = self.service.add_reminder(1, "Task", future(), repeat="weekly")
        self.service.toggle_recurring(1, r.id)  # pause
        self.service.toggle_recurring(1, r.id)  # restore
        assert r.repeat == "weekly"
        assert r.paused_repeat is None

    def test_restoring_when_not_fired_keeps_due_at(self):
        due = future(hours=48)
        r = self.service.add_reminder(1, "Task", due, repeat="daily")
        r.due_at = due
        self.service.toggle_recurring(1, r.id)  # pause
        self.service.toggle_recurring(1, r.id)  # restore (fired_at is None)
        assert r.due_at == due

    def test_restoring_when_fired_advances_due_at(self):
        due = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
        r = self.service.add_reminder(1, "Task", future(), repeat="daily")
        r.due_at = due
        r.fired_at = datetime.now(timezone.utc)
        self.service.toggle_recurring(1, r.id)  # pause
        self.service.toggle_recurring(1, r.id)  # restore
        assert r.due_at == datetime(2026, 1, 11, 12, 0, tzinfo=timezone.utc)
        assert r.fired_at is None
        assert r.reminded_24h_at is None

    def test_no_op_when_not_recurring_and_no_paused(self):
        r = self.service.add_reminder(1, "Task", future())
        result = self.service.toggle_recurring(1, r.id)
        assert result.repeat == "none"
        assert result.paused_repeat is None

    def test_not_found_raises(self):
        with pytest.raises(ReminderNotFoundError):
            self.service.toggle_recurring(1, 999)

    def test_returns_the_reminder(self):
        r = self.service.add_reminder(1, "Task", future(), repeat="weekly")
        result = self.service.toggle_recurring(1, r.id)
        assert result is r

    @pytest.mark.parametrize("repeat", ["daily", "weekly", "monthly", "yearly"])
    def test_all_repeat_values_can_be_toggled(self, repeat):
        r = self.service.add_reminder(1, "Task", future(), repeat=repeat)
        self.service.toggle_recurring(1, r.id)
        assert r.paused_repeat == repeat
        self.service.toggle_recurring(1, r.id)
        assert r.repeat == repeat


# ---------------------------------------------------------------------------
# reminders_now_due
# ---------------------------------------------------------------------------

class TestRemindersNowDue:
    def setup_method(self):
        self.service = ReminderService()

    def test_empty_when_no_reminders(self):
        assert self.service.reminders_now_due() == []

    def test_returns_reminder_at_due_time(self):
        r = self.service.add_reminder(1, "Task", future(hours=1))
        ref = r.due_at + timedelta(minutes=1)
        result = self.service.reminders_now_due(reference_time=ref)
        assert len(result) == 1
        assert result[0] == (1, r)

    def test_skips_reminder_in_future(self):
        self.service.add_reminder(1, "Task", future(hours=5))
        assert self.service.reminders_now_due() == []

    def test_skips_already_fired(self):
        r = self.service.add_reminder(1, "Task", future(hours=1))
        r.fired_at = datetime.now(timezone.utc)
        ref = r.due_at + timedelta(minutes=1)
        assert self.service.reminders_now_due(reference_time=ref) == []

    def test_sorted_by_due_at(self):
        r1 = self.service.add_reminder(1, "Later", future(hours=3))
        r2 = self.service.add_reminder(1, "Sooner", future(hours=1))
        ref = r1.due_at + timedelta(minutes=1)
        result = self.service.reminders_now_due(reference_time=ref)
        assert result[0][1].id == r2.id
        assert result[1][1].id == r1.id

    def test_multiple_users_included(self):
        r1 = self.service.add_reminder(1, "User 1", future(hours=1))
        r2 = self.service.add_reminder(2, "User 2", future(hours=1))
        ref = max(r1.due_at, r2.due_at) + timedelta(minutes=1)
        result = self.service.reminders_now_due(reference_time=ref)
        user_ids = {uid for uid, _ in result}
        assert user_ids == {1, 2}

    def test_reference_time_used_correctly(self):
        r = self.service.add_reminder(1, "Task", future(hours=1))
        # Pass reference time before due_at — should not appear
        ref = r.due_at - timedelta(minutes=1)
        result = self.service.reminders_now_due(reference_time=ref)
        assert result == []


# ---------------------------------------------------------------------------
# reminders_due_within_24_hours
# ---------------------------------------------------------------------------

class TestRemindersDueWithin24Hours:
    def setup_method(self):
        self.service = ReminderService()

    def test_empty_when_no_reminders(self):
        assert self.service.reminders_due_within_24_hours() == []

    def test_returns_reminder_in_window(self):
        r = self.service.add_reminder(1, "Task", future(hours=12))
        result = self.service.reminders_due_within_24_hours()
        assert len(result) == 1
        assert result[0] == (1, r)

    def test_skips_reminder_outside_window(self):
        self.service.add_reminder(1, "Far future", future(hours=48))
        assert self.service.reminders_due_within_24_hours() == []

    def test_skips_already_reminded(self):
        r = self.service.add_reminder(1, "Task", future(hours=12))
        r.reminded_24h_at = datetime.now(timezone.utc)
        assert self.service.reminders_due_within_24_hours() == []

    def test_skips_overdue_reminders(self):
        r = self.service.add_reminder(1, "Task", future(hours=1))
        ref = r.due_at + timedelta(hours=1)
        assert self.service.reminders_due_within_24_hours(reference_time=ref) == []

    def test_sorted_by_due_at(self):
        r1 = self.service.add_reminder(1, "Later", future(hours=20))
        r2 = self.service.add_reminder(1, "Sooner", future(hours=2))
        result = self.service.reminders_due_within_24_hours()
        assert result[0][1].id == r2.id
        assert result[1][1].id == r1.id

    def test_boundary_at_exactly_24h_included(self):
        ref = datetime.now(timezone.utc)
        # Set due_at explicitly to exactly ref + 24h so timing doesn't shift
        r = self.service.add_reminder(1, "Task", future(hours=25))
        r.due_at = ref + timedelta(hours=24)
        result = self.service.reminders_due_within_24_hours(reference_time=ref)
        assert len(result) == 1

    def test_boundary_just_past_24h_excluded(self):
        ref = datetime.now(timezone.utc)
        self.service.add_reminder(1, "Task", future(hours=25))
        assert self.service.reminders_due_within_24_hours(reference_time=ref) == []


# ---------------------------------------------------------------------------
# load_reminder
# ---------------------------------------------------------------------------

class TestLoadReminder:
    def setup_method(self):
        self.service = ReminderService()

    def test_adds_new_reminder(self):
        r = ReminderItem(id=5, reminder="Task", due_at=future())
        self.service.load_reminder(1, r)
        assert len(self.service.list_reminders(1)) == 1

    def test_updates_existing_reminder_with_same_id(self):
        self.service.load_reminder(1, ReminderItem(id=5, reminder="Original", due_at=future()))
        self.service.load_reminder(1, ReminderItem(id=5, reminder="Updated", due_at=future()))
        reminders = self.service.list_reminders(1)
        assert len(reminders) == 1
        assert reminders[0].reminder == "Updated"

    def test_advances_next_id(self):
        self.service.load_reminder(1, ReminderItem(id=10, reminder="Task", due_at=future()))
        new_r = self.service.add_reminder(1, "New", future())
        assert new_r.id == 11

    def test_does_not_lower_next_id(self):
        self.service.load_reminder(1, ReminderItem(id=10, reminder="High", due_at=future()))
        self.service.load_reminder(1, ReminderItem(id=3, reminder="Low", due_at=future()))
        new_r = self.service.add_reminder(1, "Next", future())
        assert new_r.id == 11


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

class TestReset:
    def test_clears_all_reminders(self):
        service = ReminderService()
        service.add_reminder(1, "Task A", future())
        service.add_reminder(2, "Task B", future())
        service.reset()
        assert service.list_reminders(1) == []
        assert service.list_reminders(2) == []

    def test_resets_id_counters(self):
        service = ReminderService()
        service.add_reminder(1, "Task", future())
        service.add_reminder(1, "Task", future())
        service.reset()
        new_r = service.add_reminder(1, "After reset", future())
        assert new_r.id == 1
