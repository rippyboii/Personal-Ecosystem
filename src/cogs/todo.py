from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands
from discord.abc import Messageable

from config import todo_completed_channel_id, todo_list_channel_id
from services.todo_service import (
    TodoNotFoundError,
    TodoItem,
    TodoService,
    TodoServiceError,
    TodoValidationError,
)

class TodoCog(commands.GroupCog, group_name="todo", group_description="Manage your todo list"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.todo_service = TodoService()

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
            await channel.send(
                f"New todo from {interaction.user.mention}: #{todo.id} {todo.task}\n"
                f"Task created on: {self._format_timestamp(todo.created_at)}"
            )
        except discord.DiscordException:
            return

    async def _send_todo_completed_update(self, interaction: discord.Interaction, todo: TodoItem) -> None:
        channel = await self._resolve_channel(todo_completed_channel_id)
        if channel is None:
            return

        completed_text = self._format_timestamp(todo.completed_at) if todo.completed_at else "N/A"
        try:
            await channel.send(
                f"Completed todo from {interaction.user.mention}: #{todo.id} {todo.task}\n"
                f"Task created on: {self._format_timestamp(todo.created_at)}\n"
                f"Task completed on: {completed_text}"
            )
        except discord.DiscordException:
            return

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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TodoCog(bot))
