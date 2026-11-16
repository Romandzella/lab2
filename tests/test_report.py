"""Тесты отчётов JSON и CSV"""

import csv
import json
from datetime import UTC, datetime
from io import StringIO

import pytest

from streamstats.analysis import Stats, analyze
from streamstats.errors import UnsupportedFormatError
from streamstats.models import Event, Level
from streamstats.report import format_report


def test_json_empty_report() -> None:
    assert json.loads(format_report(Stats())) == {
        "total": 0,
        "skipped": 0,
        "by_level": {"DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 0, "CRITICAL": 0},
        "by_source": {},
        "top_error_sources": [],
        "first_timestamp": None,
        "last_timestamp": None,
    }


def test_csv_and_json_express_the_same_statistics() -> None:
    timestamp = datetime(2025, 2, 3, 10, tzinfo=UTC)
    source = "База\rданных"
    stats = analyze([Event(timestamp, Level.ERROR, source, "ошибка")])

    text = format_report(stats)
    assert "База" in text
    data = json.loads(text)
    assert data["top_error_sources"] == [{"source": source, "count": 1}]
    assert data["last_timestamp"] == "2025-02-03T10:00:00+00:00"

    rows = list(csv.reader(StringIO(format_report(stats, "csv"), newline="")))
    assert rows == [
        ["section", "key", "value"],
        ["total", "", str(data["total"])],
        ["skipped", "", str(data["skipped"])],
        *[["level", level, str(count)] for level, count in data["by_level"].items()],
        ["source", source, str(data["by_source"][source])],
        ["top_error_source", source, "1"],
        ["first_timestamp", "", data["first_timestamp"]],
        ["last_timestamp", "", data["last_timestamp"]],
    ]


def test_unknown_report_format() -> None:
    with pytest.raises(UnsupportedFormatError):
        format_report(Stats(), "xml")
