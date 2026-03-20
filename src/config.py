import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
bot_log_channel_id = os.getenv("bot_log_channel_id")

if not TOKEN:
    raise ValueError("TOKEN is not found. Check your .env file.")