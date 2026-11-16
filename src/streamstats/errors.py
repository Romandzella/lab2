"""Исключения анализатора событий"""

from pathlib import Path
from typing import Self


class StreamStatsError(Exception):
    """Базовая ошибка пакета"""


class UnsupportedFormatError(StreamStatsError):
    """Формат входных данных или отчёта не поддерживается"""


class CliConfigError(StreamStatsError):
    """Неверные параметры обработки"""


class InvalidEventError(StreamStatsError):
    """Ошибка записи с именем файла и номером строки"""

    def __init__(
        self, detail: str, path: Path | None = None, line: int | None = None
    ) -> None:
        super().__init__(detail)
        self.path = path
        self.line = line

    def at(self, path: Path, line: int) -> Self:
        """Добавляет имя файла и номер строки к ошибке"""
        self.path = path
        self.line = line
        return self

    def __str__(self) -> str:
        detail = super().__str__()
        if self.path is not None and self.line is not None:
            return f"{self.path}:{self.line}: {detail}"
        return detail


class TimestampError(InvalidEventError):
    """Неверное значение поля timestamp"""
