import re
import shlex
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.abc import Messageable
from discord.ext import commands, tasks

from config import reminder_channel_id, reminder_list_channel_id
from services.reminder_service import (
    ReminderItem,
    ReminderNotFoundError,
    ReminderService,
    ReminderServiceError,
    ReminderValidationError,
)

WHITE_CHECK_MARK = "\N{WHITE HEAVY CHECK MARK}"
CROSS_MARK = "\N{CROSS MARK}"
REMINDER_LIST_COLOR = 0xF59E0B
REMINDER_DUE_SOON_COLOR = 0xF97316
REMINDER_DUE_NOW_COLOR = 0xEF4444
REMINDER_DONE_COLOR = 0x10B981
REPEAT_CHOICES = [
    app_commands.Choice(name="None (one-time)", value="none"),
    app_commands.Choice(name="Daily", value="daily"),
    app_commands.Choice(name="Weekly", value="weekly"),
    app_commands.Choice(name="Monthly", value="monthly"),
    app_commands.Choice(name="Yearly", value="yearly"),
]
PICKER_TIMEZONE_CHOICES = [
    ("UTC", "UTC"),
    ("Europe/Stockholm", "Europe/Stockholm"),
    ("America/New_York", "America/New_York"),
    ("America/Chicago", "America/Chicago"),
    ("America/Los_Angeles", "America/Los_Angeles"),
    ("Asia/Shanghai", "Asia/Shanghai"),
    ("Asia/Kathmandu", "Asia/Kathmandu"),
    ("Asia/Singapore", "Asia/Singapore"),
    ("Asia/Tokyo", "Asia/Tokyo"),
    ("Australia/Sydney", "Australia/Sydney"),
]


class ReminderDateSelect(discord.ui.Select):
    def __init__(self, picker_view: "ReminderDatePickerView") -> None:
        self.picker_view = picker_view
        super().__init__(
            placeholder="Select due date",
            min_values=1,
            max_values=1,
            options=self.picker_view.build_date_options(),
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.picker_view.selected_date = date.fromisoformat(self.values[0])
        await self.picker_view.refresh_message(interaction)


class ReminderTimezoneSelect(discord.ui.Select):
    def __init__(self, picker_view: "ReminderDatePickerView") -> None:
        self.picker_view = picker_view
        super().__init__(
            placeholder="Select timezone",
            min_values=1,
            max_values=1,
            options=self.picker_view.build_timezone_options(),
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.picker_view.timezone_name = self.values[0]
        await self.picker_view.refresh_message(interaction)


class ReminderHourSelect(discord.ui.Select):
    def __init__(self, picker_view: "ReminderDatePickerView") -> None:
        self.picker_view = picker_view
        super().__init__(
            placeholder="Select hour",
            min_values=1,
            max_values=1,
            options=self.picker_view.build_hour_options(),
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.picker_view.selected_hour = int(self.values[0])
        await self.picker_view.refresh_message(interaction)


class ReminderMinuteSelect(discord.ui.Select):
    def __init__(self, picker_view: "ReminderDatePickerView") -> None:
        self.picker_view = picker_view
        super().__init__(
            placeholder="Select minute",
            min_values=1,
            max_values=1,
            options=self.picker_view.build_minute_options(),
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.picker_view.selected_minute = int(self.values[0])
        await self.picker_view.refresh_message(interaction)


class ReminderPreviousDatesButton(discord.ui.Button):
    def __init__(self, picker_view: "ReminderDatePickerView") -> None:
        self.picker_view = picker_view
        super().__init__(label="Earlier Dates", style=discord.ButtonStyle.secondary, row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        self.picker_view.shift_date_window(-self.picker_view.DATE_PAGE_SIZE)
        await self.picker_view.refresh_message(interaction)


class ReminderNextDatesButton(discord.ui.Button):
    def __init__(self, picker_view: "ReminderDatePickerView") -> None:
        self.picker_view = picker_view
        super().__init__(label="Later Dates", style=discord.ButtonStyle.secondary, row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        self.picker_view.shift_date_window(self.picker_view.DATE_PAGE_SIZE)
        await self.picker_view.refresh_message(interaction)


class ReminderCreateButton(discord.ui.Button):
    def __init__(self, picker_view: "ReminderDatePickerView") -> None:
        self.picker_view = picker_view
        super().__init__(label="Create Reminder", style=discord.ButtonStyle.success, row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        due_at = self.picker_view.selected_due_at_utc()
        if due_at <= datetime.now(timezone.utc):
            await self.picker_view.refresh_message(interaction, "Selected due time must be in the future.")
            return

        try:
            reminder_item = self.picker_view.cog.reminder_service.add_reminder(
                interaction.user.id,
                self.picker_view.reminder_text,
                due_at,
                self.picker_view.repeat,
            )
            await self.picker_view.cog._send_reminder_list_update(interaction.user, reminder_item)
            await self.picker_view.cog._check_due_reminders()
        except ReminderValidationError as error:
            await self.picker_view.refresh_message(interaction, f"I couldn't add that reminder: {error}")
            return
        except ReminderServiceError as error:
            await self.picker_view.cog._report_error("ReminderDatePickerView.create", error)
            await self.picker_view.refresh_message(
                interaction,
                "Something went wrong while creating your reminder. Please try again.",
            )
            return

        self.picker_view.stop()
        await interaction.response.edit_message(
            content=(
                f"Added reminder #{reminder_item.id} due "
                f"{self.picker_view.cog._format_timestamp(reminder_item.due_at)}."
            ),
            view=None,
        )


class ReminderCancelButton(discord.ui.Button):
    def __init__(self, picker_view: "ReminderDatePickerView") -> None:
        self.picker_view = picker_view
        super().__init__(label="Cancel", style=discord.ButtonStyle.danger, row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        self.picker_view.stop()
        await interaction.response.edit_message(content="Reminder creation cancelled.", view=None)


class ReminderSaveButton(discord.ui.Button):
    def __init__(self, picker_view: "ReminderDatePickerView") -> None:
        self.picker_view = picker_view
        super().__init__(label="Save Changes", style=discord.ButtonStyle.success, row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        due_at = self.picker_view.selected_due_at_utc()
        if due_at <= datetime.now(timezone.utc):
            await self.picker_view.refresh_message(interaction, "Selected due time must be in the future.")
            return

        try:
            reminder_item = self.picker_view.cog.reminder_service.update_reminder(
                interaction.user.id,
                self.picker_view.edit_reminder_id,
                reminder_text=self.picker_view.reminder_text,
                due_at=due_at,
                repeat=self.picker_view.repeat,
            )
            await self.picker_view.cog._refresh_reminder_list_message(interaction.user.id, reminder_item.id)
            await self.picker_view.cog._check_due_reminders()
        except ReminderNotFoundError:
            await self.picker_view.refresh_message(
                interaction, f"I couldn't find reminder #{self.picker_view.edit_reminder_id}."
            )
            return
        except ReminderValidationError as error:
            await self.picker_view.refresh_message(interaction, f"I couldn't update that reminder: {error}")
            return
        except ReminderServiceError as error:
            await self.picker_view.cog._report_error("ReminderSaveButton.callback", error)
            await self.picker_view.refresh_message(
                interaction,
                "Something went wrong while updating your reminder. Please try again.",
            )
            return

        self.picker_view.stop()
        await interaction.response.edit_message(
            content=(
                f"Updated reminder #{reminder_item.id} due "
                f"{self.picker_view.cog._format_timestamp(reminder_item.due_at)}."
            ),
            view=None,
        )


class ReminderDatePickerView(discord.ui.View):
    DATE_PAGE_SIZE = 25

    def __init__(
        self,
        cog: "ReminderCog",
        requester_id: int,
        reminder_text: str,
        repeat: str = "none",
        prefill_due_at: datetime | None = None,
        edit_reminder_id: int | None = None,
    ) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.requester_id = requester_id
        self.reminder_text = reminder_text
        self.repeat = repeat
        self.edit_reminder_id = edit_reminder_id

        if prefill_due_at is not None:
            prefill_utc = prefill_due_at.astimezone(timezone.utc)
            self.selected_date = prefill_utc.date()
            self.selected_hour = prefill_utc.hour
            self.selected_minute = min([0, 15, 30, 45], key=lambda m: abs(m - prefill_utc.minute))
            self.timezone_name = "UTC"
        else:
            suggested_due = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(
                minute=0,
                second=0,
                microsecond=0,
            )
            self.selected_date = suggested_due.date()
            self.selected_hour = suggested_due.hour
            self.selected_minute = 0
            self.timezone_name = "UTC"

        self.date_page_start = self.selected_date
        self.message: discord.Message | None = None

        self.date_select = ReminderDateSelect(self)
        self.timezone_select = ReminderTimezoneSelect(self)
        self.hour_select = ReminderHourSelect(self)
        self.minute_select = ReminderMinuteSelect(self)
        self.previous_dates_button = ReminderPreviousDatesButton(self)
        self.next_dates_button = ReminderNextDatesButton(self)
        self.action_button = ReminderSaveButton(self) if edit_reminder_id is not None else ReminderCreateButton(self)
        self.cancel_button = ReminderCancelButton(self)

        self.add_item(self.date_select)
        self.add_item(self.timezone_select)
        self.add_item(self.hour_select)
        self.add_item(self.minute_select)
        self.add_item(self.previous_dates_button)
        self.add_item(self.next_dates_button)
        self.add_item(self.action_button)
        self.add_item(self.cancel_button)

        self.sync_component_state()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Only the user who started this reminder setup can use these controls.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is None:
            return
        retry_cmd = "/reminder edit" if self.edit_reminder_id is not None else "/reminder add"
        try:
            await self.message.edit(
                content=f"Reminder setup timed out. Run `{retry_cmd}` again.",
                view=self,
            )
        except discord.DiscordException:
            return

    def shift_date_window(self, delta_days: int) -> None:
        minimum_date = self.minimum_allowed_date()
        proposed_start = self.date_page_start + timedelta(days=delta_days)
        if proposed_start < minimum_date:
            proposed_start = minimum_date

        self.date_page_start = proposed_start
        if not self.date_is_visible(self.selected_date):
            self.selected_date = self.date_page_start

    def date_is_visible(self, selected: date) -> bool:
        window_end = self.date_page_start + timedelta(days=self.DATE_PAGE_SIZE - 1)
        return self.date_page_start <= selected <= window_end

    def minimum_allowed_date(self) -> date:
        return datetime.now(timezone.utc).date()

    def sync_component_state(self) -> None:
        minimum_date = self.minimum_allowed_date()
        if self.date_page_start < minimum_date:
            self.date_page_start = minimum_date
        if self.selected_date < minimum_date:
            self.selected_date = minimum_date
        if not self.date_is_visible(self.selected_date):
            self.selected_date = self.date_page_start

        self.date_select.options = self.build_date_options()
        self.timezone_select.options = self.build_timezone_options()
        self.hour_select.options = self.build_hour_options()
        self.minute_select.options = self.build_minute_options()

        self.previous_dates_button.disabled = self.date_page_start <= minimum_date
        self.action_button.disabled = self.selected_due_at_utc() <= datetime.now(timezone.utc)

    async def refresh_message(self, interaction: discord.Interaction, notice: str | None = None) -> None:
        self.sync_component_state()
        await interaction.response.edit_message(content=self.build_prompt_text(notice), view=self)

    def build_prompt_text(self, notice: str | None = None) -> str:
        due_at = self.selected_due_at_utc()
        window_end = self.date_page_start + timedelta(days=self.DATE_PAGE_SIZE - 1)

        if self.edit_reminder_id is not None:
            heading = f"Editing reminder #{self.edit_reminder_id}. Adjust the due date and time below."
        else:
            heading = "Use the picker below to set reminder due date and time."
        lines = [
            heading,
            f"Reminder: {self.reminder_text}",
            f"Selected due: {self.cog._format_timestamp(due_at)} ({self.cog._format_relative_timestamp(due_at)})",
            f"Timezone: `{self.timezone_name}`",
            f"Date window: `{self.date_page_start.isoformat()}` to `{window_end.isoformat()}`",
            f"Repeat: `{self.repeat}`",
        ]
        if due_at <= datetime.now(timezone.utc):
            lines.append("Selected due time must be in the future.")
        if notice:
            lines.append(notice)
        return "\n".join(lines)

    def build_date_options(self) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        for day_offset in range(self.DATE_PAGE_SIZE):
            current_date = self.date_page_start + timedelta(days=day_offset)
            options.append(
                discord.SelectOption(
                    label=current_date.strftime("%a %d %b %Y"),
                    value=current_date.isoformat(),
                    default=current_date == self.selected_date,
                )
            )
        return options

    def build_timezone_options(self) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        for label, value in PICKER_TIMEZONE_CHOICES:
            options.append(
                discord.SelectOption(
                    label=label,
                    value=value,
                    default=value == self.timezone_name,
                )
            )
        return options

    def build_hour_options(self) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        for hour in range(24):
            options.append(
                discord.SelectOption(
                    label=f"{hour:02d}:00",
                    value=str(hour),
                    default=hour == self.selected_hour,
                )
            )
        return options

    def build_minute_options(self) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        for minute in (0, 15, 30, 45):
            options.append(
                discord.SelectOption(
                    label=f"{minute:02d}",
                    value=str(minute),
                    default=minute == self.selected_minute,
                )
            )
        return options

    def selected_due_at_utc(self) -> datetime:
        tzinfo = self.resolve_timezone()
        local_due = datetime(
            year=self.selected_date.year,
            month=self.selected_date.month,
            day=self.selected_date.day,
            hour=self.selected_hour,
            minute=self.selected_minute,
            tzinfo=tzinfo,
        )
        return local_due.astimezone(timezone.utc)

    def resolve_timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError:
            self.timezone_name = "UTC"
            return ZoneInfo("UTC")


class ReminderCog(commands.GroupCog, group_name="reminder", group_description="Manage your reminders"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.reminder_service = ReminderService()
        self.reminder_message_by_key: Dict[Tuple[int, int], int] = {}
        self.reminder_reaction_map: Dict[int, Tuple[int, int]] = {}
        self.reminder_cross_reaction_map: Dict[int, Tuple[int, int]] = {}
        self._state_restored = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self._state_restored:
            try:
                await self._restore_state_from_channel()
                self._state_restored = True
            except discord.DiscordException:
                self._state_restored = False
                return

        await self._check_due_reminders()
        if not self.reminder_scan_loop.is_running():
            self.reminder_scan_loop.start()

    def cog_unload(self) -> None:
        if self.reminder_scan_loop.is_running():
            self.reminder_scan_loop.cancel()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        bot_user_id = self.bot.user.id if self.bot.user else None
        if payload.user_id == bot_user_id:
            return

        emoji = str(payload.emoji)
        if emoji not in (WHITE_CHECK_MARK, CROSS_MARK):
            return

        if emoji == WHITE_CHECK_MARK:
            mapping = self.reminder_reaction_map.get(payload.message_id)
            if mapping is None:
                return
            owner_user_id, reminder_id = mapping
            if payload.user_id != owner_user_id:
                return
            ping_channel_id = self._to_int(reminder_channel_id)
            if ping_channel_id is not None and payload.channel_id != ping_channel_id:
                return
            self.reminder_reaction_map.pop(payload.message_id, None)
            try:
                reminder_item = self._find_reminder(owner_user_id, reminder_id)
                if reminder_item is not None:
                    self.reminder_service.delete_reminder(owner_user_id, reminder_id)
                    await self._mark_reminder_done_in_channel(owner_user_id, reminder_item)
                    self.reminder_message_by_key.pop((owner_user_id, reminder_id), None)
                    self.reminder_cross_reaction_map.pop(payload.message_id, None)
                await self._delete_message(reminder_channel_id, payload.message_id)
            except (ReminderServiceError, discord.DiscordException):
                return

        elif emoji == CROSS_MARK:
            mapping = self.reminder_cross_reaction_map.get(payload.message_id)
            if mapping is None:
                return
            owner_user_id, reminder_id = mapping
            if payload.user_id != owner_user_id:
                return
            list_channel_id = self._to_int(reminder_list_channel_id)
            if list_channel_id is not None and payload.channel_id != list_channel_id:
                return
            try:
                reminder_item = self._find_reminder(owner_user_id, reminder_id)
                if reminder_item is None:
                    return
                self.reminder_service.toggle_recurring(owner_user_id, reminder_id)
                await self._refresh_reminder_list_message(owner_user_id, reminder_id)
            except (ReminderServiceError, discord.DiscordException):
                return

    @app_commands.command(name="add", description="Add a new reminder")
    @app_commands.describe(
        reminder="What should I remind you about?",
        repeat="How often to repeat (default: none)",
    )
    @app_commands.choices(repeat=REPEAT_CHOICES)
    async def add_reminder(self, interaction: discord.Interaction, reminder: str, repeat: str = "none") -> None:
        cleaned_reminder = reminder.strip()
        if not cleaned_reminder:
            await interaction.response.send_message("Reminder text cannot be empty.", ephemeral=True)
            return
        if len(cleaned_reminder) > 200:
            await interaction.response.send_message(
                "Reminder text is too long (max 200 characters).",
                ephemeral=True,
            )
            return

        picker_view = ReminderDatePickerView(self, interaction.user.id, cleaned_reminder, repeat)
        await interaction.response.send_message(
            picker_view.build_prompt_text(),
            ephemeral=True,
            view=picker_view,
        )
        try:
            picker_view.message = await interaction.original_response()
        except discord.DiscordException:
            picker_view.message = None

    @app_commands.command(name="quickadd", description="Add a reminder using explicit date and time")
    @app_commands.describe(
        reminder="What should I remind you about?",
        due_date="Date in yyyy/mm/dd (example: 2026/03/30)",
        due_time="Time in HH:MM 24-hour clock (example: 18:45)",
        timezone_name="Timezone name (default: UTC, example: Asia/Kathmandu)",
        repeat="How often to repeat (default: none)",
    )
    @app_commands.choices(repeat=REPEAT_CHOICES)
    async def quick_add_reminder(
        self,
        interaction: discord.Interaction,
        reminder: str,
        due_date: str,
        due_time: str,
        timezone_name: str = "UTC",
        repeat: str = "none",
    ) -> None:
        cleaned_reminder = reminder.strip()
        if not cleaned_reminder:
            await interaction.response.send_message("Reminder text cannot be empty.", ephemeral=True)
            return

        try:
            due_at = self._parse_due_datetime_parts(due_date, due_time, timezone_name)
            reminder_item = self.reminder_service.add_reminder(interaction.user.id, cleaned_reminder, due_at, repeat)
            await self._send_reminder_list_update(interaction.user, reminder_item)
            await self._check_due_reminders()
            await interaction.response.send_message(
                f"Added reminder #{reminder_item.id} due {self._format_timestamp(reminder_item.due_at)}."
            )
        except ValueError as error:
            await interaction.response.send_message(
                f"{error}\nUse date `yyyy/mm/dd`, time `HH:MM` (24-hour).",
                ephemeral=True,
            )
        except ReminderValidationError as error:
            await interaction.response.send_message(f"I couldn't add that reminder: {error}", ephemeral=True)
        except ReminderServiceError as error:
            await self._report_error("ReminderCog.quick_add_reminder", error)
            await interaction.response.send_message(
                "Something went wrong while adding your reminder. Please try again.",
                ephemeral=True,
            )

    @app_commands.command(name="list", description="List your reminders")
    async def list_reminders(self, interaction: discord.Interaction) -> None:
        try:
            reminders = self.reminder_service.list_reminders(interaction.user.id)
            if not reminders:
                await interaction.response.send_message(
                    "You have no reminders yet. Use `/reminder add` to create one."
                )
                return

            reminders = sorted(reminders, key=lambda reminder_item: reminder_item.due_at)
            lines = []
            for reminder_item in reminders:
                if reminder_item.fired_at:
                    status = "fired"
                elif reminder_item.reminded_24h_at:
                    status = "24h ping sent"
                else:
                    status = "pending"
                repeat_tag = f" [{reminder_item.repeat}]" if reminder_item.repeat != "none" else ""
                lines.append(
                    f"#{reminder_item.id} {self._format_timestamp(reminder_item.due_at)} "
                    f"({self._format_relative_timestamp(reminder_item.due_at)}) [{status}]{repeat_tag} - "
                    f"{reminder_item.reminder}"
                )

            await interaction.response.send_message("Your reminders:\n" + "\n".join(lines))
        except ReminderServiceError as error:
            await self._report_error("ReminderCog.list_reminders", error)
            await interaction.response.send_message(
                "Something went wrong while reading your reminders. Please try again.",
                ephemeral=True,
            )

    @app_commands.command(name="delete", description="Delete a reminder")
    @app_commands.describe(reminder_id="The reminder ID to delete")
    async def delete_reminder(self, interaction: discord.Interaction, reminder_id: int) -> None:
        try:
            reminder_item = self.reminder_service.delete_reminder(interaction.user.id, reminder_id)
            await interaction.response.send_message(
                f"Deleted reminder #{reminder_item.id}: {reminder_item.reminder}"
            )
            await self._delete_reminder_list_message(interaction.user.id, reminder_item.id)
        except ReminderNotFoundError:
            await interaction.response.send_message(
                f"I couldn't find reminder #{reminder_id}. Use `/reminder list` to check IDs.",
                ephemeral=True,
            )
        except ReminderServiceError as error:
            await self._report_error("ReminderCog.delete_reminder", error)
            await interaction.response.send_message(
                "Something went wrong while deleting your reminder. Please try again.",
                ephemeral=True,
            )

    @app_commands.command(name="edit", description="Edit an existing reminder")
    @app_commands.describe(
        reminder_id="The reminder ID to edit",
        reminder="New reminder text (leave blank to keep existing)",
        repeat="New repeat setting (leave blank to keep existing)",
    )
    @app_commands.choices(repeat=REPEAT_CHOICES)
    async def edit_reminder(
        self,
        interaction: discord.Interaction,
        reminder_id: int,
        reminder: str | None = None,
        repeat: str | None = None,
    ) -> None:
        existing = self._find_reminder(interaction.user.id, reminder_id)
        if existing is None:
            await interaction.response.send_message(
                f"I couldn't find reminder #{reminder_id}. Use `/reminder list` to check IDs.",
                ephemeral=True,
            )
            return

        new_text = reminder.strip() if reminder else existing.reminder
        new_repeat = repeat if repeat is not None else existing.repeat

        picker_view = ReminderDatePickerView(
            self,
            interaction.user.id,
            new_text,
            new_repeat,
            prefill_due_at=existing.due_at,
            edit_reminder_id=reminder_id,
        )
        await interaction.response.send_message(
            picker_view.build_prompt_text(),
            ephemeral=True,
            view=picker_view,
        )
        try:
            picker_view.message = await interaction.original_response()
        except discord.DiscordException:
            picker_view.message = None

    @commands.command(name="reminder")
    async def add_reminder_legacy(self, ctx: commands.Context, *, args: str | None = None) -> None:
        if not args:
            await ctx.send(
                'Usage: `!reminder "<description>" -d "yyyy/mm/dd" -t "HH:MM" [-r none|daily|weekly|monthly|yearly]`'
                " (24-hour clock, UTC)."
            )
            return

        try:
            reminder_text, due_at, repeat = self._parse_legacy_reminder_args(args)
            reminder_item = self.reminder_service.add_reminder(ctx.author.id, reminder_text, due_at, repeat)
            await self._send_reminder_list_update(ctx.author, reminder_item)
            await self._check_due_reminders()
            await ctx.send(
                f"Added reminder #{reminder_item.id} due {self._format_timestamp(reminder_item.due_at)}."
            )
        except ValueError as error:
            await ctx.send(
                f"{error}\nUsage: `!reminder \"<description>\" -d \"yyyy/mm/dd\" -t \"HH:MM\""
                " [-r none|daily|weekly|monthly|yearly]` (24-hour clock, UTC)."
            )
        except ReminderValidationError as error:
            await ctx.send(f"I couldn't add that reminder: {error}")
        except ReminderServiceError as error:
            await self._report_error("ReminderCog.add_reminder_legacy", error)
            await ctx.send("Something went wrong while adding your reminder. Please try again.")

    @tasks.loop(minutes=5)
    async def reminder_scan_loop(self) -> None:
        await self._check_due_reminders()

    @reminder_scan_loop.before_loop
    async def before_reminder_scan_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _check_due_reminders(self) -> None:
        due_soon = self.reminder_service.reminders_due_within_24_hours()
        for user_id, reminder_item in due_soon:
            try:
                sent = await self._send_due_soon_ping(user_id, reminder_item)
                if not sent:
                    continue
                self.reminder_service.mark_24h_reminded(user_id, reminder_item.id)
                await self._refresh_reminder_list_message(user_id, reminder_item.id)
            except ReminderServiceError as error:
                await self._report_error("ReminderCog._check_due_reminders", error)
            except discord.DiscordException as error:
                await self._report_error("ReminderCog._check_due_reminders", error)

        now_due = self.reminder_service.reminders_now_due()
        for user_id, reminder_item in now_due:
            try:
                sent = await self._send_due_now_ping(user_id, reminder_item)
                if not sent:
                    continue
                if reminder_item.repeat != "none":
                    self.reminder_service.reschedule_reminder(user_id, reminder_item.id)
                else:
                    self.reminder_service.mark_fired(user_id, reminder_item.id)
                await self._refresh_reminder_list_message(user_id, reminder_item.id)
            except ReminderServiceError as error:
                await self._report_error("ReminderCog._check_due_reminders", error)
            except discord.DiscordException as error:
                await self._report_error("ReminderCog._check_due_reminders", error)

    async def _send_due_soon_ping(self, user_id: int, reminder_item: ReminderItem) -> bool:
        channel = await self._resolve_channel(reminder_channel_id)
        if channel is None:
            return False

        embed = self._build_due_soon_embed(user_id, reminder_item)
        focus_text = self._build_focus_heading(reminder_item.reminder, "⏰")
        content = (
            f"<@{user_id}> I am reminding you about your reminder for schedules in next 24hr.\n"
            f"{focus_text}"
        )

        try:
            message = await channel.send(content=content, embed=embed)
        except discord.DiscordException:
            return False

        try:
            await message.add_reaction(WHITE_CHECK_MARK)
            self.reminder_reaction_map[message.id] = (user_id, reminder_item.id)
        except discord.DiscordException:
            pass

        return True

    async def _send_due_now_ping(self, user_id: int, reminder_item: ReminderItem) -> bool:
        channel = await self._resolve_channel(reminder_channel_id)
        if channel is None:
            return False

        embed = self._build_due_now_embed(user_id, reminder_item)
        focus_text = self._build_focus_heading(reminder_item.reminder, "🔔")
        content = (
            f"<@{user_id}> Your reminder is due now!\n"
            f"{focus_text}"
        )

        try:
            message = await channel.send(content=content, embed=embed)
        except discord.DiscordException:
            return False

        try:
            await message.add_reaction(WHITE_CHECK_MARK)
            self.reminder_reaction_map[message.id] = (user_id, reminder_item.id)
        except discord.DiscordException:
            pass

        return True

    async def _send_reminder_list_update(
        self, user: discord.User | discord.Member, reminder_item: ReminderItem
    ) -> None:
        channel = await self._resolve_channel(reminder_list_channel_id)
        if channel is None:
            return

        try:
            embed = self._build_reminder_list_embed(user.id, reminder_item)
            focus_text = self._build_focus_heading(reminder_item.reminder, "🗓️")
            message = await channel.send(content=focus_text, embed=embed)
            self.reminder_message_by_key[(user.id, reminder_item.id)] = message.id
            if reminder_item.repeat != "none" or reminder_item.paused_repeat is not None:
                await message.add_reaction(CROSS_MARK)
                self.reminder_cross_reaction_map[message.id] = (user.id, reminder_item.id)
        except discord.DiscordException:
            return

    async def _refresh_reminder_list_message(self, user_id: int, reminder_id: int) -> None:
        key = (user_id, reminder_id)
        message_id = self.reminder_message_by_key.get(key)
        if message_id is None:
            return

        reminder_item = self._find_reminder(user_id, reminder_id)
        if reminder_item is None:
            return

        channel = await self._resolve_channel(reminder_list_channel_id)
        if channel is None or not hasattr(channel, "fetch_message"):
            return

        try:
            message = await channel.fetch_message(message_id)
            embed = self._build_reminder_list_embed(user_id, reminder_item)
            focus_text = self._build_focus_heading(reminder_item.reminder, "🗓️")
            await message.edit(content=focus_text, embed=embed)
            if (reminder_item.repeat != "none" or reminder_item.paused_repeat is not None) and \
                    message_id not in self.reminder_cross_reaction_map:
                await message.add_reaction(CROSS_MARK)
                self.reminder_cross_reaction_map[message_id] = (user_id, reminder_id)
        except discord.NotFound:
            self.reminder_message_by_key.pop(key, None)
            self.reminder_cross_reaction_map.pop(message_id, None)
        except discord.DiscordException:
            return

    async def _delete_reminder_list_message(self, user_id: int, reminder_id: int) -> None:
        key = (user_id, reminder_id)
        message_id = self.reminder_message_by_key.get(key)
        if message_id is None:
            return

        await self._delete_message(reminder_list_channel_id, message_id)
        self.reminder_message_by_key.pop(key, None)
        self.reminder_cross_reaction_map.pop(message_id, None)

    async def _delete_message(self, channel_id: str | None, message_id: int) -> None:
        channel = await self._resolve_channel(channel_id)
        if channel is None or not hasattr(channel, "fetch_message"):
            return
        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
        except discord.DiscordException:
            return

    async def _restore_state_from_channel(self) -> None:
        self.reminder_service.reset()
        self.reminder_message_by_key.clear()
        self.reminder_reaction_map.clear()
        self.reminder_cross_reaction_map.clear()

        list_channel = await self._resolve_channel(reminder_list_channel_id)
        if list_channel is None or not hasattr(list_channel, "history"):
            return

        async for message in list_channel.history(limit=None, oldest_first=True):
            parsed = self._parse_reminder_list_message(message)
            if parsed is None:
                continue

            user_id, reminder_id, reminder_text, due_at, created_at, reminded_24h_at, fired_at, repeat, paused_repeat = parsed
            reminder_item = ReminderItem(
                id=reminder_id,
                reminder=reminder_text,
                due_at=due_at,
                created_at=created_at,
                reminded_24h_at=reminded_24h_at,
                fired_at=fired_at,
                repeat=repeat,
                paused_repeat=paused_repeat,
            )
            self.reminder_service.load_reminder(user_id, reminder_item)
            self.reminder_message_by_key[(user_id, reminder_id)] = message.id
            if reminder_item.repeat != "none" or reminder_item.paused_repeat is not None:
                self.reminder_cross_reaction_map[message.id] = (user_id, reminder_id)

    async def _resolve_channel(self, channel_id: str | None) -> Messageable | None:
        if not channel_id:
            return None
        try:
            channel_id_int = int(channel_id)
        except ValueError:
            return None

        cached_channel = self.bot.get_channel(channel_id_int)
        if cached_channel is not None:
            return cached_channel

        try:
            fetched_channel = await self.bot.fetch_channel(channel_id_int)
        except discord.DiscordException:
            return None
        if isinstance(fetched_channel, Messageable):
            return fetched_channel
        return None

    def _parse_reminder_list_message(
        self, message: discord.Message
    ) -> tuple[int, int, str, datetime, datetime, datetime | None, datetime | None, str, str | None] | None:
        if not message.embeds:
            return None

        embed = message.embeds[0]
        if self._embed_field_value(embed, "Done At") is not None:
            return None

        reminder_id = self._extract_reminder_id(embed.title)
        if reminder_id is None:
            return None

        user_id = self._extract_owner_id(embed, message.content)
        if user_id is None:
            user_id = self._extract_owner_id_from_message(message)
        if user_id is None:
            return None

        reminder_text = self._extract_reminder_text(embed, message.content)
        if not reminder_text:
            return None

        due_at = self._extract_field_timestamp(embed, "Due")
        if due_at is None:
            return None

        created_at = self._extract_field_timestamp(embed, "Created") or message.created_at
        reminded_24h_at = self._extract_field_timestamp(embed, "24h Reminder Sent")
        fired_at = self._extract_field_timestamp(embed, "Due Ping Sent")
        repeat = self._embed_field_value(embed, "Repeat") or "none"
        paused_repeat_str = self._embed_field_value(embed, "Paused Repeat") or "none"
        paused_repeat = None if paused_repeat_str == "none" else paused_repeat_str
        return (
            user_id,
            reminder_id,
            reminder_text,
            due_at.astimezone(timezone.utc),
            created_at.astimezone(timezone.utc),
            reminded_24h_at.astimezone(timezone.utc) if reminded_24h_at else None,
            fired_at.astimezone(timezone.utc) if fired_at else None,
            repeat,
            paused_repeat,
        )

    def _extract_reminder_id(self, title: str | None) -> int | None:
        if not title:
            return None
        match = re.search(r"Reminder\s+#(\d+)", title)
        if not match:
            return None
        return int(match.group(1))

    def _extract_owner_id(self, embed: discord.Embed, content: str | None) -> int | None:
        owner_field = self._embed_field_value(embed, "Owner")
        if owner_field:
            match = re.search(r"<@!?(\d+)>", owner_field)
            if match:
                return int(match.group(1))

        if content:
            match = re.search(r"<@!?(\d+)>", content)
            if match:
                return int(match.group(1))

        return None

    def _extract_owner_id_from_message(self, message: discord.Message) -> int | None:
        interaction_metadata = getattr(message, "interaction_metadata", None)
        if interaction_metadata and getattr(interaction_metadata, "user", None):
            return interaction_metadata.user.id
        return None

    def _extract_reminder_text(self, embed: discord.Embed, content: str | None) -> str | None:
        reminder_field = self._embed_field_value(embed, "Reminder")
        if reminder_field:
            lines = [line.lstrip("> ") for line in reminder_field.splitlines()]
            reminder_text = "\n".join(lines).strip()
            if reminder_text:
                return reminder_text

        if embed.description and embed.description.strip():
            return embed.description.strip()

        if content:
            heading_match = re.search(r"^##\s+[^\n]*", content, re.MULTILINE)
            if heading_match:
                heading = heading_match.group(0)
                heading = re.sub(r"^##\s+", "", heading).strip()
                heading = re.sub(r"^[^\w]+", "", heading).strip()
                if heading:
                    return heading
        return None

    def _extract_field_timestamp(self, embed: discord.Embed, field_name: str) -> datetime | None:
        value = self._embed_field_value(embed, field_name)
        if not value:
            return None

        match = re.search(r"<t:(\d+)(?::[a-zA-Z])?>", value)
        if not match:
            return None
        return datetime.fromtimestamp(int(match.group(1)), tz=timezone.utc)

    def _embed_field_value(self, embed: discord.Embed, name: str) -> str | None:
        for field in embed.fields:
            if field.name.strip().lower() == name.lower():
                return field.value
        return None

    def _build_reminder_list_embed(self, user_id: int, reminder_item: ReminderItem) -> discord.Embed:
        if reminder_item.fired_at:
            status_text = "Fired"
        elif reminder_item.reminded_24h_at:
            status_text = "24h reminder sent"
        else:
            status_text = "Pending"
        embed = discord.Embed(
            title=f"Reminder #{reminder_item.id}",
            color=REMINDER_LIST_COLOR,
        )
        embed.add_field(name="Owner", value=f"<@{user_id}>", inline=True)
        embed.add_field(
            name="Due",
            value=f"{self._format_timestamp(reminder_item.due_at)} ({self._format_relative_timestamp(reminder_item.due_at)})",
            inline=True,
        )
        embed.add_field(name="Status", value=status_text, inline=True)
        embed.add_field(name="Created", value=self._format_timestamp(reminder_item.created_at), inline=True)
        embed.add_field(name="24h Reminder Sent", value=self._format_timestamp(reminder_item.reminded_24h_at), inline=True)
        embed.add_field(name="Due Ping Sent", value=self._format_timestamp(reminder_item.fired_at), inline=True)
        embed.add_field(name="Repeat", value=reminder_item.repeat, inline=True)
        embed.add_field(name="Paused Repeat", value=reminder_item.paused_repeat or "none", inline=True)
        embed.add_field(name="Reminder", value=self._format_reminder_body(reminder_item.reminder), inline=False)
        embed.set_footer(text="Stored in reminders list channel for persistence")
        return embed

    def _build_due_soon_embed(self, user_id: int, reminder_item: ReminderItem) -> discord.Embed:
        embed = discord.Embed(
            title=f"Upcoming Reminder #{reminder_item.id}",
            color=REMINDER_DUE_SOON_COLOR,
        )
        embed.add_field(name="Owner", value=f"<@{user_id}>", inline=True)
        embed.add_field(
            name="Due",
            value=f"{self._format_timestamp(reminder_item.due_at)} ({self._format_relative_timestamp(reminder_item.due_at)})",
            inline=True,
        )
        embed.add_field(name="Reminder", value=self._format_reminder_body(reminder_item.reminder), inline=False)
        embed.set_footer(text="Reminder for the next 24 hours")
        return embed

    def _build_due_now_embed(self, user_id: int, reminder_item: ReminderItem) -> discord.Embed:
        embed = discord.Embed(
            title=f"Reminder #{reminder_item.id} — Due Now",
            color=REMINDER_DUE_NOW_COLOR,
        )
        embed.add_field(name="Owner", value=f"<@{user_id}>", inline=True)
        embed.add_field(
            name="Due",
            value=f"{self._format_timestamp(reminder_item.due_at)} ({self._format_relative_timestamp(reminder_item.due_at)})",
            inline=True,
        )
        embed.add_field(name="Reminder", value=self._format_reminder_body(reminder_item.reminder), inline=False)
        embed.set_footer(text="This reminder is now due.")
        return embed

    async def _mark_reminder_done_in_channel(self, user_id: int, reminder_item: ReminderItem) -> None:
        key = (user_id, reminder_item.id)
        message_id = self.reminder_message_by_key.get(key)
        if message_id is None:
            return

        channel = await self._resolve_channel(reminder_list_channel_id)
        if channel is None or not hasattr(channel, "fetch_message"):
            return

        try:
            message = await channel.fetch_message(message_id)
            embed = self._build_done_reminder_embed(user_id, reminder_item)
            focus_text = self._build_focus_heading(reminder_item.reminder, "✅")
            await message.edit(content=focus_text, embed=embed)
        except discord.DiscordException:
            return

    def _build_done_reminder_embed(self, user_id: int, reminder_item: ReminderItem) -> discord.Embed:
        embed = discord.Embed(
            title=f"Reminder #{reminder_item.id}",
            color=REMINDER_DONE_COLOR,
        )
        embed.add_field(name="Owner", value=f"<@{user_id}>", inline=True)
        embed.add_field(
            name="Due",
            value=f"{self._format_timestamp(reminder_item.due_at)} ({self._format_relative_timestamp(reminder_item.due_at)})",
            inline=True,
        )
        embed.add_field(name="Status", value="Done", inline=True)
        embed.add_field(name="Created", value=self._format_timestamp(reminder_item.created_at), inline=True)
        embed.add_field(name="Repeat", value=reminder_item.repeat, inline=True)
        embed.add_field(name="Done At", value=self._format_timestamp(datetime.now(timezone.utc)), inline=True)
        embed.add_field(name="Reminder", value=self._format_reminder_body(reminder_item.reminder), inline=False)
        embed.set_footer(text="This reminder has been marked as done.")
        return embed

    def _to_int(self, value: str | None) -> int | None:
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def _build_focus_heading(self, reminder_text: str, icon: str) -> str:
        single_line = " ".join(reminder_text.splitlines()).strip()
        if len(single_line) > 120:
            single_line = single_line[:117] + "..."
        return f"## {icon} {single_line}"

    def _format_reminder_body(self, reminder_text: str) -> str:
        cleaned = reminder_text.replace("```", "`\u200b``")
        lines = cleaned.splitlines() or [cleaned]
        return "\n".join(f"> {line}" if line else ">" for line in lines)

    def _format_timestamp(self, value: datetime | None) -> str:
        if value is None:
            return "N/A"
        unix_ts = int(value.timestamp())
        return f"<t:{unix_ts}:F>"

    def _format_relative_timestamp(self, value: datetime | None) -> str:
        if value is None:
            return "N/A"
        unix_ts = int(value.timestamp())
        return f"<t:{unix_ts}:R>"

    def _find_reminder(self, user_id: int, reminder_id: int) -> ReminderItem | None:
        reminders = self.reminder_service.list_reminders(user_id)
        for reminder_item in reminders:
            if reminder_item.id == reminder_id:
                return reminder_item
        return None

    def _parse_legacy_reminder_args(self, args: str) -> tuple[str, datetime, str]:
        try:
            tokens = shlex.split(args)
        except ValueError as error:
            raise ValueError(f"Could not parse arguments: {error}") from error

        if len(tokens) < 5:
            raise ValueError("Missing arguments.")

        reminder_text = tokens[0].strip()
        if not reminder_text:
            raise ValueError("Reminder description cannot be empty.")

        options: Dict[str, str] = {}
        index = 1
        while index < len(tokens):
            key = tokens[index]
            if key not in ("-d", "-t", "-r"):
                raise ValueError(f"Unknown option `{key}`.")
            if index + 1 >= len(tokens):
                raise ValueError(f"Option `{key}` requires a value.")
            options[key] = tokens[index + 1]
            index += 2

        date_text = options.get("-d")
        time_text = options.get("-t")
        if not date_text or not time_text:
            raise ValueError("Both `-d` and `-t` are required.")

        repeat = options.get("-r", "none")
        return reminder_text, self._parse_due_datetime_parts(date_text, time_text, "UTC"), repeat

    def _parse_due_datetime_parts(self, date_text: str, time_text: str, timezone_name: str = "UTC") -> datetime:
        if not re.fullmatch(r"\d{4}/\d{2}/\d{2}", date_text):
            raise ValueError("Date must be in `yyyy/mm/dd` format.")
        if not re.fullmatch(r"\d{2}:\d{2}", time_text):
            raise ValueError("Time must be in `HH:MM` 24-hour format.")

        try:
            naive_due = datetime.strptime(f"{date_text} {time_text}", "%Y/%m/%d %H:%M")
        except ValueError as error:
            raise ValueError(f"Invalid date/time: {error}") from error

        tzinfo = self._resolve_timezone_name(timezone_name)
        due_local = naive_due.replace(tzinfo=tzinfo)
        return due_local.astimezone(timezone.utc)

    def _resolve_timezone_name(self, timezone_name: str) -> ZoneInfo:
        cleaned = (timezone_name or "UTC").strip()
        try:
            return ZoneInfo(cleaned)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"Unknown timezone `{cleaned}`.") from error

    async def _report_error(self, source: str, error: BaseException) -> None:
        reporter = getattr(self.bot, "error_reporter", None)
        if reporter is None:
            return
        await reporter.report_exception(source, error)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReminderCog(bot))
