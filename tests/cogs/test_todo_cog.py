from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from cogs.todo import TodoCog, TODO_LIST_COLOR, TODO_COMPLETED_COLOR
from services.todo_service import TodoItem, TodoNotFoundError, TodoServiceError, TodoValidationError


def make_cog() -> TodoCog:
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 9999
    return TodoCog(bot)


def make_interaction(user_id: int = 1, display_name: str = "Tester") -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.user.display_name = display_name
    interaction.user.display_avatar.url = "https://example.com/avatar.png"
    interaction.response.send_message = AsyncMock()
    return interaction


def make_embed(title: str = "", description: str = "", fields: list[tuple[str, str]] | None = None) -> discord.Embed:
    embed = discord.Embed(title=title, description=description)
    for name, value in (fields or []):
        embed.add_field(name=name, value=value)
    return embed


# ---------------------------------------------------------------------------
# _format_task_body
# ---------------------------------------------------------------------------

class TestFormatTaskBody:
    def setup_method(self):
        self.cog = make_cog()

    def test_single_line(self):
        assert self.cog._format_task_body("Buy milk") == "> Buy milk"

    def test_multiline(self):
        result = self.cog._format_task_body("Line 1\nLine 2")
        assert result == "> Line 1\n> Line 2"

    def test_empty_lines_get_bare_gt(self):
        result = self.cog._format_task_body("Line 1\n\nLine 3")
        assert result == "> Line 1\n>\n> Line 3"

    def test_triple_backtick_escaped(self):
        result = self.cog._format_task_body("code ```block```")
        assert "```" not in result.replace("`\u200b``", "")


# ---------------------------------------------------------------------------
# _build_focus_heading
# ---------------------------------------------------------------------------

class TestBuildFocusHeading:
    def setup_method(self):
        self.cog = make_cog()

    def test_basic_heading(self):
        assert self.cog._build_focus_heading("Buy milk", "📝") == "## 📝 Buy milk"

    def test_long_text_truncated(self):
        long = "a" * 130
        result = self.cog._build_focus_heading(long, "📝")
        assert result.endswith("...")
        assert len(result) < len(f"## 📝 {long}")

    def test_multiline_collapsed_to_single(self):
        result = self.cog._build_focus_heading("Line 1\nLine 2", "📝")
        assert "\n" not in result
        assert "Line 1 Line 2" in result

    def test_exactly_120_chars_not_truncated(self):
        text = "a" * 120
        result = self.cog._build_focus_heading(text, "📝")
        assert not result.endswith("...")


# ---------------------------------------------------------------------------
# _format_timestamp
# ---------------------------------------------------------------------------

class TestFormatTimestamp:
    def setup_method(self):
        self.cog = make_cog()

    def test_none_returns_na(self):
        assert self.cog._format_timestamp(None) == "N/A"

    def test_datetime_returns_discord_timestamp(self):
        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = self.cog._format_timestamp(dt)
        assert result.startswith("<t:")
        assert result.endswith(":F>")
        assert str(int(dt.timestamp())) in result


# ---------------------------------------------------------------------------
# _extract_todo_id
# ---------------------------------------------------------------------------

class TestExtractTodoId:
    def setup_method(self):
        self.cog = make_cog()

    def test_task_format(self):
        assert self.cog._extract_todo_id("Task #5") == 5

    def test_completed_task_format(self):
        assert self.cog._extract_todo_id("Completed Task #10") == 10

    def test_none_returns_none(self):
        assert self.cog._extract_todo_id(None) is None

    def test_no_match_returns_none(self):
        assert self.cog._extract_todo_id("Some random title") is None

    def test_empty_string_returns_none(self):
        assert self.cog._extract_todo_id("") is None


# ---------------------------------------------------------------------------
# _embed_field_value
# ---------------------------------------------------------------------------

class TestEmbedFieldValue:
    def setup_method(self):
        self.cog = make_cog()

    def test_returns_matching_field_value(self):
        embed = make_embed(fields=[("Owner", "<@123>"), ("Status", "Pending")])
        assert self.cog._embed_field_value(embed, "Owner") == "<@123>"

    def test_case_insensitive(self):
        embed = make_embed(fields=[("TASK", "Do the thing")])
        assert self.cog._embed_field_value(embed, "task") == "Do the thing"

    def test_missing_field_returns_none(self):
        embed = make_embed(fields=[("Other", "value")])
        assert self.cog._embed_field_value(embed, "Missing") is None

    def test_no_fields_returns_none(self):
        embed = make_embed()
        assert self.cog._embed_field_value(embed, "Anything") is None


# ---------------------------------------------------------------------------
# _extract_task
# ---------------------------------------------------------------------------

class TestExtractTask:
    def setup_method(self):
        self.cog = make_cog()

    def test_from_task_field_strips_gt_prefix(self):
        embed = make_embed(fields=[("Task", "> Buy milk")])
        assert self.cog._extract_task(embed, None) == "Buy milk"

    def test_from_task_field_multiline(self):
        embed = make_embed(fields=[("Task", "> Line 1\n> Line 2")])
        result = self.cog._extract_task(embed, None)
        assert "Line 1" in result
        assert "Line 2" in result

    def test_from_embed_description_when_no_task_field(self):
        embed = make_embed(description="Buy milk")
        assert self.cog._extract_task(embed, None) == "Buy milk"

    def test_from_content_heading_when_no_field_or_description(self):
        embed = make_embed()
        content = "## 📝 Buy milk"
        assert self.cog._extract_task(embed, content) == "Buy milk"

    def test_task_field_takes_priority_over_description(self):
        embed = make_embed(description="From description", fields=[("Task", "> From field")])
        assert self.cog._extract_task(embed, None) == "From field"

    def test_all_missing_returns_none(self):
        embed = make_embed()
        assert self.cog._extract_task(embed, None) is None


# ---------------------------------------------------------------------------
# _extract_owner_id
# ---------------------------------------------------------------------------

class TestExtractOwnerId:
    def setup_method(self):
        self.cog = make_cog()

    def test_from_owner_field(self):
        embed = make_embed(fields=[("Owner", "<@12345>")])
        assert self.cog._extract_owner_id(embed, None) == 12345

    def test_from_owner_field_with_exclamation(self):
        embed = make_embed(fields=[("Owner", "<@!67890>")])
        assert self.cog._extract_owner_id(embed, None) == 67890

    def test_from_content_mention(self):
        embed = make_embed()
        assert self.cog._extract_owner_id(embed, "<@99999>") == 99999

    def test_owner_field_takes_priority_over_content(self):
        embed = make_embed(fields=[("Owner", "<@111>")])
        assert self.cog._extract_owner_id(embed, "<@222>") == 111

    def test_no_owner_info_returns_none(self):
        embed = make_embed()
        assert self.cog._extract_owner_id(embed, None) is None


# ---------------------------------------------------------------------------
# _extract_field_timestamp
# ---------------------------------------------------------------------------

class TestExtractFieldTimestamp:
    def setup_method(self):
        self.cog = make_cog()

    def test_valid_discord_timestamp(self):
        dt = datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        ts = int(dt.timestamp())
        embed = make_embed(fields=[("Created", f"<t:{ts}:F>")])
        result = self.cog._extract_field_timestamp(embed, "Created")
        assert result is not None
        assert result.timestamp() == pytest.approx(dt.timestamp(), abs=1)

    def test_missing_field_returns_none(self):
        embed = make_embed()
        assert self.cog._extract_field_timestamp(embed, "Created") is None

    def test_field_without_timestamp_returns_none(self):
        embed = make_embed(fields=[("Created", "not a timestamp")])
        assert self.cog._extract_field_timestamp(embed, "Created") is None


# ---------------------------------------------------------------------------
# _build_todo_list_embed
# ---------------------------------------------------------------------------

class TestBuildTodoListEmbed:
    def setup_method(self):
        self.cog = make_cog()
        self.user = make_interaction().user

    def test_title_contains_task_id(self):
        todo = TodoItem(id=3, task="Buy milk")
        embed = self.cog._build_todo_list_embed(self.user, todo)
        assert embed.title == "Task #3"

    def test_description_is_task_text(self):
        todo = TodoItem(id=1, task="Buy milk")
        embed = self.cog._build_todo_list_embed(self.user, todo)
        assert embed.description == "Buy milk"

    def test_color_is_list_color(self):
        todo = TodoItem(id=1, task="Task")
        embed = self.cog._build_todo_list_embed(self.user, todo)
        assert embed.color.value == TODO_LIST_COLOR

    def test_footer_text(self):
        todo = TodoItem(id=1, task="Task")
        embed = self.cog._build_todo_list_embed(self.user, todo)
        assert embed.footer.text == "React ✅ to complete"

    def test_no_fields_when_no_description(self):
        todo = TodoItem(id=1, task="Task", description=None)
        embed = self.cog._build_todo_list_embed(self.user, todo)
        assert len(embed.fields) == 0

    def test_description_field_shown_when_present(self):
        todo = TodoItem(id=1, task="Task", description="Some details")
        embed = self.cog._build_todo_list_embed(self.user, todo)
        assert len(embed.fields) == 1
        assert "Some details" in embed.fields[0].value

    def test_no_owner_status_created_fields(self):
        todo = TodoItem(id=1, task="Task")
        embed = self.cog._build_todo_list_embed(self.user, todo)
        field_names = [f.name.lower() for f in embed.fields]
        assert "owner" not in field_names
        assert "status" not in field_names
        assert "created" not in field_names


# ---------------------------------------------------------------------------
# _build_todo_completed_embed
# ---------------------------------------------------------------------------

class TestBuildTodoCompletedEmbed:
    def setup_method(self):
        self.cog = make_cog()
        self.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        self.completed_at = datetime(2024, 1, 2, tzinfo=timezone.utc)

    def _make_todo(self, description: str | None = None) -> TodoItem:
        return TodoItem(
            id=7,
            task="Fix the bug",
            completed=True,
            created_at=self.created_at,
            completed_at=self.completed_at,
            description=description,
        )

    def test_title_contains_task_id(self):
        embed = self.cog._build_todo_completed_embed(1, self._make_todo(), "done")
        assert embed.title == "Task #7"

    def test_description_is_task_text(self):
        embed = self.cog._build_todo_completed_embed(1, self._make_todo(), "done")
        assert embed.description == "Fix the bug"

    def test_color_is_completed_color(self):
        embed = self.cog._build_todo_completed_embed(1, self._make_todo(), "done")
        assert embed.color.value == TODO_COMPLETED_COLOR

    def test_footer_text(self):
        embed = self.cog._build_todo_completed_embed(1, self._make_todo(), "done")
        assert embed.footer.text == "Completed"

    def test_created_field_present(self):
        embed = self.cog._build_todo_completed_embed(1, self._make_todo(), "done")
        field_names = [f.name for f in embed.fields]
        assert "Created" in field_names

    def test_completed_field_present(self):
        completed_text = self.cog._format_timestamp(self.completed_at)
        embed = self.cog._build_todo_completed_embed(1, self._make_todo(), completed_text)
        field_names = [f.name for f in embed.fields]
        assert "Completed" in field_names

    def test_completed_field_value_matches_passed_text(self):
        completed_text = self.cog._format_timestamp(self.completed_at)
        embed = self.cog._build_todo_completed_embed(1, self._make_todo(), completed_text)
        completed_field = next(f for f in embed.fields if f.name == "Completed")
        assert completed_field.value == completed_text

    def test_no_description_field_when_none(self):
        embed = self.cog._build_todo_completed_embed(1, self._make_todo(None), "done")
        assert all(f.name != "" or "details" not in f.value for f in embed.fields)

    def test_description_field_shown_when_present(self):
        embed = self.cog._build_todo_completed_embed(1, self._make_todo("Extra context"), "done")
        values = [f.value for f in embed.fields]
        assert any("Extra context" in v for v in values)

    def test_no_owner_field(self):
        embed = self.cog._build_todo_completed_embed(1, self._make_todo(), "done")
        field_names = [f.name.lower() for f in embed.fields]
        assert "owner" not in field_names


# ---------------------------------------------------------------------------
# add_todo command
# ---------------------------------------------------------------------------

class TestAddTodoCommand:
    def setup_method(self):
        self.cog = make_cog()
        self.cog._send_todo_list_update = AsyncMock()
        self._cmd = self.cog.add_todo.callback

    async def test_success_sends_confirmation(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, task="Buy milk")
        interaction.response.send_message.assert_awaited_once()
        msg = interaction.response.send_message.call_args[0][0]
        assert "Buy milk" in msg

    async def test_success_with_description(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, task="Task", description="Details")
        self.cog._send_todo_list_update.assert_awaited_once()

    async def test_sends_todo_list_update_on_success(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, task="Buy milk")
        self.cog._send_todo_list_update.assert_awaited_once()

    async def test_validation_error_sends_ephemeral(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, task="")
        interaction.response.send_message.assert_awaited_once()
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True

    async def test_validation_error_does_not_send_list_update(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, task="")
        self.cog._send_todo_list_update.assert_not_awaited()

    async def test_service_error_sends_ephemeral(self):
        interaction = make_interaction()
        self.cog.todo_service.add_todo = MagicMock(side_effect=TodoServiceError("boom"))
        self.cog._report_error = AsyncMock()
        await self._cmd(self.cog, interaction, task="Task")
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# list_todos command
# ---------------------------------------------------------------------------

class TestListTodosCommand:
    def setup_method(self):
        self.cog = make_cog()
        self._cmd = self.cog.list_todos.callback

    async def test_no_todos_sends_hint_message(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction)
        msg = interaction.response.send_message.call_args[0][0]
        assert "no todos" in msg.lower() or "/todo add" in msg

    async def test_with_todos_lists_them(self):
        interaction = make_interaction()
        self.cog.todo_service.add_todo(interaction.user.id, "Task 1")
        self.cog.todo_service.add_todo(interaction.user.id, "Task 2")
        await self._cmd(self.cog, interaction)
        msg = interaction.response.send_message.call_args[0][0]
        assert "Task 1" in msg
        assert "Task 2" in msg

    async def test_service_error_sends_ephemeral(self):
        interaction = make_interaction()
        self.cog.todo_service.list_todos = MagicMock(side_effect=TodoServiceError("boom"))
        self.cog._report_error = AsyncMock()
        await self._cmd(self.cog, interaction)
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# complete_todo command
# ---------------------------------------------------------------------------

class TestCompleteTodoCommand:
    def setup_method(self):
        self.cog = make_cog()
        self.cog._send_todo_completed_update = AsyncMock()
        self.cog._delete_todo_list_message = AsyncMock()
        self._cmd = self.cog.complete_todo.callback

    async def test_success_sends_confirmation(self):
        interaction = make_interaction()
        todo = self.cog.todo_service.add_todo(interaction.user.id, "Task")
        await self._cmd(self.cog, interaction, todo_id=todo.id)
        msg = interaction.response.send_message.call_args[0][0]
        assert "Task" in msg

    async def test_success_sends_completed_update(self):
        interaction = make_interaction()
        todo = self.cog.todo_service.add_todo(interaction.user.id, "Task")
        await self._cmd(self.cog, interaction, todo_id=todo.id)
        self.cog._send_todo_completed_update.assert_awaited_once()

    async def test_success_deletes_list_message(self):
        interaction = make_interaction()
        todo = self.cog.todo_service.add_todo(interaction.user.id, "Task")
        await self._cmd(self.cog, interaction, todo_id=todo.id)
        self.cog._delete_todo_list_message.assert_awaited_once()

    async def test_not_found_sends_ephemeral(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, todo_id=999)
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True

    async def test_service_error_sends_ephemeral(self):
        interaction = make_interaction()
        self.cog.todo_service.complete_todo = MagicMock(side_effect=TodoServiceError("boom"))
        self.cog._report_error = AsyncMock()
        await self._cmd(self.cog, interaction, todo_id=1)
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# delete_todo command
# ---------------------------------------------------------------------------

class TestDeleteTodoCommand:
    def setup_method(self):
        self.cog = make_cog()
        self.cog._delete_todo_list_message = AsyncMock()
        self.cog._delete_todo_completed_message = AsyncMock()
        self._cmd = self.cog.delete_todo.callback

    async def test_success_sends_confirmation(self):
        interaction = make_interaction()
        todo = self.cog.todo_service.add_todo(interaction.user.id, "Task")
        await self._cmd(self.cog, interaction, todo_id=todo.id)
        msg = interaction.response.send_message.call_args[0][0]
        assert "Task" in msg

    async def test_success_deletes_list_message(self):
        interaction = make_interaction()
        todo = self.cog.todo_service.add_todo(interaction.user.id, "Task")
        await self._cmd(self.cog, interaction, todo_id=todo.id)
        self.cog._delete_todo_list_message.assert_awaited_once()

    async def test_success_deletes_completed_message(self):
        interaction = make_interaction()
        todo = self.cog.todo_service.add_todo(interaction.user.id, "Task")
        await self._cmd(self.cog, interaction, todo_id=todo.id)
        self.cog._delete_todo_completed_message.assert_awaited_once()

    async def test_not_found_sends_ephemeral(self):
        interaction = make_interaction()
        await self._cmd(self.cog, interaction, todo_id=999)
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True

    async def test_service_error_sends_ephemeral(self):
        interaction = make_interaction()
        self.cog.todo_service.delete_todo = MagicMock(side_effect=TodoServiceError("boom"))
        self.cog._report_error = AsyncMock()
        await self._cmd(self.cog, interaction, todo_id=1)
        _, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# on_raw_reaction_add
# ---------------------------------------------------------------------------

class TestOnRawReactionAdd:
    def setup_method(self):
        self.cog = make_cog()
        self.cog._send_todo_completed_update_by_user_id = AsyncMock()
        self.cog._delete_todo_list_message = AsyncMock()
        # Register a todo and map a message to it
        self.todo = self.cog.todo_service.add_todo(1, "Task")
        self.cog.todo_message_map[42] = (1, self.todo.id)

    def _make_payload(self, user_id=1, emoji="\N{WHITE HEAVY CHECK MARK}", message_id=42, channel_id=111111111111111111):
        payload = MagicMock()
        payload.user_id = user_id
        payload.emoji = emoji
        payload.message_id = message_id
        payload.channel_id = channel_id
        return payload

    async def test_valid_reaction_completes_todo(self):
        payload = self._make_payload()
        await self.cog.on_raw_reaction_add(payload)
        assert self.cog.todo_service.list_todos(1)[0].completed is True

    async def test_valid_reaction_sends_completed_update(self):
        payload = self._make_payload()
        await self.cog.on_raw_reaction_add(payload)
        self.cog._send_todo_completed_update_by_user_id.assert_awaited_once()

    async def test_valid_reaction_deletes_list_message(self):
        payload = self._make_payload()
        await self.cog.on_raw_reaction_add(payload)
        self.cog._delete_todo_list_message.assert_awaited_once()

    async def test_valid_reaction_removes_from_map(self):
        payload = self._make_payload()
        await self.cog.on_raw_reaction_add(payload)
        assert 42 not in self.cog.todo_message_map

    async def test_bot_own_reaction_ignored(self):
        payload = self._make_payload(user_id=9999)  # matches bot.user.id
        await self.cog.on_raw_reaction_add(payload)
        assert self.cog.todo_service.list_todos(1)[0].completed is False

    async def test_wrong_emoji_ignored(self):
        payload = self._make_payload(emoji="❌")
        await self.cog.on_raw_reaction_add(payload)
        assert self.cog.todo_service.list_todos(1)[0].completed is False

    async def test_unknown_message_ignored(self):
        payload = self._make_payload(message_id=9999)
        await self.cog.on_raw_reaction_add(payload)
        self.cog._send_todo_completed_update_by_user_id.assert_not_awaited()

    async def test_non_owner_reaction_ignored(self):
        payload = self._make_payload(user_id=2)  # user 2, but message belongs to user 1
        await self.cog.on_raw_reaction_add(payload)
        assert self.cog.todo_service.list_todos(1)[0].completed is False

    async def test_wrong_channel_ignored(self):
        payload = self._make_payload(channel_id=999999999999999999)
        await self.cog.on_raw_reaction_add(payload)
        assert self.cog.todo_service.list_todos(1)[0].completed is False

o