from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List


class TodoServiceError(Exception):
    """Base error for todo service failures."""


class TodoValidationError(TodoServiceError):
    """Raised when user input is invalid."""


class TodoNotFoundError(TodoServiceError):
    """Raised when a todo item does not exist for a user."""


@dataclass
class TodoItem:
    id: int
    task: str
    completed: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


class TodoService:
    def __init__(self) -> None:
        self._todos_by_user: Dict[int, List[TodoItem]] = {}
        self._next_id_by_user: Dict[int, int] = {}

    def add_todo(self, user_id: int, task: str) -> TodoItem:
        cleaned_task = self._validate_task(task)
        todo = TodoItem(
            id=self._next_id(user_id),
            task=cleaned_task,
            created_at=datetime.now(timezone.utc),
        )
        self._todos_by_user.setdefault(user_id, []).append(todo)
        return todo

    def list_todos(self, user_id: int) -> List[TodoItem]:
        return list(self._todos_by_user.get(user_id, []))

    def complete_todo(self, user_id: int, todo_id: int) -> TodoItem:
        todo = self._get_todo_by_id(user_id, todo_id)
        if not todo.completed:
            todo.completed = True
            todo.completed_at = datetime.now(timezone.utc)
        return todo

    def delete_todo(self, user_id: int, todo_id: int) -> TodoItem:
        todos = self._todos_by_user.get(user_id, [])
        for index, todo in enumerate(todos):
            if todo.id == todo_id:
                removed_todo = todos.pop(index)
                if not todos:
                    self._todos_by_user.pop(user_id, None)
                    self._next_id_by_user.pop(user_id, None)
                return removed_todo
        raise TodoNotFoundError(f"Todo with id {todo_id} not found.")

    def _validate_task(self, task: str) -> str:
        if task is None:
            raise TodoValidationError("Task is required.")

        cleaned = task.strip()
        if not cleaned:
            raise TodoValidationError("Task cannot be empty.")
        if len(cleaned) > 200:
            raise TodoValidationError("Task is too long (max 200 characters).")
        return cleaned

    def _next_id(self, user_id: int) -> int:
        next_id = self._next_id_by_user.get(user_id, 1)
        self._next_id_by_user[user_id] = next_id + 1
        return next_id

    def _get_todo_by_id(self, user_id: int, todo_id: int) -> TodoItem:
        for todo in self._todos_by_user.get(user_id, []):
            if todo.id == todo_id:
                return todo
        raise TodoNotFoundError(f"Todo with id {todo_id} not found.")
