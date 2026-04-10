# Personal Ecosystem

A personal Discord bot for managing your todo list.

## Features

- **Todo management** — add, list, complete, and delete tasks directly from Discord
- **Optional task descriptions** — attach extra details to any task
- **React to complete** — react with ✅ on a task card to mark it done without a command
- **Persistent state** — bot restores its todo state from channel history on restart
- **Error reporting** — unhandled errors are posted to a designated log channel

## Commands

| Command | Description |
|---|---|
| `/todo add <task> [description]` | Add a new task, with an optional description |
| `/todo list` | List all your pending tasks |
| `/todo complete <id>` | Mark a task as complete |
| `/todo delete <id>` | Delete a task |

## Setup

**1. Clone and install dependencies**

```bash
uv sync
```

**2. Create a `.env` file**

```env
TOKEN=your_discord_bot_token

todo_list_channel_id=...
todo_completed_channel_id=...
bot_log_channel_id=...
error_log_channel_id=...
```

**3. Run the bot**

```bash
uv run src/main.py
```

## Running Tests

```bash
uv run pytest
```

## Project Structure

```
src/
  main.py            # Bot entry point and error reporting
  config.py          # Environment variable loading
  cogs/
    todo.py          # Todo slash commands and Discord embed logic
  services/
    todo_service.py  # In-memory todo storage and business logic
tests/
  services/
    test_todo_service.py
  cogs/
    test_todo_cog.py
```

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)
