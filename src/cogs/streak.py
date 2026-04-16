# src/cogs/streak.py

import discord
from discord import app_commands
from discord.abc import Messageable
from discord.ext import commands, tasks
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime

from config import streak_channel_id, streak_list_channel_id
from services.streak_service import (
    StreakService,
    StreakStats,
    StreakLog,
    StreakNotFoundError,
    StreakValidationError,
    AlreadyLoggedTodayError,
    MILESTONE_DAYS,
)

MOOD_EMOJI = {1: "😞", 2: "😐", 3: "🙂", 4: "😀", 5: "🔥"}
STREAK_CARD_COLOR = 0x6366F1
STREAK_DONE_COLOR = 0x10B981
MILESTONE_COLOR   = 0xF59E0B
LOGS_PER_PAGE     = 10


class StreakHistoryView(discord.ui.View):
    def __init__(self, user_id: int, streak_name: str, logs: list[StreakLog]) -> None:
        super().__init__(timeout=120)
        self.user_id = user_id
        self.streak_name = streak_name
        self.logs = logs
        self.page = 0
        self.total_pages = max(1, (len(logs) + LOGS_PER_PAGE - 1) // LOGS_PER_PAGE)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= self.total_pages - 1

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"📋 {self.streak_name} — Full History",
            color=STREAK_CARD_COLOR,
        )
        if not self.logs:
            embed.description = "No logs yet."
            return embed

        start = self.page * LOGS_PER_PAGE
        page_logs = self.logs[start : start + LOGS_PER_PAGE]
        lines = []
        for log in page_logs:
            mood_str = f" {MOOD_EMOJI[log.mood]}" if log.mood else ""
            tag_str = f" `{', '.join(log.tags)}`" if log.tags else ""
            note_preview = (log.note[:80] + "…") if log.note and len(log.note) > 80 else (log.note or "")
            note_str = f" — {note_preview}" if note_preview else ""
            lines.append(f"`{log.logged_at.date()}`{mood_str}{tag_str}{note_str}")

        embed.description = "\n".join(lines)
        embed.set_footer(text=f"Page {self.page + 1} of {self.total_pages} • {len(self.logs)} total logs")
        return embed

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your history.", ephemeral=True)
            return
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your history.", ephemeral=True)
            return
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class StreakLogModal(discord.ui.Modal, title="Log Activity"):
    note = discord.ui.TextInput(
        label="Note (optional)",
        placeholder="e.g. Legs day — felt strong today.",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph,
    )
    mood = discord.ui.TextInput(
        label="Mood (1=awful – 5=amazing)",
        placeholder="Enter a number 1–5",
        required=False,
        max_length=1,
    )
    tags = discord.ui.TextInput(
        label="Tags (optional, comma-separated)",
        placeholder="e.g. legs, cardio, heavy",
        required=False,
        max_length=200,
    )

    def __init__(self, cog: "StreakCog", streak_id: int, streak_name: str) -> None:
        super().__init__(title=f"Log: {streak_name}")
        self.cog = cog
        self.streak_id = streak_id
        self.streak_name = streak_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        mood_val: int | None = None
        if self.mood.value.strip():
            try:
                mood_val = int(self.mood.value.strip())
                if not 1 <= mood_val <= 5:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message(
                    "Mood must be a number between 1 and 5.", ephemeral=True
                )
                return

        tag_list = [t.strip() for t in self.tags.value.split(",") if t.strip()]

        try:
            await self.cog.service.log_activity(
                interaction.user.id,
                self.streak_id,
                note=self.note.value.strip() or None,
                mood=mood_val,
                tags=tag_list,
            )
            mood_str = f" {MOOD_EMOJI[mood_val]}" if mood_val else ""
            await interaction.response.send_message(
                f"Logged **{self.streak_name}**!{mood_str} 🔥"
            )
            await self.cog._refresh_streak_card(interaction.user.id, self.streak_id)
            await self.cog._remove_due_card(interaction.user.id, self.streak_id)
            await self.cog._check_milestone(interaction.user, self.streak_id)
        except AlreadyLoggedTodayError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except StreakValidationError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except Exception as e:
            await self.cog._report_error("StreakLogModal.on_submit", e)
            await interaction.response.send_message(
                "Something went wrong. Please try again.", ephemeral=True
            )


class StreakEditModal(discord.ui.Modal, title="Edit Streak"):
    name = discord.ui.TextInput(
        label="Name",
        max_length=64,
        required=True,
    )
    schedule = discord.ui.TextInput(
        label="Schedule",
        placeholder="'daily' or e.g. 'mon,wed,fri'",
        max_length=50,
        required=False,
    )
    description = discord.ui.TextInput(
        label="Description (optional)",
        max_length=200,
        required=False,
        style=discord.TextStyle.paragraph,
    )

    def __init__(
        self,
        cog: "StreakCog",
        streak_id: int,
        current_name: str,
        current_schedule: str,
        current_desc: str | None,
    ) -> None:
        super().__init__(title=f"Edit: {current_name}")
        self.cog = cog
        self.streak_id = streak_id
        self.name.default = current_name
        self.schedule.default = current_schedule
        self.description.default = current_desc or ""

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            updated = await self.cog.service.update_streak(
                interaction.user.id,
                self.streak_id,
                name=self.name.value.strip() or None,
                description=self.description.value.strip() or None,
                schedule=self.schedule.value.strip() or None,
            )
            await interaction.response.send_message(
                f"Updated streak **{updated.name}**."
            )
            await self.cog._refresh_streak_card(interaction.user.id, self.streak_id)
        except StreakValidationError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except StreakNotFoundError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except Exception as e:
            await self.cog._report_error("StreakEditModal.on_submit", e)
            await interaction.response.send_message(
                "Something went wrong. Please try again.", ephemeral=True
            )


class StreakCog(commands.GroupCog, group_name="streak", group_description="Manage your streaks"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.service = StreakService()
        self.streak_card_map: dict[tuple[int, int], int] = {}  # (user_id, streak_id) -> message_id in list channel
        self.due_card_map: dict[tuple[int, int], int] = {}     # (user_id, streak_id) -> message_id in streak channel

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.weekly_summary_task.is_running():
            self.weekly_summary_task.start()
        if not self.daily_due_task.is_running():
            self.daily_due_task.start()

    def cog_unload(self) -> None:
        self.weekly_summary_task.cancel()
        self.daily_due_task.cancel()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @app_commands.command(name="create", description="Create a new streak tracker")
    @app_commands.describe(
        name="Name for the streak (e.g. 'Gym')",
        schedule="Active days: 'daily' or e.g. 'mon,wed,fri' (default: daily)",
        description="Optional description",
    )
    async def create_streak(
        self,
        interaction: discord.Interaction,
        name: str,
        schedule: str = "daily",
        description: str | None = None,
    ) -> None:
        try:
            streak = await self.service.create_streak(
                interaction.user.id, name, description, schedule
            )
            await interaction.response.send_message(
                f"Created streak **{streak.name}** (schedule: `{streak.schedule}`) 🎯"
            )
            await self._post_streak_card(interaction.user, streak.id)
        except StreakValidationError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except Exception as e:
            await self._report_error("StreakCog.create_streak", e)
            await interaction.response.send_message(
                "Something went wrong. Please try again.", ephemeral=True
            )

    @app_commands.command(name="log", description="Log today's activity for a streak")
    @app_commands.describe(name="Name of the streak to log")
    async def log_streak(self, interaction: discord.Interaction, name: str) -> None:
        try:
            streak = await self.service.get_streak_by_name(interaction.user.id, name)
            modal = StreakLogModal(self, streak.id, streak.name)
            await interaction.response.send_modal(modal)
        except StreakNotFoundError:
            await interaction.response.send_message(
                f"No streak named '{name}'. Use `/streak list` to see yours.",
                ephemeral=True,
            )

    @app_commands.command(name="list", description="List all your streaks")
    async def list_streaks(self, interaction: discord.Interaction) -> None:
        all_stats = await self.service.get_all_stats(interaction.user.id)
        if not all_stats:
            await interaction.response.send_message(
                "No streaks yet. Use `/streak create` to start one."
            )
            return
        lines = []
        for s in all_stats:
            fire = "🔥" * min(s.current_streak, 5)
            lines.append(
                f"**{s.streak.name}** — {s.current_streak} day streak {fire} "
                f"(best: {s.best_streak}) — {s.total_logs} total logs"
            )
        await interaction.response.send_message("\n".join(lines))

    @app_commands.command(name="delete", description="Delete a streak")
    @app_commands.describe(name="Name of the streak to delete")
    async def delete_streak(self, interaction: discord.Interaction, name: str) -> None:
        try:
            streak = await self.service.get_streak_by_name(interaction.user.id, name)
            deleted = await self.service.delete_streak(interaction.user.id, streak.id)
            await interaction.response.send_message(
                f"Deleted streak **{deleted.name}** and all its logs."
            )
            await self._delete_streak_card(interaction.user.id, deleted.id)
        except StreakNotFoundError:
            await interaction.response.send_message(
                f"No streak named '{name}'.", ephemeral=True
            )

    @app_commands.command(name="view", description="View detailed stats for a streak")
    @app_commands.describe(name="Name of the streak")
    async def view_streak(self, interaction: discord.Interaction, name: str) -> None:
        try:
            streak = await self.service.get_streak_by_name(interaction.user.id, name)
            stats = await self.service.get_stats(interaction.user.id, streak.id)
            embed = self._build_streak_card(interaction.user.id, stats)
            if stats.recent_logs:
                log_lines = []
                for log in stats.recent_logs[:5]:
                    mood_str = f" {MOOD_EMOJI[log.mood]}" if log.mood else ""
                    tag_str = f" `{', '.join(log.tags)}`" if log.tags else ""
                    note_str = (
                        f" — {log.note[:60]}..."
                        if log.note and len(log.note) > 60
                        else (f" — {log.note}" if log.note else "")
                    )
                    log_lines.append(f"`{log.logged_at.date()}`{mood_str}{tag_str}{note_str}")
                embed.add_field(
                    name="Recent Logs",
                    value="\n".join(log_lines),
                    inline=False,
                )
            await interaction.response.send_message(embed=embed)
        except StreakNotFoundError:
            await interaction.response.send_message(
                f"No streak named '{name}'.", ephemeral=True
            )

    @app_commands.command(name="history", description="View full log history for a streak")
    @app_commands.describe(name="Name of the streak")
    async def streak_history(self, interaction: discord.Interaction, name: str) -> None:
        try:
            streak = await self.service.get_streak_by_name(interaction.user.id, name)
            logs = await self.service.get_logs(interaction.user.id, streak.id)
            view = StreakHistoryView(interaction.user.id, streak.name, logs)
            await interaction.response.send_message(embed=view.build_embed(), view=view)
        except StreakNotFoundError:
            await interaction.response.send_message(
                f"No streak named '{name}'.", ephemeral=True
            )

    @app_commands.command(name="edit", description="Edit an existing streak")
    @app_commands.describe(name="Name of the streak to edit")
    async def edit_streak(self, interaction: discord.Interaction, name: str) -> None:
        try:
            streak = await self.service.get_streak_by_name(interaction.user.id, name)
            modal = StreakEditModal(
                self,
                streak.id,
                streak.name,
                streak.schedule,
                streak.description,
            )
            await interaction.response.send_modal(modal)
        except StreakNotFoundError:
            await interaction.response.send_message(
                f"No streak named '{name}'.", ephemeral=True
            )

    @app_commands.command(name="freeze", description="Spend a freeze token to protect yesterday's streak")
    @app_commands.describe(name="Name of the streak")
    async def freeze_streak(self, interaction: discord.Interaction, name: str) -> None:
        try:
            streak = await self.service.get_streak_by_name(interaction.user.id, name)
            updated = await self.service.spend_freeze(interaction.user.id, streak.id)
            await interaction.response.send_message(
                f"Used a freeze token on **{updated.name}**. "
                f"Tokens remaining: {updated.freeze_tokens} 🧊"
            )
            await self._refresh_streak_card(interaction.user.id, updated.id)
        except (StreakNotFoundError, StreakValidationError) as e:
            await interaction.response.send_message(str(e), ephemeral=True)

    # ------------------------------------------------------------------
    # Reactions
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.user_id == getattr(self.bot.user, "id", None):
            return
        if str(payload.emoji) != "✅":
            return

        # Check both maps: persistent streak cards and daily due cards
        for card_map in (self.streak_card_map, self.due_card_map):
            for (user_id, streak_id), message_id in list(card_map.items()):
                if message_id == payload.message_id and user_id == payload.user_id:
                    await self._handle_quick_log(user_id, streak_id)
                    return

    async def _handle_quick_log(self, user_id: int, streak_id: int) -> None:
        try:
            await self.service.log_activity(user_id, streak_id)
            await self._refresh_streak_card(user_id, streak_id)
            await self._remove_due_card(user_id, streak_id)
            user = await self.bot.fetch_user(user_id)
            await self._check_milestone(user, streak_id)
        except AlreadyLoggedTodayError:
            pass  # silent — already logged today
        except Exception as e:
            await self._report_error("StreakCog._handle_quick_log", e)

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    @tasks.loop(time=dtime(hour=8, minute=0))
    async def daily_due_task(self) -> None:
        """Every morning: clear yesterday's due cards, post today's for unlogged streaks."""
        # Clear previous due cards
        for message_id in list(self.due_card_map.values()):
            await self._delete_message(streak_channel_id, message_id)
        self.due_card_map.clear()

        # Post due cards for every streak that is scheduled today and not yet logged
        all_streaks = await self.service.get_all_streaks()
        for streak in all_streaks:
            if not self.service.is_scheduled_today(streak):
                continue
            if await self.service.is_logged_today(streak.id):
                continue
            await self._post_due_card(streak.user_id, streak.id)

    @tasks.loop(time=dtime(hour=9, minute=0))
    async def weekly_summary_task(self) -> None:
        if datetime.now(timezone.utc).weekday() != 0:  # Monday only
            return
        all_streaks = await self.service.get_all_streaks()
        user_ids = {s.user_id for s in all_streaks}
        channel = await self._resolve_channel(streak_channel_id)
        if channel is None:
            return
        for user_id in user_ids:
            all_stats = await self.service.get_all_stats(user_id)
            if not all_stats:
                continue
            lines = [f"**Weekly Streak Summary for <@{user_id}>**"]
            for s in all_stats:
                lines.append(
                    f"• **{s.streak.name}**: {s.current_streak} day streak "
                    f"{'🔥' * min(s.current_streak, 5)}"
                )
            await channel.send("\n".join(lines))

    # ------------------------------------------------------------------
    # Milestone detection
    # ------------------------------------------------------------------

    async def _check_milestone(
        self, user: discord.User | discord.Member, streak_id: int
    ) -> None:
        stats = await self.service.get_stats(user.id, streak_id)
        if stats.current_streak in MILESTONE_DAYS:
            channel = await self._resolve_channel(streak_channel_id)
            if channel is None:
                return
            embed = discord.Embed(
                title=f"🏆 {stats.current_streak}-Day Streak Milestone!",
                description=(
                    f"<@{user.id}> has hit **{stats.current_streak} days** "
                    f"on their **{stats.streak.name}** streak! Keep it up!"
                ),
                color=MILESTONE_COLOR,
            )
            await channel.send(embed=embed)

    # ------------------------------------------------------------------
    # Due card helpers
    # ------------------------------------------------------------------

    def _build_due_card(self, user_id: int, stats: StreakStats) -> discord.Embed:
        s = stats.streak
        fire = "🔥" * min(stats.current_streak, 5) if stats.current_streak else ""
        embed = discord.Embed(
            title=f"⏰ {s.name}",
            description=(
                f"<@{user_id}> — **{stats.current_streak}** day streak {fire}\n"
                f"Log it to keep the streak going!"
            ),
            color=0xF59E0B,
        )
        return embed

    async def _post_due_card(self, user_id: int, streak_id: int) -> None:
        channel = await self._resolve_channel(streak_channel_id)
        if channel is None:
            return
        try:
            stats = await self.service.get_stats(user_id, streak_id)
        except Exception:
            return
        embed = self._build_due_card(user_id, stats)
        message = await channel.send(content=f"<@{user_id}>", embed=embed)
        await message.add_reaction("✅")
        self.due_card_map[(user_id, streak_id)] = message.id

    async def _remove_due_card(self, user_id: int, streak_id: int) -> None:
        key = (user_id, streak_id)
        message_id = self.due_card_map.pop(key, None)
        if message_id:
            await self._delete_message(streak_channel_id, message_id)

    # ------------------------------------------------------------------
    # Streak card helpers
    # ------------------------------------------------------------------

    def _build_streak_card(self, user_id: int, stats: StreakStats) -> discord.Embed:
        s = stats.streak
        embed = discord.Embed(title=f"🔥 {s.name}", color=STREAK_CARD_COLOR)
        embed.add_field(name="Current Streak", value=f"**{stats.current_streak}** days", inline=True)
        embed.add_field(name="Best Streak",    value=f"**{stats.best_streak}** days",    inline=True)
        embed.add_field(name="Total Logs",     value=str(stats.total_logs),               inline=True)
        embed.add_field(name="Owner",          value=f"<@{user_id}>",                    inline=True)
        embed.add_field(name="Schedule",       value=f"`{s.schedule}`",                  inline=True)
        embed.add_field(name="Freeze Tokens",  value=str(s.freeze_tokens),               inline=True)
        grid = self._build_activity_grid(stats)
        embed.add_field(name="Last 7 Days", value=grid, inline=False)
        if stats.last_logged:
            embed.add_field(name="Last Logged", value=stats.last_logged.isoformat(), inline=True)
        if stats.avg_mood is not None:
            embed.add_field(name="Avg Mood", value=MOOD_EMOJI.get(round(stats.avg_mood), ""), inline=True)
        if s.description:
            embed.set_footer(text=s.description)
        return embed

    def _build_activity_grid(self, stats: StreakStats) -> str:
        logged_dates = {log.logged_at.date() for log in stats.recent_logs}
        today = date.today()
        grid = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            grid.append("🟩" if d in logged_dates else "⬜")
        return "".join(grid) + "  ← today"

    async def _post_streak_card(
        self, user: discord.User | discord.Member, streak_id: int
    ) -> None:
        channel = await self._resolve_channel(streak_list_channel_id)
        if channel is None:
            return
        stats = await self.service.get_stats(user.id, streak_id)
        embed = self._build_streak_card(user.id, stats)
        message = await channel.send(content=f"<@{user.id}>", embed=embed)
        await message.add_reaction("✅")
        self.streak_card_map[(user.id, streak_id)] = message.id

    async def _refresh_streak_card(self, user_id: int, streak_id: int) -> None:
        key = (user_id, streak_id)
        message_id = self.streak_card_map.get(key)
        if message_id is None:
            return
        channel = await self._resolve_channel(streak_list_channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(message_id)
            stats = await self.service.get_stats(user_id, streak_id)
            embed = self._build_streak_card(user_id, stats)
            await message.edit(embed=embed)
        except discord.NotFound:
            self.streak_card_map.pop(key, None)

    async def _delete_streak_card(self, user_id: int, streak_id: int) -> None:
        key = (user_id, streak_id)
        message_id = self.streak_card_map.pop(key, None)
        if message_id:
            await self._delete_message(streak_list_channel_id, message_id)

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    async def _resolve_channel(self, channel_id: str | None) -> Messageable | None:
        if not channel_id:
            return None
        try:
            channel_id_int = int(channel_id)
        except ValueError:
            return None
        cached = self.bot.get_channel(channel_id_int)
        if cached is not None:
            return cached
        try:
            fetched = await self.bot.fetch_channel(channel_id_int)
        except discord.DiscordException:
            return None
        if isinstance(fetched, Messageable):
            return fetched
        return None

    async def _delete_message(self, channel_id: str | None, message_id: int) -> None:
        channel = await self._resolve_channel(channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
        except discord.NotFound:
            pass
        except discord.DiscordException:
            pass

    async def _report_error(self, source: str, error: BaseException) -> None:
        reporter = getattr(self.bot, "error_reporter", None)
        if reporter is None:
            return
        await reporter.report_exception(source, error)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StreakCog(bot))
