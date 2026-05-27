from __future__ import annotations

from app.utils.tracing import traceable


class TestTraceable:
    def test_returns_original_function(self) -> None:
        @traceable()
        def my_func() -> str:
            return "hello"

        assert my_func() == "hello"

    def test_accepts_name_argument(self) -> None:
        @traceable("my-trace-name")
        def my_func() -> int:
            return 42

        assert my_func() == 42

    def test_accepts_keyword_arguments(self) -> None:
        @traceable(name="trace", tags=["a", "b"])
        def my_func() -> bool:
            return True

        assert my_func() is True

    def test_preserves_function_identity(self) -> None:
        def my_func() -> None:
            pass

        decorated = traceable()(my_func)
        assert decorated is my_func

    def test_works_without_arguments(self) -> None:
        @traceable()
        def my_func(x: int, y: int) -> int:
            return x + y

        assert my_func(2, 3) == 5
