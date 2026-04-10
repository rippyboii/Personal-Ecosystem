from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from cogs.reminder import (
    CROSS_MARK,
    REMINDER_DONE_COLOR,
    REMINDER_DUE_NOW_COLOR,
    REMINDER_DUE_SOON_COLOR,
    REMINDER_LIST_COLOR,
    WHITE_CHECK_MARK,
    ReminderCog,
)
from services.reminder_service import ReminderItem, ReminderServiceError, ReminderValidationError

PING_CHANNEL_ID = 555555555555555555
LIST_CHANNEL_ID = 666666666666666666


def future(hours: float = 2) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def make_cog() -> ReminderCog:
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 9999
    return ReminderCog(bot)


def make_interaction(user_id: int = 1) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.send_message = AsyncMock()
    interaction.original_response = AsyncMock(return_value=MagicMock())
    return interaction


def make_reminder_item(
    id: int = 1,
    reminder: str = "Test reminder",
    due_at: datetime | None = None,
    created_at: datetime | None = None,
    reminded_24h_at: datetime | None = None,
    fired_at: datetime | None = None,
    repeat: str = "none",
    paused_repeat: str | None = None,
) -> ReminderItem:
    return ReminderItem(
        id=id,
        reminder=reminder,
        due_at=due_at or future(hours=24),
        created_at=created_at or datetime(2025, 1, 1, tzinfo=timezone.utc),
        reminded_24h_at=reminded_24h_at,
        fired_at=fired_at,
        repeat=repeat,
        paused_repeat=paused_repeat,
    )


def make_list_message(embed: discord.Embed, content: str = "") -> MagicMock:
    message = MagicMock()
    message.embeds = [embed]
    message.content = content
    message.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return message


# ---------------------------------------------------------------------------
# _format_reminder_body
# ---------------------------------------------------------------------------

class TestFormatReminderBody:
    def setup_method(self):
        self.cog = make_cog()

    def test_single_line(self):
        assert self.cog._format_reminder_body("Buy milk") == "> Buy milk"

    def test_multiline(self):
        result = self.cog._format_reminder_body("Line 1\nLine 2")
        assert result == "> Line 1\n> Line 2"

    def test_empty_lines_get_bare_gt(self):
        result = self.cog._format_reminder_body("Line 1\n\nLine 3")
        assert result == "> Line 1\n>\n> Line 3"

    def test_triple_backtick_escaped(self):
        result = self.cog._format_reminder_body("code ```block```")
        assert "```" not in result.replace("`\u200b``", "")


# ---------------------------------------------------------------------------
# _build_focus_heading
# ---------------------------------------------------------------------------

class TestBuildFocusHeading:
    def setup_method(self):
        self.cog = make_cog()

    def test_basic_heading(self):
        assert self.cog._build_focus_heading("Buy milk", "⏰") == "## ⏰ Buy milk"

    def test_long_text_truncated(self):
        long = "a" * 130
        result = self.cog._build_focus_heading(long, "⏰")
        assert result.endswith("...")

    def test_multiline_collapsed(self):
        result = self.cog._build_focus_heading("Line 1\nLine 2", "⏰")
        assert "\n" not in result
        assert "Line 1 Line 2" in result

    def test_exactly_120_chars_not_truncated(self):
        result = self.cog._build_focus_heading("a" * 120, "⏰")
        assert not result.endswith("...")


# ---------------------------------------------------------------------------
# _format_timestamp / _format_relative_timestamp
# ---------------------------------------------------------------------------

class TestFormatTimestamp:
    def setup_method(self):
        self.cog = make_cog()

    def test_none_returns_na(self):
        assert self.cog._format_timestamp(None) == "N/A"

    def test_datetime_returns_discord_full_timestamp(self):
        dt = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        result = self.cog._format_timestamp(dt)
        assert result.startswith("<t:")
        assert result.endswith(":F>")
        assert str(int(dt.timestamp())) in result

    def test_none_relative_returns_na(self):
        assert self.cog._format_relative_timestamp(None) == "N/A"

    def test_datetime_returns_relative_discord_timestamp(self):
        dt = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        result = self.cog._format_relative_timestamp(dt)
        assert result.startswith("<t:")
        assert result.endswith(":R>")


# ---------------------------------------------------------------------------
# _extract_reminder_id
# ---------------------------------------------------------------------------

class TestExtractReminderId:
    def setup_method(self):
        self.cog = make_cog()

    def test_reminder_number_format(self):
        assert self.cog._extract_reminder_id("Reminder #5") == 5

    def test_due_now_format(self):
        assert self.cog._extract_reminder_id("Reminder #12 — Due Now") == 12

    def test_upcoming_format(self):
        assert self.cog._extract_reminder_id("Upcoming Reminder #3") == 3

    def test_none_returns_none(self):
        assert self.cog._extract_reminder_id(None) is None

    def test_no_match_returns_none(self):
        assert self.cog._extract_reminder_id("Random title") is None

    def test_empty_returns_none(self):
        assert self.cog._extract_reminder_id("") is None


# ---------------------------------------------------------------------------
# _embed_field_value
# ---------------------------------------------------------------------------

class TestEmbedFieldValue:
    def setup_method(self):
        self.cog = make_cog()

    def _embed(self, fields: list[tuple[str, str]]) -> discord.Embed:
        embed = discord.Embed()
        for name, value in fields:
            embed.add_field(name=name, value=value)
        return embed

    def test_returns_matching_field_value(self):
        embed = self._embed([("Owner", "<@123>"), ("Status", "Pending")])
        assert self.cog._embed_field_value(embed, "Owner") == "<@123>"

    def test_case_insensitive(self):
        embed = self._embed([("REPEAT", "weekly")])
        assert self.cog._embed_field_value(embed, "repeat") == "weekly"

    def test_missing_field_returns_none(self):
        embed = self._embed([("Other", "value")])
        assert self.cog._embed_field_value(embed, "Missing") is None

    def test_no_fields_returns_none(self):
        assert self.cog._embed_field_value(discord.Embed(), "Anything") is None


# ---------------------------------------------------------------------------
# _extract_owner_id
# ---------------------------------------------------------------------------

class TestExtractOwnerId:
    def setup_method(self):
        self.cog = make_cog()

    def _embed(self, fields: list[tuple[str, str]] | None = None) -> discord.Embed:
        embed = discord.Embed()
        for name, value in (fields or []):
            embed.add_field(name=name, value=value)
        return embed

    def test_from_owner_field(self):
        embed = self._embed([("Owner", "<@12345>")])
        assert self.cog._extract_owner_id(embed, None) == 12345

    def test_from_owner_field_with_exclamation(self):
        embed = self._embed([("Owner", "<@!67890>")])
        assert self.cog._extract_owner_id(embed, None) == 67890

    def test_from_content_mention(self):
        embed = self._embed()
        assert self.cog._extract_owner_id(embed, "<@99999> reminder text") == 99999

    def test_owner_field_takes_priority_over_content(self):
        embed = self._embed([("Owner", "<@111>")])
        assert self.cog._extract_owner_id(embed, "<@222>") == 111

    def test_no_owner_returns_none(self):
        assert self.cog._extract_owner_id(self._embed(), None) is None


# ---------------------------------------------------------------------------
# _extract_reminder_text
# ---------------------------------------------------------------------------

class TestExtractReminderText:
    def setup_method(self):
        self.cog = make_cog()

    def _embed(self, fields: list[tuple[str, str]] | None = None, description: str | None = None) -> discord.Embed:
        embed = discord.Embed(description=description)
        for name, value in (fields or []):
            embed.add_field(name=name, value=value)
        return embed

    def test_from_reminder_field_strips_gt_prefix(self):
        embed = self._embed(fields=[("Reminder", "> Buy milk")])
        assert self.cog._extract_reminder_text(embed, None) == "Buy milk"

    def test_from_reminder_field_multiline(self):
        embed = self._embed(fields=[("Reminder", "> Line 1\n> Line 2")])
        result = self.cog._extract_reminder_text(embed, None)
        assert "Line 1" in result
        assert "Line 2" in result

    def test_from_embed_description_when_no_field(self):
        embed = self._embed(description="Buy milk")
        assert self.cog._extract_reminder_text(embed, None) == "Buy milk"

    def test_from_content_heading(self):
        embed = self._embed()
        assert self.cog._extract_reminder_text(embed, "## ⏰ Buy milk") == "Buy milk"

    def test_reminder_field_takes_priority_over_description(self):
        embed = self._embed(description="From description", fields=[("Reminder", "> From field")])
        assert self.cog._extract_reminder_text(embed, None) == "From field"

    def test_all_missing_returns_none(self):
        assert self.cog._extract_reminder_text(self._embed(), None) is None


# ---------------------------------------------------------------------------
# _extract_field_timestamp
# ---------------------------------------------------------------------------

class TestExtractFieldTimestamp:
    def setup_method(self):
        self.cog = make_cog()

    def test_valid_discord_timestamp(self):
        dt = datetime(2025, 6, 1, tzinfo=timezone.utc)
        embed = discord.Embed()
        embed.add_field(name="Due", value=f"<t:{int(dt.timestamp())}:F>")
        result = self.cog._extract_field_timestamp(embed, "Due")
        assert result is not None
        assert result.timestamp() == pytest.approx(dt.timestamp(), abs=1)

    def test_missing_field_returns_none(self):
        assert self.cog._extract_field_timestamp(discord.Embed(), "Due") is None

    def test_field_without_timestamp_returns_none(self):
        embed = discord.Embed()
        embed.add_field(name="Due", value="not a timestamp")
        assert self.cog._extract_field_timestamp(embed, "Due") is None


# ---------------------------------------------------------------------------
# _build_reminder_list_embed
# ---------------------------------------------------------------------------

class TestBuildReminderListEmbed:
    def setup_method(self):
        self.cog = make_cog()

    def _field(self, embed: discord.Embed, name: str) -> str | None:
        for f in embed.fields:
            if f.name == name:
                return f.value
        return None

    def test_title_contains_reminder_id(self):
        embed = self.cog._build_reminder_list_embed(1, make_reminder_item(id=7))
        assert "Reminder #7" in embed.title

    def test_color_is_list_color(self):
        embed = self.cog._build_reminder_list_embed(1, make_reminder_item())
        assert embed.color.value == REMINDER_LIST_COLOR

    def test_owner_field_contains_mention(self):
        embed = self.cog._build_reminder_list_embed(42, make_reminder_item())
        assert "<@42>" in self._field(embed, "Owner")

    def test_status_pending_when_not_fired(self):
        embed = self.cog._build_reminder_list_embed(1, make_reminder_item())
        assert "Pending" in self._field(embed, "Status")

    def test_status_24h_when_reminded(self):
        r = make_reminder_item(reminded_24h_at=datetime.now(timezone.utc))
        embed = self.cog._build_reminder_list_embed(1, r)
        assert "24h" in self._field(embed, "Status")

    def test_status_fired_takes_priority_over_24h(self):
        r = make_reminder_item(
            reminded_24h_at=datetime.now(timezone.utc),
            fired_at=datetime.now(timezone.utc),
        )
        embed = self.cog._build_reminder_list_embed(1, r)
        assert "Fired" in self._field(embed, "Status")

    def test_repeat_on_when_active(self):
        r = make_reminder_item(repeat="weekly")
        embed = self.cog._build_reminder_list_embed(1, r)
        repeat_val = self._field(embed, "Repeat")
        assert "weekly" in repeat_val
        assert "(ON)" in repeat_val

    def test_repeat_off_when_paused(self):
        r = make_reminder_item(repeat="none", paused_repeat="monthly")
        embed = self.cog._build_reminder_list_embed(1, r)
        repeat_val = self._field(embed, "Repeat")
        assert "monthly" in repeat_val
        assert "(OFF)" in repeat_val

    def test_repeat_none_when_not_recurring(self):
        r = make_reminder_item(repeat="none")
        embed = self.cog._build_reminder_list_embed(1, r)
        assert self._field(embed, "Repeat") == "none"

    def test_paused_repeat_field_shows_raw_value(self):
        r = make_reminder_item(repeat="none", paused_repeat="weekly")
        embed = self.cog._build_reminder_list_embed(1, r)
        assert self._field(embed, "Paused Repeat") == "weekly"

    def test_reminder_text_in_reminder_field(self):
        r = make_reminder_item(reminder="Important task")
        embed = self.cog._build_reminder_list_embed(1, r)
        assert "Important task" in self._field(embed, "Reminder")


# ---------------------------------------------------------------------------
# _build_due_soon_embed
# ---------------------------------------------------------------------------

class TestBuildDueSoonEmbed:
    def setup_method(self):
        self.cog = make_cog()

    def test_title_contains_reminder_id(self):
        embed = self.cog._build_due_soon_embed(1, make_reminder_item(id=3))
        assert "#3" in embed.title

    def test_color_is_due_soon(self):
        embed = self.cog._build_due_soon_embed(1, make_reminder_item())
        assert embed.color.value == REMINDER_DUE_SOON_COLOR

    def test_has_owner_and_due_fields(self):
        embed = self.cog._build_due_soon_embed(1, make_reminder_item())
        names = [f.name for f in embed.fields]
        assert "Owner" in names
        assert "Due" in names

    def test_reminder_text_in_fields(self):
        r = make_reminder_item(reminder="Doctor appointment")
        embed = self.cog._build_due_soon_embed(1, r)
        all_values = " ".join(f.value for f in embed.fields)
        assert "Doctor appointment" in all_values


# ---------------------------------------------------------------------------
# _build_due_now_embed
# ---------------------------------------------------------------------------

class TestBuildDueNowEmbed:
    def setup_method(self):
        self.cog = make_cog()

    def test_title_references_due_now(self):
        embed = self.cog._build_due_now_embed(1, make_reminder_item(id=5))
        assert "Due Now" in embed.title or "#5" in embed.title

    def test_color_is_due_now(self):
        embed = self.cog._build_due_now_embed(1, make_reminder_item())
        assert embed.color.value == REMINDER_DUE_NOW_COLOR

    def test_reminder_text_in_fields(self):
        r = make_reminder_item(reminder="Call doctor")
        embed = self.cog._build_due_now_embed(1, r)
        all_values = " ".join(f.value for f in embed.fields)
        assert "Call doctor" in all_values


# ---------------------------------------------------------------------------
# _build_done_reminder_embed
# ---------------------------------------------------------------------------

class TestBuildDoneReminderEmbed:
    def setup_method(self):
        self.cog = make_cog()

    def test_color_is_done_color(self):
        embed = self.cog._build_done_reminder_embed(1, make_reminder_item())
        assert embed.color.value == REMINDER_DONE_COLOR

    def test_has_done_at_field(self):
        embed = self.cog._build_done_reminder_embed(1, make_reminder_item())
        names = [f.name for f in embed.fields]
        assert "Done At" in names

    def test_status_is_done(self):
        embed = self.cog._build_done_reminder_embed(1, make_reminder_item())
        status = next((f.value for f in embed.fields if f.name == "Status"), None)
        assert status == "Done"

    def test_reminder_text_present(self):
        r = make_reminder_item(reminder="Finish report")
        embed = self.cog._build_done_reminder_embed(1, r)
        all_values = " ".join(f.value for f in embed.fields)
        assert "Finish report" in all_values


# ---------------------------------------------------------------------------
# _parse_reminder_list_message
# ---------------------------------------------------------------------------

class TestParseReminderListMessage:
    def setup_method(self):
        self.cog = make_cog()

    def _build_list_message(self, user_id: int, reminder: ReminderItem) -> MagicMock:
        embed = self.cog._build_reminder_list_embed(user_id, reminder)
        content = self.cog._build_focus_heading(reminder.reminder, "🗓️")
        return make_list_message(embed, content)

    def test_returns_none_for_no_embeds(self):
        message = MagicMock()
        message.embeds = []
        assert self.cog._parse_reminder_list_message(message) is None

    def test_returns_none_for_done_embed(self):
        embed = self.cog._build_done_reminder_embed(1, make_reminder_item())
        assert self.cog._parse_reminder_list_message(make_list_message(embed)) is None

    def test_returns_none_if_no_reminder_id_in_title(self):
        embed = discord.Embed(title="Not a reminder title")
        assert self.cog._parse_reminder_list_message(make_list_message(embed)) is None

    def test_returns_none_if_no_owner(self):
        embed = discord.Embed(title="Reminder #1")
        assert self.cog._parse_reminder_list_message(make_list_message(embed)) is None

    def test_round_trip_basic(self):
        r = make_reminder_item(id=3, reminder="Buy groceries")
        parsed = self.cog._parse_reminder_list_message(self._build_list_message(1, r))
        assert parsed is not None
        user_id, reminder_id, reminder_text, due_at, *_ = parsed
        assert user_id == 1
        assert reminder_id == 3
        assert reminder_text == "Buy groceries"
        assert due_at.tzinfo == timezone.utc

    def test_round_trip_active_repeat(self):
        r = make_reminder_item(repeat="weekly")
        parsed = self.cog._parse_reminder_list_message(self._build_list_message(1, r))
        assert parsed is not None
        *_, repeat, paused_repeat = parsed
        assert repeat == "weekly"
        assert paused_repeat is None

    def test_round_trip_paused_repeat(self):
        # This tests the (OFF) parsing fix — without it, repeat would wrongly be "monthly"
        r = make_reminder_item(repeat="none", paused_repeat="monthly")
        parsed = self.cog._parse_reminder_list_message(self._build_list_message(1, r))
        assert parsed is not None
        *_, repeat, paused_repeat = parsed
        assert repeat == "none"
        assert paused_repeat == "monthly"

    def test_round_trip_no_repeat(self):
        r = make_reminder_item(repeat="none")
        parsed = self.cog._parse_reminder_list_message(self._build_list_message(1, r))
        assert parsed is not None
        *_, repeat, paused_repeat = parsed
        assert repeat == "none"
        assert paused_repeat is None

    def test_round_trip_reminded_24h_at(self):
        ts = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
        r = make_reminder_item(reminded_24h_at=ts)
        parsed = self.cog._parse_reminder_list_message(self._build_list_message(1, r))
        assert parsed is not None
        _, _, _, _, _, reminded_24h_at, *_ = parsed
        assert reminded_24h_at is not None
        assert abs((reminded_24h_at - ts).total_seconds()) < 2

    def test_round_trip_fired_at(self):
        ts = datetime(2025, 7, 1, 8, 0, tzinfo=timezone.utc)
        r = make_reminder_item(fired_at=ts)
        parsed = self.cog._parse_reminder_list_message(self._build_list_message(1, r))
        assert parsed is not None
        _, _, _, _, _, _, fired_at, *_ = parsed
        assert fired_at is not None
        assert abs((fired_at - ts).total_seconds()) < 2

    def test_backward_compat_repeat_without_suffix(self):
        # Old embeds stored raw "weekly" with no (ON)/(OFF) — must still parse
        embed = discord.Embed(title="Reminder #1")
        embed.add_field(name="Owner", value="<@1>")
        embed.add_field(name="Due", value=f"<t:{int(future().timestamp())}:F>")
        embed.add_field(name="Reminder", value="> Task")
        embed.add_field(name="Repeat", value="weekly")
        embed.add_field(name="Paused Repeat", value="none")
        parsed = self.cog._parse_reminder_list_message(make_list_message(embed))
        assert parsed is not None
        *_, repeat, paused_repeat = parsed
        assert repeat == "weekly"


# ---------------------------------------------------------------------------
# _parse_due_datetime_parts
# ---------------------------------------------------------------------------

class TestParseDueDatetimeParts:
    def setup_method(self):
        self.cog = make_cog()

    def test_valid_utc(self):
        dt = self.cog._parse_due_datetime_parts("2030/06/15", "14:30", "UTC")
        assert dt.year == 2030
        assert dt.month == 6
        assert dt.day == 15
        assert dt.hour == 14
        assert dt.minute == 30
        assert dt.tzinfo == timezone.utc

    def test_invalid_date_format_raises(self):
        with pytest.raises(ValueError):
            self.cog._parse_due_datetime_parts("15-06-2030", "14:30", "UTC")

    def test_invalid_time_format_raises(self):
        with pytest.raises(ValueError):
            self.cog._parse_due_datetime_parts("2030/06/15", "2pm", "UTC")

    def test_unknown_timezone_raises(self):
        with pytest.raises(ValueError):
            self.cog._parse_due_datetime_parts("2030/06/15", "14:30", "Fake/Zone")

    def test_timezone_offsets_correctly(self):
        # America/New_York is UTC-5 in January
        dt = self.cog._parse_due_datetime_parts("2030/01/15", "12:00", "America/New_York")
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 17  # 12:00 ET = 17:00 UTC


# ---------------------------------------------------------------------------
# quick_add_reminder command
# ---------------------------------------------------------------------------

class TestQuickAddReminderCommand:
    def setup_method(self):
        self.cog = make_cog()
        self.cog._send_reminder_list_update = AsyncMock()
        self.cog._check_due_reminders = AsyncMock()
        self._cmd = self.cog.quick_add_reminder.callback

    async def test_success_sends_confirmation(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, reminder="Buy milk", due_date="2030/06/15", due_time="10:00")
        interaction.response.send_message.assert_awaited_once()

    async def test_success_calls_list_update(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, reminder="Task", due_date="2030/06/15", due_time="10:00")
        self.cog._send_reminder_list_update.assert_awaited_once()

    async def test_success_calls_check_due(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, reminder="Task", due_date="2030/06/15", due_time="10:00")
        self.cog._check_due_reminders.assert_awaited_once()

    async def test_invalid_date_format_sends_ephemeral(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, reminder="Task", due_date="bad-date", due_time="10:00")
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True

    async def test_past_date_sends_ephemeral(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, reminder="Task", due_date="2000/01/01", due_time="10:00")
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True

    async def test_empty_reminder_sends_ephemeral(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, reminder="   ", due_date="2030/06/15", due_time="10:00")
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True

    async def test_validation_error_does_not_call_list_update(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, reminder="Task", due_date="bad", due_time="10:00")
        self.cog._send_reminder_list_update.assert_not_awaited()

    async def test_service_error_sends_ephemeral(self):
        interaction = make_interaction()
        self.cog.reminder_service.add_reminder = MagicMock(side_effect=ReminderServiceError("boom"))
        self.cog._report_error = AsyncMock()
        await self._cmd(self.cog, interaction, reminder="Task", due_date="2030/06/15", due_time="10:00")
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# list_reminders command
# ---------------------------------------------------------------------------

class TestListRemindersCommand:
    def setup_method(self):
        self.cog = make_cog()
        self._cmd = self.cog.list_reminders.callback

    async def test_no_reminders_sends_hint(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction)
        msg = interaction.response.send_message.call_args[0][0]
        assert "no reminders" in msg.lower() or "/reminder add" in msg

    async def test_with_reminders_lists_them(self):
        interaction = make_interaction()
        self.cog.reminder_service.add_reminder(interaction.user.id, "Task 1", future())
        self.cog.reminder_service.add_reminder(interaction.user.id, "Task 2", future())
        await self._cmd(self.cog, interaction)
        msg = interaction.response.send_message.call_args[0][0]
        assert "Task 1" in msg
        assert "Task 2" in msg

    async def test_service_error_sends_ephemeral(self):
        interaction = make_interaction()
        self.cog.reminder_service.list_reminders = MagicMock(side_effect=ReminderServiceError("boom"))
        self.cog._report_error = AsyncMock()
        await self._cmd(self.cog, interaction)
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# delete_reminder command
# ---------------------------------------------------------------------------

class TestDeleteReminderCommand:
    def setup_method(self):
        self.cog = make_cog()
        self.cog._delete_reminder_list_message = AsyncMock()
        self._cmd = self.cog.delete_reminder.callback

    async def test_success_sends_confirmation(self):
        interaction = make_interaction()
        r = self.cog.reminder_service.add_reminder(interaction.user.id, "Task", future())
        await self._cmd(self.cog, interaction, reminder_id=r.id)
        msg = interaction.response.send_message.call_args[0][0]
        assert "Task" in msg

    async def test_success_removes_from_service(self):
        interaction = make_interaction()
        r = self.cog.reminder_service.add_reminder(interaction.user.id, "Task", future())
        await self._cmd(self.cog, interaction, reminder_id=r.id)
        assert self.cog.reminder_service.list_reminders(interaction.user.id) == []

    async def test_success_deletes_list_message(self):
        interaction = make_interaction()
        r = self.cog.reminder_service.add_reminder(interaction.user.id, "Task", future())
        await self._cmd(self.cog, interaction, reminder_id=r.id)
        self.cog._delete_reminder_list_message.assert_awaited_once()

    async def test_not_found_sends_ephemeral(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, reminder_id=999)
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True

    async def test_service_error_sends_ephemeral(self):
        interaction = make_interaction()
        self.cog.reminder_service.delete_reminder = MagicMock(side_effect=ReminderServiceError("boom"))
        self.cog._report_error = AsyncMock()
        await self._cmd(self.cog, interaction, reminder_id=1)
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# edit_reminder command
# ---------------------------------------------------------------------------

class TestEditReminderCommand:
    def setup_method(self):
        self.cog = make_cog()
        self._cmd = self.cog.edit_reminder.callback

    async def test_not_found_sends_ephemeral(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, reminder_id=999)
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True

    async def test_found_opens_picker(self):
        interaction = make_interaction()
        r = self.cog.reminder_service.add_reminder(interaction.user.id, "Task", future())
        await self._cmd(self.cog, interaction, reminder_id=r.id)
        interaction.response.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# on_raw_reaction_add — ✅ tick (ping channel)
# ---------------------------------------------------------------------------

class TestOnRawReactionAddTick:
    def setup_method(self):
        self.cog = make_cog()
        self.cog._mark_reminder_done_in_channel = AsyncMock()
        self.cog._delete_message = AsyncMock()
        self.cog._report_error = AsyncMock()

        r = self.cog.reminder_service.add_reminder(1, "Task", future())
        self.reminder = r
        self.ping_msg_id = 42
        self.list_msg_id = 99
        self.cog.reminder_reaction_map[self.ping_msg_id] = (1, r.id)
        self.cog.reminder_message_by_key[(1, r.id)] = self.list_msg_id
        self.cog.reminder_cross_reaction_map[self.list_msg_id] = (1, r.id)

    def _make_payload(self, user_id=1, message_id=42, channel_id=PING_CHANNEL_ID, emoji=WHITE_CHECK_MARK):
        payload = MagicMock()
        payload.user_id = user_id
        payload.message_id = message_id
        payload.channel_id = channel_id
        payload.emoji = emoji
        return payload

    @patch("cogs.reminder.reminder_channel_id", str(PING_CHANNEL_ID))
    async def test_valid_tick_deletes_reminder_from_service(self):
        await self.cog.on_raw_reaction_add(self._make_payload())
        assert self.cog.reminder_service.list_reminders(1) == []

    @patch("cogs.reminder.reminder_channel_id", str(PING_CHANNEL_ID))
    async def test_valid_tick_marks_done_in_list_channel(self):
        await self.cog.on_raw_reaction_add(self._make_payload())
        self.cog._mark_reminder_done_in_channel.assert_awaited_once()

    @patch("cogs.reminder.reminder_channel_id", str(PING_CHANNEL_ID))
    async def test_valid_tick_deletes_ping_message(self):
        await self.cog.on_raw_reaction_add(self._make_payload())
        self.cog._delete_message.assert_awaited_once()

    @patch("cogs.reminder.reminder_channel_id", str(PING_CHANNEL_ID))
    async def test_valid_tick_removes_from_tick_reaction_map(self):
        await self.cog.on_raw_reaction_add(self._make_payload())
        assert self.ping_msg_id not in self.cog.reminder_reaction_map

    @patch("cogs.reminder.reminder_channel_id", str(PING_CHANNEL_ID))
    async def test_valid_tick_clears_cross_reaction_map_for_list_message(self):
        # Tests the bug fix — uses list_msg_id, not ping_msg_id
        await self.cog.on_raw_reaction_add(self._make_payload())
        assert self.list_msg_id not in self.cog.reminder_cross_reaction_map

    async def test_bot_own_reaction_ignored(self):
        await self.cog.on_raw_reaction_add(self._make_payload(user_id=9999))
        assert len(self.cog.reminder_service.list_reminders(1)) == 1

    async def test_wrong_emoji_ignored(self):
        await self.cog.on_raw_reaction_add(self._make_payload(emoji="👍"))
        assert len(self.cog.reminder_service.list_reminders(1)) == 1

    async def test_unknown_message_id_ignored(self):
        await self.cog.on_raw_reaction_add(self._make_payload(message_id=9999))
        self.cog._mark_reminder_done_in_channel.assert_not_awaited()

    async def test_non_owner_reaction_ignored(self):
        await self.cog.on_raw_reaction_add(self._make_payload(user_id=2))
        assert len(self.cog.reminder_service.list_reminders(1)) == 1

    @patch("cogs.reminder.reminder_channel_id", str(PING_CHANNEL_ID))
    async def test_wrong_channel_ignored(self):
        await self.cog.on_raw_reaction_add(self._make_payload(channel_id=999999999999999999))
        assert len(self.cog.reminder_service.list_reminders(1)) == 1


# ---------------------------------------------------------------------------
# on_raw_reaction_add — ❌ cross (list channel)
# ---------------------------------------------------------------------------

class TestOnRawReactionAddCross:
    def setup_method(self):
        self.cog = make_cog()
        self.cog._refresh_reminder_list_message = AsyncMock()
        self.cog._report_error = AsyncMock()

        r = self.cog.reminder_service.add_reminder(1, "Task", future(), repeat="weekly")
        self.reminder = r
        self.list_msg_id = 77
        self.cog.reminder_cross_reaction_map[self.list_msg_id] = (1, r.id)
        self.cog.reminder_message_by_key[(1, r.id)] = self.list_msg_id

    def _make_payload(self, user_id=1, message_id=77, channel_id=LIST_CHANNEL_ID, emoji=CROSS_MARK):
        payload = MagicMock()
        payload.user_id = user_id
        payload.message_id = message_id
        payload.channel_id = channel_id
        payload.emoji = emoji
        return payload

    @patch("cogs.reminder.reminder_list_channel_id", str(LIST_CHANNEL_ID))
    async def test_valid_cross_pauses_recurring(self):
        await self.cog.on_raw_reaction_add(self._make_payload())
        r = self.cog.reminder_service.list_reminders(1)[0]
        assert r.repeat == "none"
        assert r.paused_repeat == "weekly"

    @patch("cogs.reminder.reminder_list_channel_id", str(LIST_CHANNEL_ID))
    async def test_valid_cross_refreshes_list_message(self):
        await self.cog.on_raw_reaction_add(self._make_payload())
        self.cog._refresh_reminder_list_message.assert_awaited_once()

    @patch("cogs.reminder.reminder_list_channel_id", str(LIST_CHANNEL_ID))
    async def test_cross_twice_restores_recurring(self):
        payload = self._make_payload()
        await self.cog.on_raw_reaction_add(payload)  # pause
        await self.cog.on_raw_reaction_add(payload)  # restore
        r = self.cog.reminder_service.list_reminders(1)[0]
        assert r.repeat == "weekly"
        assert r.paused_repeat is None

    async def test_bot_own_reaction_ignored(self):
        await self.cog.on_raw_reaction_add(self._make_payload(user_id=9999))
        r = self.cog.reminder_service.list_reminders(1)[0]
        assert r.repeat == "weekly"

    async def test_unknown_message_id_ignored(self):
        await self.cog.on_raw_reaction_add(self._make_payload(message_id=9999))
        self.cog._refresh_reminder_list_message.assert_not_awaited()

    async def test_non_owner_reaction_ignored(self):
        await self.cog.on_raw_reaction_add(self._make_payload(user_id=2))
        r = self.cog.reminder_service.list_reminders(1)[0]
        assert r.repeat == "weekly"

    @patch("cogs.reminder.reminder_list_channel_id", str(LIST_CHANNEL_ID))
    async def test_wrong_channel_ignored(self):
        await self.cog.on_raw_reaction_add(self._make_payload(channel_id=999999999999999999))
        r = self.cog.reminder_service.list_reminders(1)[0]
        assert r.repeat == "weekly"


# ---------------------------------------------------------------------------
# _send_due_soon_ping
# ---------------------------------------------------------------------------

class TestSendDueSoonPing:
    def setup_method(self):
        self.cog = make_cog()
        self.message = AsyncMock()
        self.message.id = 42
        self.channel = AsyncMock()
        self.channel.send = AsyncMock(return_value=self.message)
        self.cog._resolve_channel = AsyncMock(return_value=self.channel)

    async def test_returns_false_when_no_channel(self):
        self.cog._resolve_channel = AsyncMock(return_value=None)
        assert await self.cog._send_due_soon_ping(1, make_reminder_item()) is False

    async def test_returns_true_on_success(self):
        assert await self.cog._send_due_soon_ping(1, make_reminder_item()) is True

    async def test_sends_message_to_channel(self):
        await self.cog._send_due_soon_ping(1, make_reminder_item())
        self.channel.send.assert_awaited_once()

    async def test_adds_tick_reaction_to_message(self):
        await self.cog._send_due_soon_ping(1, make_reminder_item())
        self.message.add_reaction.assert_awaited_once_with(WHITE_CHECK_MARK)

    async def test_registers_in_reaction_map(self):
        r = make_reminder_item(id=5)
        await self.cog._send_due_soon_ping(1, r)
        assert self.cog.reminder_reaction_map.get(42) == (1, 5)

    async def test_returns_true_even_if_reaction_fails(self):
        self.message.add_reaction = AsyncMock(side_effect=discord.DiscordException)
        assert await self.cog._send_due_soon_ping(1, make_reminder_item()) is True

    async def test_returns_false_if_send_fails(self):
        self.channel.send = AsyncMock(side_effect=discord.DiscordException)
        assert await self.cog._send_due_soon_ping(1, make_reminder_item()) is False


# ---------------------------------------------------------------------------
# _send_due_now_ping
# ---------------------------------------------------------------------------

class TestSendDueNowPing:
    def setup_method(self):
        self.cog = make_cog()
        self.message = AsyncMock()
        self.message.id = 55
        self.channel = AsyncMock()
        self.channel.send = AsyncMock(return_value=self.message)
        self.cog._resolve_channel = AsyncMock(return_value=self.channel)

    async def test_returns_false_when_no_channel(self):
        self.cog._resolve_channel = AsyncMock(return_value=None)
        assert await self.cog._send_due_now_ping(1, make_reminder_item()) is False

    async def test_returns_true_on_success(self):
        assert await self.cog._send_due_now_ping(1, make_reminder_item()) is True

    async def test_sends_message_to_channel(self):
        await self.cog._send_due_now_ping(1, make_reminder_item())
        self.channel.send.assert_awaited_once()

    async def test_adds_tick_reaction(self):
        await self.cog._send_due_now_ping(1, make_reminder_item())
        self.message.add_reaction.assert_awaited_once_with(WHITE_CHECK_MARK)

    async def test_registers_in_reaction_map(self):
        r = make_reminder_item(id=7)
        await self.cog._send_due_now_ping(1, r)
        assert self.cog.reminder_reaction_map.get(55) == (1, 7)

    async def test_returns_true_even_if_reaction_fails(self):
        self.message.add_reaction = AsyncMock(side_effect=discord.DiscordException)
        assert await self.cog._send_due_now_ping(1, make_reminder_item()) is True

    async def test_returns_false_if_send_fails(self):
        self.channel.send = AsyncMock(side_effect=discord.DiscordException)
        assert await self.cog._send_due_now_ping(1, make_reminder_item()) is False


# ---------------------------------------------------------------------------
# _send_reminder_list_update
# ---------------------------------------------------------------------------

class TestSendReminderListUpdate:
    def setup_method(self):
        self.cog = make_cog()
        self.message = AsyncMock()
        self.message.id = 100
        self.channel = AsyncMock()
        self.channel.send = AsyncMock(return_value=self.message)
        self.cog._resolve_channel = AsyncMock(return_value=self.channel)

    def _user(self, user_id: int = 1) -> MagicMock:
        user = MagicMock()
        user.id = user_id
        return user

    async def test_sends_embed_to_channel(self):
        await self.cog._send_reminder_list_update(self._user(), make_reminder_item())
        self.channel.send.assert_awaited_once()

    async def test_stores_message_id_in_map(self):
        r = make_reminder_item(id=3)
        await self.cog._send_reminder_list_update(self._user(1), r)
        assert self.cog.reminder_message_by_key.get((1, 3)) == 100

    async def test_adds_cross_reaction_for_recurring(self):
        await self.cog._send_reminder_list_update(self._user(), make_reminder_item(repeat="daily"))
        self.message.add_reaction.assert_awaited_once_with(CROSS_MARK)

    async def test_adds_cross_reaction_for_paused(self):
        r = make_reminder_item(repeat="none", paused_repeat="weekly")
        await self.cog._send_reminder_list_update(self._user(), r)
        self.message.add_reaction.assert_awaited_once_with(CROSS_MARK)

    async def test_no_cross_reaction_for_one_time(self):
        await self.cog._send_reminder_list_update(self._user(), make_reminder_item(repeat="none"))
        self.message.add_reaction.assert_not_awaited()

    async def test_registers_cross_in_map_for_recurring(self):
        r = make_reminder_item(id=5, repeat="daily")
        await self.cog._send_reminder_list_update(self._user(1), r)
        assert self.cog.reminder_cross_reaction_map.get(100) == (1, 5)

    async def test_no_cross_registration_for_one_time(self):
        r = make_reminder_item(id=5, repeat="none")
        await self.cog._send_reminder_list_update(self._user(1), r)
        assert 100 not in self.cog.reminder_cross_reaction_map

    async def test_no_op_when_no_channel(self):
        self.cog._resolve_channel = AsyncMock(return_value=None)
        await self.cog._send_reminder_list_update(self._user(), make_reminder_item())
        self.channel.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# _check_due_reminders
# ---------------------------------------------------------------------------

class TestCheckDueReminders:
    def setup_method(self):
        self.cog = make_cog()
        self.cog._send_due_soon_ping = AsyncMock(return_value=True)
        self.cog._send_due_now_ping = AsyncMock(return_value=True)
        self.cog._refresh_reminder_list_message = AsyncMock()
        self.cog._report_error = AsyncMock()

    def _add(self, user_id: int = 1, **kwargs) -> ReminderItem:
        return self.cog.reminder_service.add_reminder(user_id, "Task", future(), **kwargs)

    async def test_sends_24h_ping_for_due_soon_reminder(self):
        r = self._add()
        r.due_at = datetime.now(timezone.utc) + timedelta(hours=12)
        await self.cog._check_due_reminders()
        self.cog._send_due_soon_ping.assert_awaited_once()

    async def test_marks_24h_reminded_after_ping_sent(self):
        r = self._add()
        r.due_at = datetime.now(timezone.utc) + timedelta(hours=12)
        await self.cog._check_due_reminders()
        assert r.reminded_24h_at is not None

    async def test_does_not_mark_24h_if_ping_fails(self):
        self.cog._send_due_soon_ping = AsyncMock(return_value=False)
        r = self._add()
        r.due_at = datetime.now(timezone.utc) + timedelta(hours=12)
        await self.cog._check_due_reminders()
        assert r.reminded_24h_at is None

    async def test_sends_due_now_ping_for_overdue_reminder(self):
        r = self._add()
        r.due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await self.cog._check_due_reminders()
        self.cog._send_due_now_ping.assert_awaited_once()

    async def test_marks_fired_for_one_time_reminder(self):
        r = self._add()
        r.due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await self.cog._check_due_reminders()
        assert r.fired_at is not None

    async def test_reschedules_recurring_reminder_instead_of_marking_fired(self):
        r = self._add(repeat="daily")
        original_due = datetime.now(timezone.utc) - timedelta(minutes=1)
        r.due_at = original_due
        await self.cog._check_due_reminders()
        assert r.due_at > original_due
        assert r.fired_at is None

    async def test_does_not_mark_fired_if_due_now_ping_fails(self):
        self.cog._send_due_now_ping = AsyncMock(return_value=False)
        r = self._add()
        r.due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await self.cog._check_due_reminders()
        assert r.fired_at is None

    async def test_refreshes_list_message_after_due_now(self):
        r = self._add()
        r.due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await self.cog._check_due_reminders()
        self.cog._refresh_reminder_list_message.assert_awaited()

    async def test_skips_already_reminded_24h(self):
        r = self._add()
        r.due_at = datetime.now(timezone.utc) + timedelta(hours=12)
        r.reminded_24h_at = datetime.now(timezone.utc)
        await self.cog._check_due_reminders()
        self.cog._send_due_soon_ping.assert_not_awaited()

    async def test_skips_already_fired(self):
        r = self._add()
        r.due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        r.fired_at = datetime.now(timezone.utc)
        await self.cog._check_due_reminders()
        self.cog._send_due_now_ping.assert_not_awaited()
