import re
from datetime import datetime, timezone
from typing import Dict, Tuple

import discord
from discord import app_commands
from discord.abc import Messageable
from discord.ext import commands

from config import todo_completed_channel_id, todo_list_channel_id
from services.todo_service import (
    TodoItem,
    TodoNotFoundError,
    TodoService,
    TodoServiceError,
    TodoValidationError,
)

WHITE_CHECK_MARK = "\N{WHITE HEAVY CHECK MARK}"
TODO_LIST_COLOR = 0x3B82F6
TODO_COMPLETED_COLOR = 0x10B981


class TodoCog(commands.GroupCog, group_name="todo", group_description="Manage your todo list"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.todo_service = TodoService()
        self.todo_message_map: Dict[int, Tuple[int, int]] = {}
        self.todo_list_message_by_key: Dict[Tuple[int, int], int] = {}
        self.todo_completed_message_by_key: Dict[Tuple[int, int], int] = {}
        self._state_restored = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._state_restored:
            return
        try:
            await self._restore_state_from_channels()
            self._state_restored = True
        except discord.DiscordException:
            self._state_restored = False

    @app_commands.command(name="add", description="Add a new todo item")
    @app_commands.describe(task="The task you want to add")
    async def add_todo(self, interaction: discord.Interaction, task: str) -> None:
        try:
            todo = self.todo_service.add_todo(interaction.user.id, task)
            await interaction.response.send_message(f"Added todo #{todo.id}: {todo.task}")
            await self._send_todo_list_update(interaction, todo)
        except TodoValidationError as error:
            await interaction.response.send_message(f"I couldn't add that todo: {error}", ephemeral=True)
        except TodoServiceError:
            await interaction.response.send_message(
                "Something went wrong while adding your todo. Please try again.",
                ephemeral=True,
            )

    @app_commands.command(name="list", description="List your todos")
    async def list_todos(self, interaction: discord.Interaction) -> None:
        try:
            todos = self.todo_service.list_todos(interaction.user.id)
            if not todos:
                await interaction.response.send_message("You have no todos yet. Use `/todo add` to create one.")
                return

            lines = []
            for todo in todos:
                status = "[x]" if todo.completed else "[ ]"
                lines.append(f"{status} #{todo.id} {todo.task}")

            await interaction.response.send_message("Your todos:\n" + "\n".join(lines))
        except TodoServiceError:
            await interaction.response.send_message(
                "Something went wrong while reading your todos. Please try again.",
                ephemeral=True,
            )

    @app_commands.command(name="complete", description="Mark a todo as complete")
    @app_commands.describe(todo_id="The todo ID to mark as complete")
    async def complete_todo(self, interaction: discord.Interaction, todo_id: int) -> None:
        try:
            todo = self.todo_service.complete_todo(interaction.user.id, todo_id)
            await interaction.response.send_message(f"Completed todo #{todo.id}: {todo.task}")
            await self._send_todo_completed_update(interaction, todo)
            await self._delete_todo_list_message(interaction.user.id, todo.id)
            self._remove_todo_message_mapping(interaction.user.id, todo.id)
        except TodoNotFoundError:
            await interaction.response.send_message(
                f"I couldn't find todo #{todo_id}. Use `/todo list` to check IDs.",
                ephemeral=True,
            )
        except TodoServiceError:
            await interaction.response.send_message(
                "Something went wrong while updating your todo. Please try again.",
                ephemeral=True,
            )

    @app_commands.command(name="delete", description="Delete a todo")
    @app_commands.describe(todo_id="The todo ID to delete")
    async def delete_todo(self, interaction: discord.Interaction, todo_id: int) -> None:
        try:
            todo = self.todo_service.delete_todo(interaction.user.id, todo_id)
            await interaction.response.send_message(f"Deleted todo #{todo.id}: {todo.task}")
            await self._delete_todo_list_message(interaction.user.id, todo.id)
            await self._delete_todo_completed_message(interaction.user.id, todo.id)
            self._remove_todo_message_mapping(interaction.user.id, todo.id)
        except TodoNotFoundError:
            await interaction.response.send_message(
                f"I couldn't find todo #{todo_id}. Use `/todo list` to check IDs.",
                ephemeral=True,
            )
        except TodoServiceError:
            await interaction.response.send_message(
                "Something went wrong while deleting your todo. Please try again.",
                ephemeral=True,
            )

    async def _send_todo_list_update(self, interaction: discord.Interaction, todo: TodoItem) -> None:
        channel = await self._resolve_channel(todo_list_channel_id)
        if channel is None:
            return

        try:
            embed = self._build_todo_list_embed(interaction.user, todo)
            focus_text = self._build_focus_heading(todo.task, "📝")
            message = await channel.send(content=focus_text, embed=embed)
            await message.add_reaction(WHITE_CHECK_MARK)
            self.todo_message_map[message.id] = (interaction.user.id, todo.id)
            self.todo_list_message_by_key[(interaction.user.id, todo.id)] = message.id
        except discord.DiscordException:
            return

    async def _send_todo_completed_update(self, interaction: discord.Interaction, todo: TodoItem) -> None:
        await self._send_todo_completed_update_by_user_id(interaction.user.id, todo)

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

    def _format_timestamp(self, value: datetime | None) -> str:
        if value is None:
            return "N/A"
        unix_ts = int(value.timestamp())
        return f"<t:{unix_ts}:F>"

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        bot_user_id = self.bot.user.id if self.bot.user else None
        if payload.user_id == bot_user_id:
            return
        if str(payload.emoji) != WHITE_CHECK_MARK:
            return

        mapping = self.todo_message_map.get(payload.message_id)
        if mapping is None:
            return

        owner_user_id, todo_id = mapping
        if payload.user_id != owner_user_id:
            return

        list_channel_id = self._to_int(todo_list_channel_id)
        if list_channel_id is not None and payload.channel_id != list_channel_id:
            return

        try:
            todo = self.todo_service.complete_todo(owner_user_id, todo_id)
            await self._send_todo_completed_update_by_user_id(owner_user_id, todo)
            await self._delete_todo_list_message(owner_user_id, todo.id, payload.message_id)
            self.todo_message_map.pop(payload.message_id, None)
        except (TodoServiceError, discord.DiscordException):
            return

    async def _send_todo_completed_update_by_user_id(self, user_id: int, todo: TodoItem) -> None:
        key = (user_id, todo.id)
        if key in self.todo_completed_message_by_key:
            return

        channel = await self._resolve_channel(todo_completed_channel_id)
        if channel is None:
            return

        completed_text = self._format_timestamp(todo.completed_at) if todo.completed_at else "N/A"
        try:
            embed = self._build_todo_completed_embed(user_id, todo, completed_text)
            focus_text = self._build_focus_heading(todo.task, "✅")
            message = await channel.send(content=f"<@{user_id}>\n{focus_text}", embed=embed)
            self.todo_completed_message_by_key[key] = message.id
        except discord.DiscordException:
            return

    def _build_todo_list_embed(self, user: discord.User | discord.Member, todo: TodoItem) -> discord.Embed:
        embed = discord.Embed(
            title=f"Task #{todo.id}",
            color=TODO_LIST_COLOR,
        )
        embed.set_author(name=f"{user.display_name} added a new todo", icon_url=user.display_avatar.url)
        embed.add_field(name="Owner", value=f"<@{user.id}>", inline=True)
        embed.add_field(name="Status", value="Pending", inline=True)
        embed.add_field(name="Created", value=self._format_timestamp(todo.created_at), inline=False)
        embed.add_field(name="Task", value=self._format_task_body(todo.task), inline=False)
        embed.set_footer(text="React with ✅ to complete this task")
        return embed

    def _build_todo_completed_embed(self, user_id: int, todo: TodoItem, completed_text: str) -> discord.Embed:
        embed = discord.Embed(
            title=f"Completed Task #{todo.id}",
            color=TODO_COMPLETED_COLOR,
        )
        embed.add_field(name="Owner", value=f"<@{user_id}>", inline=True)
        embed.add_field(name="Created", value=self._format_timestamp(todo.created_at), inline=True)
        embed.add_field(name="Completed", value=completed_text, inline=True)
        embed.add_field(name="Task", value=self._format_task_body(todo.task), inline=False)
        embed.set_footer(text="Great progress")
        return embed

    def _build_focus_heading(self, task: str, icon: str) -> str:
        single_line = " ".join(task.splitlines()).strip()
        if len(single_line) > 120:
            single_line = single_line[:117] + "..."
        return f"## {icon} {single_line}"

    def _format_task_body(self, task: str) -> str:
        cleaned = task.replace("```", "`\u200b``")
        lines = cleaned.splitlines() or [cleaned]
        return "\n".join(f"> {line}" if line else ">" for line in lines)

    def _to_int(self, value: str | None) -> int | None:
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def _remove_todo_message_mapping(self, user_id: int, todo_id: int) -> None:
        for message_id, mapping in list(self.todo_message_map.items()):
            owner_user_id, mapped_todo_id = mapping
            if owner_user_id == user_id and mapped_todo_id == todo_id:
                self.todo_message_map.pop(message_id, None)
        self.todo_list_message_by_key.pop((user_id, todo_id), None)

    async def _delete_todo_list_message(
        self, user_id: int, todo_id: int, message_id_override: int | None = None
    ) -> None:
        key = (user_id, todo_id)
        message_id = message_id_override or self.todo_list_message_by_key.get(key)
        if message_id is None:
            return

        await self._delete_message(todo_list_channel_id, message_id)
        self.todo_message_map.pop(message_id, None)
        self.todo_list_message_by_key.pop(key, None)

    async def _delete_todo_completed_message(self, user_id: int, todo_id: int) -> None:
        key = (user_id, todo_id)
        message_id = self.todo_completed_message_by_key.get(key)
        if message_id is None:
            return

        await self._delete_message(todo_completed_channel_id, message_id)
        self.todo_completed_message_by_key.pop(key, None)

    async def _delete_message(self, channel_id: str | None, message_id: int) -> None:
        channel = await self._resolve_channel(channel_id)
        if channel is None or not hasattr(channel, "fetch_message"):
            return
        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
        except discord.DiscordException:
            return

    async def _restore_state_from_channels(self) -> None:
        self.todo_service.reset()
        self.todo_message_map.clear()
        self.todo_list_message_by_key.clear()
        self.todo_completed_message_by_key.clear()

        max_id_by_user: Dict[int, int] = {}

        list_channel = await self._resolve_channel(todo_list_channel_id)
        if list_channel is not None and hasattr(list_channel, "history"):
            async for message in list_channel.history(limit=None, oldest_first=True):
                parsed = self._parse_list_message(message)
                if parsed is None:
                    continue

                user_id, todo_id, task, created_at = parsed
                todo = TodoItem(id=todo_id, task=task, completed=False, created_at=created_at)
                self.todo_service.load_todo(user_id, todo)

                self.todo_message_map[message.id] = (user_id, todo_id)
                self.todo_list_message_by_key[(user_id, todo_id)] = message.id
                max_id_by_user[user_id] = max(max_id_by_user.get(user_id, 0), todo_id)

        completed_channel = await self._resolve_channel(todo_completed_channel_id)
        if completed_channel is not None and hasattr(completed_channel, "history"):
            async for message in completed_channel.history(limit=None, oldest_first=True):
                parsed = self._parse_completed_message(message)
                if parsed is None:
                    continue

                user_id, todo_id = parsed
                self.todo_completed_message_by_key[(user_id, todo_id)] = message.id
                max_id_by_user[user_id] = max(max_id_by_user.get(user_id, 0), todo_id)

        for user_id, max_id in max_id_by_user.items():
            self.todo_service.ensure_next_id(user_id, max_id + 1)

    def _parse_list_message(self, message: discord.Message) -> tuple[int, int, str, datetime] | None:
        if not message.embeds:
            return None
        embed = message.embeds[0]
        todo_id = self._extract_todo_id(embed.title)
        if todo_id is None:
            return None

        user_id = self._extract_owner_id(embed, message.content)
        if user_id is None:
            user_id = self._extract_owner_id_from_message(message)
        if user_id is None:
            return None

        task = self._extract_task(embed, message.content)
        if not task:
            return None

        created_at = self._extract_field_timestamp(embed, "Created") or message.created_at
        created_at = created_at.astimezone(timezone.utc)
        return user_id, todo_id, task, created_at

    def _parse_completed_message(self, message: discord.Message) -> tuple[int, int] | None:
        if not message.embeds:
            return None
        embed = message.embeds[0]
        todo_id = self._extract_todo_id(embed.title)
        if todo_id is None:
            return None

        user_id = self._extract_owner_id(embed, message.content)
        if user_id is None:
            user_id = self._extract_owner_id_from_message(message)
        if user_id is None:
            return None
        return user_id, todo_id

    def _extract_todo_id(self, title: str | None) -> int | None:
        if not title:
            return None
        match = re.search(r"(?:Completed\s+)?Task\s+#(\d+)", title)
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

        # Fallback for older bot messages where owner field wasn't present.
        return None

    def _extract_owner_id_from_message(self, message: discord.Message) -> int | None:
        interaction = getattr(message, "interaction", None)
        if interaction and getattr(interaction, "user", None):
            return interaction.user.id

        return None

    def _extract_task(self, embed: discord.Embed, content: str | None) -> str | None:
        task_field = self._embed_field_value(embed, "Task")
        if task_field:
            lines = [line.lstrip("> ") for line in task_field.splitlines()]
            task = "\n".join(lines).strip()
            if task:
                return task

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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TodoCog(bot))
