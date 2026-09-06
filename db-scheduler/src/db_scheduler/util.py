"""The two-line helpers db-scheduler keeps in `StringUtils`, `StreamUtils` and `ExceptionUtils`."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

__all__ = ["chunked", "describe", "truncate"]


def truncate(value: str | None, length: int) -> str | None:
    """Cut a string to fit a column. `picked_by` is 50 characters, and a hostname can be longer."""
    if value is None:
        return None
    return value[:length]


def describe(cause: BaseException | None) -> str | None:
    """`TypeError: 'the message'` - a log line's worth of an exception, without the traceback.

    Used where the stack trace is either logged separately or deliberately suppressed, and the
    point is only to name what went wrong.
    """
    if cause is None:
        return None
    message = str(cause)
    return f"{type(cause).__name__}: '{message}'" if message else type(cause).__name__


def chunked[T](values: Iterable[T], chunk_size: int) -> Iterator[list[T]]:
    """Whole chunks, then whatever is left. Never an empty one."""
    if chunk_size <= 0:
        raise ValueError("Chunk size must be greater than 0")
    chunk: list[T] = []
    for value in values:
        chunk.append(value)
        if len(chunk) == chunk_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
