import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
bot_log_channel_id = os.getenv("bot_log_channel_id")
todo_list_channel_id = os.getenv("todo_list_channel_id")
todo_completed_channel_id = os.getenv("todo_completed_channel_id")
reminder_list_channel_id = os.getenv("reminder_list_channel_id") or os.getenv("reminderlist_channel_id")
reminder_channel_id = os.getenv("reminder_channel_id")
error_log_channel_id = os.getenv("error_log_channel_id")

if not TOKEN:
    raise ValueError("TOKEN is not found. Check your .env file.")
