import os
from dotenv import load_dotenv

load_dotenv()


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    return normalized in {"1", "true", "yes", "on"}

TOKEN = os.getenv("TOKEN")
bot_log_channel_id = os.getenv("bot_log_channel_id")
todo_list_channel_id = os.getenv("todo_list_channel_id")
todo_completed_channel_id = os.getenv("todo_completed_channel_id")
reminder_list_channel_id = os.getenv("reminder_list_channel_id") or os.getenv("reminderlist_channel_id")
reminder_channel_id = os.getenv("reminder_channel_id")
error_log_channel_id = os.getenv("error_log_channel_id")
message_content_intent_enabled = _parse_bool(os.getenv("message_content_intent_enabled"), default=False)
streak_channel_id       = os.getenv("streak_channel_id")
streak_list_channel_id  = os.getenv("streak_list_channel_id")
sqlite_db_path          = os.getenv("sqlite_db_path", "data/streaks.db")
dev_guild_id            = os.getenv("dev_guild_id")

if not TOKEN:
    raise ValueError("TOKEN is not found. Check your .env file.")
