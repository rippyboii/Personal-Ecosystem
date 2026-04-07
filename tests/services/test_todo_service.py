from datetime import datetime, timezone

import pytest

from services.todo_service import (
    TodoItem,
    TodoNotFoundError,
    TodoService,
    TodoValidationError,
)


class TestAddTodo:
    def setup_method(self):
        self.service = TodoService()

    def test_returns_item_with_correct_task(self):
        todo = self.service.add_todo(1, "Buy milk")
        assert todo.task == "Buy milk"

    def test_id_starts_at_one(self):
        todo = self.service.add_todo(1, "Task")
        assert todo.id == 1

    def test_completed_defaults_to_false(self):
        todo = self.service.add_todo(1, "Task")
        assert todo.completed is False

    def test_description_defaults_to_none(self):
        todo = self.service.add_todo(1, "Task")
        assert todo.description is None

    def test_with_description(self):
        todo = self.service.add_todo(1, "Task", "Some details")
        assert todo.description == "Some details"

    def test_strips_task_whitespace(self):
        todo = self.service.add_todo(1, "  Buy milk  ")
        assert todo.task == "Buy milk"

    def test_strips_description_whitespace(self):
        todo = self.service.add_todo(1, "Task", "  details  ")
        assert todo.description == "details"

    def test_blank_description_returns_none(self):
        todo = self.service.add_todo(1, "Task", "   ")
        assert todo.description is None

    def test_none_description_returns_none(self):
        todo = self.service.add_todo(1, "Task", None)
        assert todo.description is None

    def test_ids_increment_per_user(self):
        t1 = self.service.add_todo(1, "First")
        t2 = self.service.add_todo(1, "Second")
        assert t1.id == 1
        assert t2.id == 2

    def test_ids_are_independent_across_users(self):
        t1 = self.service.add_todo(1, "User 1 task")
        t2 = self.service.add_todo(2, "User 2 task")
        assert t1.id == 1
        assert t2.id == 1

    def test_created_at_is_set_to_now(self):
        before = datetime.now(timezone.utc)
        todo = self.service.add_todo(1, "Task")
        after = datetime.now(timezone.utc)
        assert before <= todo.created_at <= after

    def test_empty_task_raises(self):
        with pytest.raises(TodoValidationError):
            self.service.add_todo(1, "")

    def test_whitespace_only_task_raises(self):
        with pytest.raises(TodoValidationError):
            self.service.add_todo(1, "   ")

    def test_task_over_200_chars_raises(self):
        with pytest.raises(TodoValidationError):
            self.service.add_todo(1, "x" * 201)

    def test_task_at_200_chars_succeeds(self):
        todo = self.service.add_todo(1, "x" * 200)
        assert len(todo.task) == 200

    def test_description_over_500_chars_raises(self):
        with pytest.raises(TodoValidationError):
            self.service.add_todo(1, "Task", "x" * 501)

    def test_description_at_500_chars_succeeds(self):
        todo = self.service.add_todo(1, "Task", "x" * 500)
        assert len(todo.description) == 500


class TestListTodos:
    def setup_method(self):
        self.service = TodoService()

    def test_empty_for_new_user(self):
        assert self.service.list_todos(1) == []

    def test_returns_all_added_todos(self):
        self.service.add_todo(1, "Task 1")
        self.service.add_todo(1, "Task 2")
        assert len(self.service.list_todos(1)) == 2

    def test_todos_are_isolated_per_user(self):
        self.service.add_todo(1, "User 1 task")
        self.service.add_todo(2, "User 2 task")
        assert len(self.service.list_todos(1)) == 1
        assert len(self.service.list_todos(2)) == 1

    def test_returns_a_copy(self):
        self.service.add_todo(1, "Task")
        todos = self.service.list_todos(1)
        todos.clear()
        assert len(self.service.list_todos(1)) == 1

    def test_order_matches_insertion(self):
        self.service.add_todo(1, "First")
        self.service.add_todo(1, "Second")
        todos = self.service.list_todos(1)
        assert todos[0].task == "First"
        assert todos[1].task == "Second"


class TestCompleteTodo:
    def setup_method(self):
        self.service = TodoService()

    def test_marks_todo_as_completed(self):
        todo = self.service.add_todo(1, "Task")
        self.service.complete_todo(1, todo.id)
        assert todo.completed is True

    def test_sets_completed_at(self):
        todo = self.service.add_todo(1, "Task")
        before = datetime.now(timezone.utc)
        self.service.complete_todo(1, todo.id)
        after = datetime.now(timezone.utc)
        assert before <= todo.completed_at <= after

    def test_already_completed_does_not_change_completed_at(self):
        todo = self.service.add_todo(1, "Task")
        self.service.complete_todo(1, todo.id)
        first_ts = todo.completed_at
        self.service.complete_todo(1, todo.id)
        assert todo.completed_at == first_ts

    def test_not_found_raises(self):
        with pytest.raises(TodoNotFoundError):
            self.service.complete_todo(1, 999)

    def test_wrong_user_raises(self):
        todo = self.service.add_todo(1, "Task")
        with pytest.raises(TodoNotFoundError):
            self.service.complete_todo(2, todo.id)

    def test_returns_the_todo(self):
        todo = self.service.add_todo(1, "Task")
        result = self.service.complete_todo(1, todo.id)
        assert result is todo


class TestDeleteTodo:
    def setup_method(self):
        self.service = TodoService()

    def test_removes_the_todo(self):
        todo = self.service.add_todo(1, "Task")
        self.service.delete_todo(1, todo.id)
        assert self.service.list_todos(1) == []

    def test_returns_the_removed_todo(self):
        todo = self.service.add_todo(1, "Task")
        removed = self.service.delete_todo(1, todo.id)
        assert removed.task == "Task"

    def test_deleting_last_todo_resets_id_counter(self):
        todo = self.service.add_todo(1, "Task")
        self.service.delete_todo(1, todo.id)
        new_todo = self.service.add_todo(1, "New task")
        assert new_todo.id == 1

    def test_only_removes_target_todo(self):
        self.service.add_todo(1, "Task 1")
        t2 = self.service.add_todo(1, "Task 2")
        self.service.add_todo(1, "Task 3")
        self.service.delete_todo(1, t2.id)
        remaining = self.service.list_todos(1)
        assert len(remaining) == 2
        assert all(t.id != t2.id for t in remaining)

    def test_not_found_raises(self):
        with pytest.raises(TodoNotFoundError):
            self.service.delete_todo(1, 999)

    def test_wrong_user_raises(self):
        todo = self.service.add_todo(1, "Task")
        with pytest.raises(TodoNotFoundError):
            self.service.delete_todo(2, todo.id)


class TestLoadTodo:
    def setup_method(self):
        self.service = TodoService()

    def test_adds_new_todo(self):
        todo = TodoItem(id=5, task="Loaded task")
        self.service.load_todo(1, todo)
        todos = self.service.list_todos(1)
        assert len(todos) == 1
        assert todos[0].id == 5

    def test_updates_existing_todo_with_same_id(self):
        original = TodoItem(id=5, task="Original")
        self.service.load_todo(1, original)
        updated = TodoItem(id=5, task="Updated", completed=True)
        self.service.load_todo(1, updated)
        todos = self.service.list_todos(1)
        assert len(todos) == 1
        assert todos[0].task == "Updated"

    def test_advances_next_id(self):
        self.service.load_todo(1, TodoItem(id=10, task="High id"))
        new_todo = self.service.add_todo(1, "Next task")
        assert new_todo.id == 11

    def test_does_not_lower_next_id(self):
        self.service.load_todo(1, TodoItem(id=10, task="High id"))
        self.service.load_todo(1, TodoItem(id=3, task="Low id"))
        new_todo = self.service.add_todo(1, "Next task")
        assert new_todo.id == 11


class TestReset:
    def test_clears_todos_for_all_users(self):
        service = TodoService()
        service.add_todo(1, "Task A")
        service.add_todo(2, "Task B")
        service.reset()
        assert service.list_todos(1) == []
        assert service.list_todos(2) == []

    def test_resets_id_counters(self):
        service = TodoService()
        service.add_todo(1, "Task")
        service.add_todo(1, "Task")
        service.reset()
        new_todo = service.add_todo(1, "After reset")
        assert new_todo.id == 1
