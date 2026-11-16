"""Потоковая фильтрация и подсчёт статистики"""

import logging
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime

from streamstats.errors import InvalidEventError
from streamstats.models import Event, Level

logger = logging.getLogger(__name__)


@dataclass
class Stats:
    """Итоги по всем обработанным файлам"""

    total: int = 0
    skipped: int = 0
    by_level: dict[str, int] = field(
        default_factory=lambda: {level.value: 0 for level in Level},
    )
    by_source: dict[str, int] = field(default_factory=dict)
    top_error_sources: list[tuple[str, int]] = field(default_factory=list)
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None


def filter_events(
    events: Iterable[Event],
    *,
    levels: Collection[Level] = (),
    sources: Collection[str] = (),
    since: datetime | None = None,
    until: datetime | None = None,
) -> Iterator[Event]:
    """Отбирает события по уровню, источнику и времени [since, until)"""
    for event in events:
        if levels and event.level not in levels:
            continue
        if sources and event.source not in sources:
            continue
        if since is not None and event.timestamp < since:
            continue
        if until is not None and event.timestamp >= until:
            continue
        yield event


def analyze(
    records: Iterable[Event | InvalidEventError],
    *,
    skip_invalid: bool = False,
    levels: Collection[Level] = (),
    sources: Collection[str] = (),
    since: datetime | None = None,
    until: datetime | None = None,
    top_n: int = 5,
) -> Stats:
    """Считает статистику за один проход по записям"""
    stats = Stats()
    errors_by_source: dict[str, int] = {}

    def valid_events() -> Iterator[Event]:
        """Обрабатывает ошибки перед фильтрацией событий"""
        for record in records:
            if isinstance(record, InvalidEventError):
                if not skip_invalid:
                    raise record
                stats.skipped += 1
                logger.warning(f"skipped invalid line: {record}")
                continue
            yield record

    for event in filter_events(
        valid_events(), levels=levels, sources=sources, since=since, until=until
    ):
        stats.total += 1
        stats.by_level[event.level.value] += 1
        stats.by_source[event.source] = stats.by_source.get(event.source, 0) + 1
        if event.level in (Level.ERROR, Level.CRITICAL):
            errors_by_source[event.source] = errors_by_source.get(event.source, 0) + 1
        if stats.first_timestamp is None or event.timestamp < stats.first_timestamp:
            stats.first_timestamp = event.timestamp
        if stats.last_timestamp is None or event.timestamp > stats.last_timestamp:
            stats.last_timestamp = event.timestamp

    sorted_sources = sorted(
        errors_by_source.items(), key=lambda item: (-item[1], item[0])
    )
    stats.top_error_sources = sorted_sources[: max(0, top_n)]
    return stats
