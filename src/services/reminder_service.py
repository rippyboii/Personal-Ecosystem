from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple


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


class ReminderService:
    def __init__(self) -> None:
        self._reminders_by_user: Dict[int, List[ReminderItem]] = {}
        self._next_id_by_user: Dict[int, int] = {}

    def add_reminder(self, user_id: int, reminder_text: str, due_at: datetime) -> ReminderItem:
        cleaned_text = self._validate_reminder_text(reminder_text)
        normalized_due_at = self._validate_due_at(due_at)

        reminder = ReminderItem(
            id=self._next_id(user_id),
            reminder=cleaned_text,
            due_at=normalized_due_at,
            created_at=datetime.now(timezone.utc),
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
