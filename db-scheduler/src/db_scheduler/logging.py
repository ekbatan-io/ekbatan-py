"""`ConfigurableLogger` and `LogLevel` - how loudly a task failure is reported.

Not decoration. A task that is *expected* to fail sometimes - a webhook to a flaky endpoint,
retried with backoff - produces one ERROR per attempt by default, and an operator who sees a
hundred of those a day stops reading them. Turning that task's failure logging down to DEBUG, or
off, is what keeps the ERRORs that matter visible.
"""

from __future__ import annotations

import enum
import logging
from typing import Any

__all__ = ["ConfigurableLogger", "LogLevel"]


class LogLevel(enum.Enum):
    """Including OFF, which the standard library has no level for."""

    OFF = "OFF"
    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"

    def to_logging_level(self) -> int:
        """The `logging` constant. TRACE has no counterpart, so it maps to DEBUG."""
        return {
            LogLevel.TRACE: logging.DEBUG,
            LogLevel.DEBUG: logging.DEBUG,
            LogLevel.INFO: logging.INFO,
            LogLevel.WARN: logging.WARNING,
            LogLevel.ERROR: logging.ERROR,
            LogLevel.OFF: logging.NOTSET,
        }[self]


class ConfigurableLogger:
    """Logs at a level chosen when the scheduler was built, with or without a stack trace."""

    __slots__ = ("_level", "_log_stack_trace", "_logger")

    def __init__(
        self, logger: logging.Logger | None, level: LogLevel, log_stack_trace: bool
    ) -> None:
        self._logger = logger
        self._level = level
        self._log_stack_trace = log_stack_trace

    @staticmethod
    def create(
        logger: logging.Logger, log_level: LogLevel, log_stack_trace: bool
    ) -> ConfigurableLogger:
        if log_level is LogLevel.OFF:
            return _NO_OP_LOGGER
        return ConfigurableLogger(logger, log_level, log_stack_trace)

    def log(self, format: str, cause: BaseException | None, *arguments: Any) -> None:
        if self._logger is None:
            return
        self._logger.log(
            self._level.to_logging_level(),
            format,
            *arguments,
            exc_info=cause if self._log_stack_trace else None,
        )

    def __repr__(self) -> str:
        return f"ConfigurableLogger(level={self._level.name}, stack_trace={self._log_stack_trace})"


class _NoOpLogger(ConfigurableLogger):
    """What `LogLevel.OFF` produces. Says nothing, and is asked nothing."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(None, LogLevel.OFF, False)

    def log(self, format: str, cause: BaseException | None, *arguments: Any) -> None:
        """Nothing."""

    def __repr__(self) -> str:
        return "ConfigurableLogger(OFF)"


_NO_OP_LOGGER = _NoOpLogger()
