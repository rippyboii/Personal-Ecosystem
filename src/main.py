import asyncio
import traceback
import warnings
from datetime import datetime, timezone

import discord
from discord.app_commands import AppCommandError
from discord.ext import commands

from config import TOKEN, bot_log_channel_id, error_log_channel_id


intents = discord.Intents.default()
pes = commands.Bot(command_prefix="!", intents=intents)


class DiscordErrorReporter:
    def __init__(self, bot: commands.Bot, channel_id: str | None) -> None:
        self.bot = bot
        self.channel_id = self._parse_channel_id(channel_id)

    async def report_exception(self, source: str, error: BaseException) -> None:
        trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        await self.report_text(f"Exception: {source}", trace)

    async def report_text(self, title: str, text: str) -> None:
        if self.channel_id is None:
            return

        channel = await self._resolve_channel()
        if channel is None:
            return

        chunks = self._chunk_text(text, 1800)
        for index, chunk in enumerate(chunks):
            embed_title = title if index == 0 else f"{title} (cont. {index + 1})"
            embed = discord.Embed(
                title=embed_title,
                description=f"```text\n{chunk}\n```",
                color=0xEF4444,
                timestamp=datetime.now(timezone.utc),
            )
            try:
                await channel.send(embed=embed)
            except discord.DiscordException:
                return

    def report_exception_threadsafe(self, source: str, error: BaseException) -> None:
        if not self.bot.loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self.report_exception(source, error), self.bot.loop)

    def report_text_threadsafe(self, title: str, text: str) -> None:
        if not self.bot.loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self.report_text(title, text), self.bot.loop)

    async def _resolve_channel(self):
        if self.channel_id is None:
            return None

        cached_channel = self.bot.get_channel(self.channel_id)
        if cached_channel is not None:
            return cached_channel

        try:
            return await self.bot.fetch_channel(self.channel_id)
        except discord.DiscordException:
            return None

    @staticmethod
    def _parse_channel_id(channel_id: str | None) -> int | None:
        if not channel_id:
            return None
        try:
            return int(channel_id)
        except ValueError:
            return None

    @staticmethod
    def _chunk_text(text: str, max_size: int) -> list[str]:
        if not text:
            return ["(empty)"]

        chunks = []
        remaining = text
        while remaining:
            chunks.append(remaining[:max_size])
            remaining = remaining[max_size:]
        return chunks


def _install_warning_hook(bot: commands.Bot) -> None:
    original_showwarning = warnings.showwarning

    def warning_hook(message, category, filename, lineno, file=None, line=None):  # noqa: ANN001
        formatted = warnings.formatwarning(message, category, filename, lineno, line)
        original_showwarning(message, category, filename, lineno, file=file, line=line)

        reporter = getattr(bot, "error_reporter", None)
        if reporter:
            reporter.report_text_threadsafe("Python Warning", formatted)

    warnings.showwarning = warning_hook


@pes.event
async def setup_hook() -> None:
    pes.error_reporter = DiscordErrorReporter(pes, error_log_channel_id)
    warnings.simplefilter("default", DeprecationWarning)
    _install_warning_hook(pes)

    loop = asyncio.get_running_loop()

    def loop_exception_handler(loop_ref, context):  # noqa: ANN001
        reporter = getattr(pes, "error_reporter", None)
        if reporter is None:
            loop_ref.default_exception_handler(context)
            return

        exception = context.get("exception")
        message = context.get("message", "Unhandled asyncio error")
        if exception:
            reporter.report_exception_threadsafe(f"asyncio: {message}", exception)
        else:
            reporter.report_text_threadsafe("Asyncio Error", str(context))
        loop_ref.default_exception_handler(context)

    loop.set_exception_handler(loop_exception_handler)

    await pes.load_extension("cogs.todo")
    await pes.tree.sync()


@pes.event
async def on_ready() -> None:
    print(f"Logged in as {pes.user}")

    if bot_log_channel_id:
        channel = pes.get_channel(int(bot_log_channel_id))
        if channel:
            await channel.send("Hi <@722011173154717777>, I'm online!")
        if not channel:
            raise ValueError(f"Channel with ID {bot_log_channel_id} not found.")
    print("Bot is ONLINE! BINGO!!")


@pes.event
async def on_error(event_method: str, *args, **kwargs) -> None:  # noqa: ARG001
    reporter = getattr(pes, "error_reporter", None)
    if reporter:
        text = traceback.format_exc()
        await reporter.report_text(f"Unhandled Event Error: {event_method}", text)


@pes.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: AppCommandError) -> None:
    reporter = getattr(pes, "error_reporter", None)
    if reporter:
        await reporter.report_exception("App Command", error)

    if interaction.response.is_done():
        await interaction.followup.send("Something went wrong. The issue was reported.", ephemeral=True)
    else:
        await interaction.response.send_message("Something went wrong. The issue was reported.", ephemeral=True)


def main() -> None:
    pes.run(TOKEN)


if __name__ == "__main__":
    main()
