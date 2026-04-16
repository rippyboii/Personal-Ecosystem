from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from cogs.streak import StreakCog, StreakEditModal, StreakLogModal
from services.streak_service import (
    AlreadyLoggedTodayError,
    StreakLog,
    StreakNotFoundError,
    StreakRecord,
    StreakStats,
    StreakValidationError,
)


def make_cog() -> StreakCog:
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 9999
    return StreakCog(bot)


def make_interaction(user_id: int = 1) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.user.display_name = "Tester"
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    return interaction


def make_streak_record(
    streak_id: int = 1,
    user_id: int = 1,
    name: str = "Gym",
    slug: str = "gym",
    schedule: str = "daily",
    freeze_tokens: int = 0,
    description: str | None = None,
) -> StreakRecord:
    return StreakRecord(
        id=streak_id,
        user_id=user_id,
        name=name,
        slug=slug,
        description=description,
        schedule=schedule,
        freeze_tokens=freeze_tokens,
        created_at=datetime.now(timezone.utc),
    )


def make_stats(streak: StreakRecord | None = None, current: int = 3) -> StreakStats:
    s = streak or make_streak_record()
    return StreakStats(
        streak=s,
        current_streak=current,
        best_streak=7,
        total_logs=10,
        last_logged=date.today(),
        avg_mood=4.0,
        recent_logs=[],
    )


# ---------------------------------------------------------------------------
# create_streak
# ---------------------------------------------------------------------------

class TestCreateStreak:
    def setup_method(self):
        self.cog = make_cog()
        self.cog.service = AsyncMock()
        self.cog._post_streak_card = AsyncMock()

    async def test_sends_confirmation_with_name(self):
        self.cog.service.create_streak = AsyncMock(return_value=make_streak_record())
        interaction = make_interaction()
        await self.cog.create_streak.callback(self.cog, interaction, "Gym")
        msg = interaction.response.send_message.call_args[0][0]
        assert "Gym" in msg

    async def test_posts_streak_card_on_success(self):
        self.cog.service.create_streak = AsyncMock(return_value=make_streak_record())
        interaction = make_interaction()
        await self.cog.create_streak.callback(self.cog, interaction, "Gym")
        self.cog._post_streak_card.assert_called_once()

    async def test_validation_error_is_ephemeral(self):
        self.cog.service.create_streak = AsyncMock(
            side_effect=StreakValidationError("bad name")
        )
        interaction = make_interaction()
        await self.cog.create_streak.callback(self.cog, interaction, "")
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# log_streak
# ---------------------------------------------------------------------------

class TestLogStreak:
    def setup_method(self):
        self.cog = make_cog()
        self.cog.service = AsyncMock()

    async def test_opens_modal_for_valid_streak(self):
        self.cog.service.get_streak_by_name = AsyncMock(return_value=make_streak_record())
        interaction = make_interaction()
        await self.cog.log_streak.callback(self.cog, interaction, "Gym")
        interaction.response.send_modal.assert_called_once()
        assert isinstance(interaction.response.send_modal.call_args[0][0], StreakLogModal)

    async def test_not_found_sends_ephemeral(self):
        self.cog.service.get_streak_by_name = AsyncMock(
            side_effect=StreakNotFoundError("not found")
        )
        interaction = make_interaction()
        await self.cog.log_streak.callback(self.cog, interaction, "NoExist")
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# list_streaks
# ---------------------------------------------------------------------------

class TestListStreaks:
    def setup_method(self):
        self.cog = make_cog()
        self.cog.service = AsyncMock()

    async def test_no_streaks_sends_empty_message(self):
        self.cog.service.get_all_stats = AsyncMock(return_value=[])
        interaction = make_interaction()
        await self.cog.list_streaks.callback(self.cog, interaction)
        msg = interaction.response.send_message.call_args[0][0]
        assert "No streaks" in msg

    async def test_lists_streak_names(self):
        stats = [
            make_stats(make_streak_record(name="Gym")),
            make_stats(make_streak_record(streak_id=2, name="Read", slug="read")),
        ]
        self.cog.service.get_all_stats = AsyncMock(return_value=stats)
        interaction = make_interaction()
        await self.cog.list_streaks.callback(self.cog, interaction)
        msg = interaction.response.send_message.call_args[0][0]
        assert "Gym" in msg
        assert "Read" in msg

    async def test_includes_streak_count(self):
        stats = [make_stats(make_streak_record(), current=5)]
        self.cog.service.get_all_stats = AsyncMock(return_value=stats)
        interaction = make_interaction()
        await self.cog.list_streaks.callback(self.cog, interaction)
        msg = interaction.response.send_message.call_args[0][0]
        assert "5" in msg


# ---------------------------------------------------------------------------
# delete_streak
# ---------------------------------------------------------------------------

class TestDeleteStreak:
    def setup_method(self):
        self.cog = make_cog()
        self.cog.service = AsyncMock()
        self.cog._delete_streak_card = AsyncMock()

    async def test_sends_deletion_confirmation(self):
        streak = make_streak_record()
        self.cog.service.get_streak_by_name = AsyncMock(return_value=streak)
        self.cog.service.delete_streak = AsyncMock(return_value=streak)
        interaction = make_interaction()
        await self.cog.delete_streak.callback(self.cog, interaction, "Gym")
        msg = interaction.response.send_message.call_args[0][0]
        assert "Gym" in msg

    async def test_deletes_streak_card(self):
        streak = make_streak_record()
        self.cog.service.get_streak_by_name = AsyncMock(return_value=streak)
        self.cog.service.delete_streak = AsyncMock(return_value=streak)
        interaction = make_interaction()
        await self.cog.delete_streak.callback(self.cog, interaction, "Gym")
        self.cog._delete_streak_card.assert_called_once_with(1, streak.id)

    async def test_not_found_sends_ephemeral(self):
        self.cog.service.get_streak_by_name = AsyncMock(
            side_effect=StreakNotFoundError("not found")
        )
        interaction = make_interaction()
        await self.cog.delete_streak.callback(self.cog, interaction, "NoExist")
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# freeze_streak
# ---------------------------------------------------------------------------

class TestFreezeStreak:
    def setup_method(self):
        self.cog = make_cog()
        self.cog.service = AsyncMock()
        self.cog._refresh_streak_card = AsyncMock()

    async def test_sends_token_count(self):
        streak = make_streak_record(freeze_tokens=2)
        updated = make_streak_record(freeze_tokens=1)
        self.cog.service.get_streak_by_name = AsyncMock(return_value=streak)
        self.cog.service.spend_freeze = AsyncMock(return_value=updated)
        interaction = make_interaction()
        await self.cog.freeze_streak.callback(self.cog, interaction, "Gym")
        msg = interaction.response.send_message.call_args[0][0]
        assert "1" in msg

    async def test_no_tokens_sends_ephemeral(self):
        streak = make_streak_record()
        self.cog.service.get_streak_by_name = AsyncMock(return_value=streak)
        self.cog.service.spend_freeze = AsyncMock(
            side_effect=StreakValidationError("No freeze tokens available.")
        )
        interaction = make_interaction()
        await self.cog.freeze_streak.callback(self.cog, interaction, "Gym")
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# _build_streak_card
# ---------------------------------------------------------------------------

class TestBuildStreakCard:
    def setup_method(self):
        self.cog = make_cog()

    def test_title_contains_streak_name(self):
        stats = make_stats()
        embed = self.cog._build_streak_card(1, stats)
        assert "Gym" in embed.title

    def test_has_current_streak_field(self):
        stats = make_stats()
        embed = self.cog._build_streak_card(1, stats)
        field_names = [f.name for f in embed.fields]
        assert "Current Streak" in field_names

    def test_has_best_streak_field(self):
        stats = make_stats()
        embed = self.cog._build_streak_card(1, stats)
        field_names = [f.name for f in embed.fields]
        assert "Best Streak" in field_names

    def test_has_freeze_tokens_field(self):
        stats = make_stats()
        embed = self.cog._build_streak_card(1, stats)
        field_names = [f.name for f in embed.fields]
        assert "Freeze Tokens" in field_names

    def test_description_set_as_footer(self):
        streak = make_streak_record(description="My daily gym habit")
        stats = make_stats(streak=streak)
        embed = self.cog._build_streak_card(1, stats)
        assert embed.footer.text == "My daily gym habit"

    def test_no_footer_when_no_description(self):
        stats = make_stats()
        embed = self.cog._build_streak_card(1, stats)
        assert embed.footer.text is None or embed.footer.text == discord.Embed.Empty


# ---------------------------------------------------------------------------
# _build_activity_grid
# ---------------------------------------------------------------------------

class TestBuildActivityGrid:
    def setup_method(self):
        self.cog = make_cog()

    def test_grid_has_seven_cells(self):
        stats = make_stats()
        grid = self.cog._build_activity_grid(stats)
        cell_count = grid.count("🟩") + grid.count("⬜")
        assert cell_count == 7

    def test_ends_with_today_label(self):
        stats = make_stats()
        grid = self.cog._build_activity_grid(stats)
        assert "today" in grid

    def test_logged_today_shows_green(self):
        log = MagicMock()
        log.logged_at = datetime.now(timezone.utc)
        stats = make_stats()
        stats.recent_logs = [log]
        grid = self.cog._build_activity_grid(stats)
        assert "🟩" in grid


# ---------------------------------------------------------------------------
# on_raw_reaction_add
# ---------------------------------------------------------------------------

class TestReactionQuickLog:
    def setup_method(self):
        self.cog = make_cog()
        self.cog.service = AsyncMock()
        self.cog._refresh_streak_card = AsyncMock()
        self.cog._check_milestone = AsyncMock()

    def _make_payload(self, user_id: int, message_id: int, emoji: str) -> MagicMock:
        payload = MagicMock()
        payload.user_id = user_id
        payload.message_id = message_id
        payload.emoji = emoji  # str(emoji) returns the emoji string directly
        return payload

    async def test_wrong_emoji_is_ignored(self):
        payload = self._make_payload(1, 100, "❌")
        await self.cog.on_raw_reaction_add(payload)
        self.cog.service.log_activity.assert_not_called()

    async def test_bot_own_reaction_is_ignored(self):
        self.cog.bot.user.id = 1
        payload = self._make_payload(1, 100, "✅")
        await self.cog.on_raw_reaction_add(payload)
        self.cog.service.log_activity.assert_not_called()

    async def test_matching_reaction_logs_activity(self):
        self.cog.streak_card_map[(1, 42)] = 100
        self.cog.service.log_activity = AsyncMock()
        self.cog.bot.fetch_user = AsyncMock(return_value=MagicMock())
        payload = self._make_payload(1, 100, "✅")
        await self.cog.on_raw_reaction_add(payload)
        self.cog.service.log_activity.assert_called_once_with(1, 42)

    async def test_already_logged_is_silent(self):
        self.cog.streak_card_map[(1, 42)] = 100
        self.cog.service.log_activity = AsyncMock(side_effect=AlreadyLoggedTodayError("dup"))
        self.cog.bot.fetch_user = AsyncMock(return_value=MagicMock())
        payload = self._make_payload(1, 100, "✅")
        # Should not raise
        await self.cog.on_raw_reaction_add(payload)

    async def test_wrong_user_does_not_log(self):
        self.cog.streak_card_map[(1, 42)] = 100
        payload = self._make_payload(user_id=2, message_id=100, emoji="✅")
        await self.cog.on_raw_reaction_add(payload)
        self.cog.service.log_activity.assert_not_called()
