from calendar import monthrange
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

VALID_REPEAT_VALUES = {"none", "daily", "weekly", "monthly", "yearly"}


class ReminderServiceError(Exception):
    """Base error for reminder service failures."""


class ReminderValidationError(ReminderServiceError):
    """Raised when user input is invalid."""


class ReminderNotFoundError(ReminderServiceError):
    """Raised when a reminder does not exist for a user."""


@dataclass
class ReminderItem:
    id: int
    reminder: str
    due_at: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reminded_24h_at: datetime | None = None
    fired_at: datetime | None = None
    repeat: str = "none"


class ReminderService:
    def __init__(self) -> None:
        self._reminders_by_user: Dict[int, List[ReminderItem]] = {}
        self._next_id_by_user: Dict[int, int] = {}

    def add_reminder(
        self, user_id: int, reminder_text: str, due_at: datetime, repeat: str = "none"
    ) -> ReminderItem:
        cleaned_text = self._validate_reminder_text(reminder_text)
        normalized_due_at = self._validate_due_at(due_at)
        cleaned_repeat = self._validate_repeat(repeat)

        reminder = ReminderItem(
            id=self._next_id(user_id),
            reminder=cleaned_text,
            due_at=normalized_due_at,
            created_at=datetime.now(timezone.utc),
            repeat=cleaned_repeat,
        )
        self._reminders_by_user.setdefault(user_id, []).append(reminder)
        return reminder

    def list_reminders(self, user_id: int) -> List[ReminderItem]:
        return list(self._reminders_by_user.get(user_id, []))

    def reset(self) -> None:
        self._reminders_by_user.clear()
        self._next_id_by_user.clear()

    def load_reminder(self, user_id: int, reminder: ReminderItem) -> None:
        reminders = self._reminders_by_user.setdefault(user_id, [])
        for index, existing in enumerate(reminders):
            if existing.id == reminder.id:
                reminders[index] = reminder
                break
        else:
            reminders.append(reminder)

        self.ensure_next_id(user_id, reminder.id + 1)

    def ensure_next_id(self, user_id: int, next_id: int) -> None:
        current = self._next_id_by_user.get(user_id, 1)
        if next_id > current:
            self._next_id_by_user[user_id] = next_id

    def delete_reminder(self, user_id: int, reminder_id: int) -> ReminderItem:
        reminders = self._reminders_by_user.get(user_id, [])
        for index, reminder in enumerate(reminders):
            if reminder.id == reminder_id:
                removed = reminders.pop(index)
                if not reminders:
                    self._reminders_by_user.pop(user_id, None)
                    self._next_id_by_user.pop(user_id, None)
                return removed
        raise ReminderNotFoundError(f"Reminder with id {reminder_id} not found.")

    def mark_24h_reminded(
        self, user_id: int, reminder_id: int, reminded_at: datetime | None = None
    ) -> ReminderItem:
        reminder = self._get_reminder_by_id(user_id, reminder_id)
        reminder.reminded_24h_at = reminded_at or datetime.now(timezone.utc)
        return reminder

    def mark_fired(
        self, user_id: int, reminder_id: int, fired_at: datetime | None = None
    ) -> ReminderItem:
        reminder = self._get_reminder_by_id(user_id, reminder_id)
        reminder.fired_at = fired_at or datetime.now(timezone.utc)
        return reminder

    def reminders_now_due(
        self, reference_time: datetime | None = None
    ) -> List[Tuple[int, ReminderItem]]:
        now = reference_time or datetime.now(timezone.utc)
        now = now.astimezone(timezone.utc)

        due_reminders: List[Tuple[int, ReminderItem]] = []
        for user_id, reminders in self._reminders_by_user.items():
            for reminder in reminders:
                if reminder.fired_at is not None:
                    continue
                if reminder.due_at <= now:
                    due_reminders.append((user_id, reminder))

        due_reminders.sort(key=lambda item: item[1].due_at)
        return due_reminders

    def reminders_due_within_24_hours(
        self, reference_time: datetime | None = None
    ) -> List[Tuple[int, ReminderItem]]:
        now = reference_time or datetime.now(timezone.utc)
        now = now.astimezone(timezone.utc)
        due_window_end = now + timedelta(hours=24)

        due_reminders: List[Tuple[int, ReminderItem]] = []
        for user_id, reminders in self._reminders_by_user.items():
            for reminder in reminders:
                if reminder.reminded_24h_at is not None:
                    continue
                if now < reminder.due_at <= due_window_end:
                    due_reminders.append((user_id, reminder))

        due_reminders.sort(key=lambda item: item[1].due_at)
        return due_reminders

    def update_reminder(
        self,
        user_id: int,
        reminder_id: int,
        reminder_text: str | None = None,
        due_at: datetime | None = None,
        repeat: str | None = None,
    ) -> ReminderItem:
        reminder = self._get_reminder_by_id(user_id, reminder_id)

        if reminder_text is not None:
            reminder.reminder = self._validate_reminder_text(reminder_text)

        if due_at is not None:
            normalized = due_at.astimezone(timezone.utc) if due_at.tzinfo else due_at.replace(tzinfo=timezone.utc)
            if normalized <= datetime.now(timezone.utc):
                raise ReminderValidationError("Due date must be in the future.")
            if normalized != reminder.due_at:
                reminder.reminded_24h_at = None
                reminder.fired_at = None
            reminder.due_at = normalized

        if repeat is not None:
            reminder.repeat = self._validate_repeat(repeat)

        return reminder

    def reschedule_reminder(self, user_id: int, reminder_id: int) -> ReminderItem:
        reminder = self._get_reminder_by_id(user_id, reminder_id)
        reminder.due_at = self._next_due_at(reminder.due_at, reminder.repeat)
        reminder.reminded_24h_at = None
        reminder.fired_at = None
        return reminder

    def _next_due_at(self, due_at: datetime, repeat: str) -> datetime:
        if repeat == "daily":
            return due_at + timedelta(days=1)
        if repeat == "weekly":
            return due_at + timedelta(weeks=1)
        if repeat == "monthly":
            return self._advance_monthly(due_at)
        if repeat == "yearly":
            return self._advance_yearly(due_at)
        raise ReminderServiceError(f"Cannot reschedule a reminder with repeat='{repeat}'.")

    def _advance_yearly(self, dt: datetime) -> datetime:
        year = dt.year + 1
        max_day = monthrange(year, dt.month)[1]
        return dt.replace(year=year, day=min(dt.day, max_day))

    def _advance_monthly(self, dt: datetime) -> datetime:
        month = dt.month + 1
        year = dt.year
        if month > 12:
            month = 1
            year += 1
        max_day = monthrange(year, month)[1]
        return dt.replace(year=year, month=month, day=min(dt.day, max_day))

    def _validate_repeat(self, repeat: str) -> str:
        cleaned = (repeat or "none").strip().lower()
        if cleaned not in VALID_REPEAT_VALUES:
            raise ReminderValidationError(
                f"Invalid repeat value '{repeat}'. Must be one of: none, daily, weekly, monthly."
            )
        return cleaned

    def _validate_reminder_text(self, reminder_text: str) -> str:
        if reminder_text is None:
            raise ReminderValidationError("Reminder text is required.")

        cleaned = reminder_text.strip()
        if not cleaned:
            raise ReminderValidationError("Reminder text cannot be empty.")
        if len(cleaned) > 200:
            raise ReminderValidationError("Reminder text is too long (max 200 characters).")
        return cleaned

    def _validate_due_at(self, due_at: datetime) -> datetime:
        if due_at is None:
            raise ReminderValidationError("Due date is required.")

        normalized = due_at.astimezone(timezone.utc) if due_at.tzinfo else due_at.replace(tzinfo=timezone.utc)
        if normalized <= datetime.now(timezone.utc):
            raise ReminderValidationError("Due date must be in the future.")
        return normalized

    def _next_id(self, user_id: int) -> int:
        next_id = self._next_id_by_user.get(user_id, 1)
        self._next_id_by_user[user_id] = next_id + 1
        return next_id

    def _get_reminder_by_id(self, user_id: int, reminder_id: int) -> ReminderItem:
        for reminder in self._reminders_by_user.get(user_id, []):
            if reminder.id == reminder_id:
                return reminder
        raise ReminderNotFoundError(f"Reminder with id {reminder_id} not found.")
