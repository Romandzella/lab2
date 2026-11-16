"""Типы и временные метки событий"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from streamstats.errors import TimestampError


class Level(StrEnum):
    """Уровни событий"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class Event:
    """Событие с временной меткой в UTC"""

    timestamp: datetime
    level: Level
    source: str
    message: str


def parse_timestamp(value: object) -> datetime:
    """Переводит время из ISO 8601 в UTC"""
    if not isinstance(value, str):
        raise TimestampError("timestamp must be an ISO 8601 string")
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC)
    except (ValueError, OverflowError):
        raise TimestampError(f"invalid ISO 8601 timestamp: {value!r}") from None
