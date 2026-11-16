"""Тесты подсчёта статистики, фильтров и ошибок"""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import chain

import pytest

from streamstats.analysis import Stats, analyze, filter_events
from streamstats.errors import InvalidEventError
from streamstats.models import Event, Level


def make_event(source: str, level: Level = Level.INFO, hour: int = 10) -> Event:
    """Создаёт событие для проверки статистики"""
    return Event(datetime(2025, 2, 3, hour, tzinfo=UTC), level, source, "сообщение")


def test_empty_stream() -> None:
    assert analyze(iter(())) == Stats()


def test_aggregate_two_streams() -> None:
    later = make_event("склад", Level.ERROR, hour=12)
    earlier = make_event("склад", Level.CRITICAL, hour=8)
    stats = analyze(chain([later, make_event("касса")], [earlier]))

    assert stats.total == 3
    assert stats.skipped == 0
    assert stats.by_level == {
        "DEBUG": 0,
        "INFO": 1,
        "WARNING": 0,
        "ERROR": 1,
        "CRITICAL": 1,
    }
    assert stats.by_source == {"склад": 2, "касса": 1}
    assert stats.top_error_sources == [("склад", 2)]
    assert stats.first_timestamp == earlier.timestamp
    assert stats.last_timestamp == later.timestamp


@pytest.mark.parametrize(
    ("limit", "names"), [(5, "zabcd"), (2, "za"), (0, ""), (-1, "")]
)
def test_top_is_limited_and_deterministic(limit: int, names: str) -> None:
    events = [make_event(source, Level.ERROR) for source in "fedcba"]
    events.extend([make_event("z", Level.ERROR), make_event("z", Level.CRITICAL)])
    events.extend([make_event("a", Level.WARNING)] * 5)
    expected = [(name, 2 if name == "z" else 1) for name in names]

    assert analyze(events, top_n=limit).top_error_sources == expected
    assert analyze(reversed(events), top_n=limit).top_error_sources == expected


def test_strict_mode_stops_at_first_error() -> None:
    error = InvalidEventError("events.jsonl:7: invalid JSON")

    def records() -> Iterator[Event | InvalidEventError]:
        yield make_event("a")
        yield error
        pytest.fail("Строгий режим прочитал запись после ошибки")

    with pytest.raises(InvalidEventError) as caught:
        analyze(records(), sources={"b"})
    assert caught.value is error


def test_skip_counts_errors_before_filter(caplog: pytest.LogCaptureFixture) -> None:
    error = InvalidEventError("events.jsonl:7: invalid JSON")
    records = [make_event("a"), error, make_event("b")]

    stats = analyze(records, skip_invalid=True, sources={"b"})

    assert stats.total == 1
    assert stats.by_source == {"b": 1}
    assert stats.skipped == 1
    assert caplog.messages == ["skipped invalid line: events.jsonl:7: invalid JSON"]
    assert caplog.records[0].levelno == logging.WARNING


def test_filter_combines_conditions_with_half_open_interval() -> None:
    match = make_event("a", Level.ERROR, hour=10)
    events = [
        make_event("a", Level.ERROR, hour=9),
        match,
        make_event("a", Level.ERROR, hour=11),
        make_event("a", Level.INFO, hour=10),
        make_event("b", Level.ERROR, hour=10),
    ]
    stats = analyze(
        events,
        levels={Level.ERROR},
        sources={"a"},
        since=match.timestamp,
        until=events[2].timestamp,
    )

    assert stats.total == 1
    assert stats.first_timestamp == stats.last_timestamp == match.timestamp
    assert stats.by_source == {"a": 1}


def test_filter_yields_before_reading_rest() -> None:
    match = make_event("wanted")

    def events() -> Iterator[Event]:
        yield make_event("ignored")
        yield match
        pytest.fail("Фильтр прочитал лишнее событие")

    assert next(filter_events(events(), sources={"wanted"})) == match
