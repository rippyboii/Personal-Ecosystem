import discord
from discord.ext import commands
from config import TOKEN, bot_log_channel_id

intents = discord.Intents.default()
pes = commands.Bot(command_prefix='!', intents=intents)

@pes.event
async def on_ready():
    print(f"Logged in as {pes.user}")

    if bot_log_channel_id:
        channel = pes.get_channel(int(bot_log_channel_id))
        if channel:
            await channel.send("Hi <@722011173154717777>, I'm online!")
        if not channel:
            raise ValueError (f"Channel with ID {bot_log_channel_id} not found.")
    print("Bot is ONLINE! BINGO!!")

def main():
    pes.run(TOKEN)

if __name__ == "__main__":
    main()
    