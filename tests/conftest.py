import os

# Must be set before config.py is imported (it raises ValueError if TOKEN is missing)
os.environ.setdefault("TOKEN", "test-token")
os.environ.setdefault("todo_list_channel_id", "111111111111111111")
os.environ.setdefault("todo_completed_channel_id", "222222222222222222")
os.environ.setdefault("bot_log_channel_id", "333333333333333333")
os.environ.setdefault("error_log_channel_id", "444444444444444444")
os.environ.setdefault("reminder_channel_id", "555555555555555555")
os.environ.setdefault("reminder_list_channel_id", "666666666666666666")
