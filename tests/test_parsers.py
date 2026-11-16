"""Тесты парсеров и повреждённых записей"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from io import BytesIO, StringIO
from pathlib import Path
from struct import pack

import pytest

from streamstats.errors import CliConfigError, InvalidEventError, TimestampError
from streamstats.models import Event, Level, parse_timestamp
from streamstats.parsers import parse_binary, parse_csv, parse_jsonl

CSV_HEADER = "timestamp,level,source,message\n"
EVENT = Event(datetime(2026, 10, 14, 9, tzinfo=UTC), Level.ERROR, "Сеть", "")
EVENT_JSON = (
    '{"timestamp":"2026-10-14T12:00:00+03:00","level":"ERROR",'
    '"source":"Сеть","message":""}'
)


def test_csv_column_order_and_multiline_message() -> None:
    stream = StringIO(
        "message,source,timestamp,level,extra\r\n"
        '"Сбой, повтор\r\nещё раз",Сеть,2026-10-14T09:00:00Z,ERROR,note\r\n',
        newline="",
    )

    assert list(parse_csv(stream, Path("events.csv"))) == [
        Event(EVENT.timestamp, EVENT.level, EVENT.source, "Сбой, повтор\r\nещё раз")
    ]


def test_jsonl_event_with_empty_message() -> None:
    assert list(parse_jsonl([EVENT_JSON], Path("events.jsonl"))) == [EVENT]


@pytest.mark.parametrize(
    "source",
    ["2026-10-14T09:00:00Z", "2026-10-14T12:00:00+03:00", "2026-10-14T09:00:00"],
)
def test_timestamp_is_normalized_to_utc(source: str) -> None:
    assert parse_timestamp(source) == EVENT.timestamp


@pytest.mark.parametrize("source", [None, "2026-02-30", "0001-01-01T00:00+03:00"])
def test_invalid_timestamp(source: object) -> None:
    with pytest.raises(TimestampError, match="timestamp"):
        parse_timestamp(source)


@pytest.mark.parametrize("content", ["", CSV_HEADER])
def test_empty_csv(content: str) -> None:
    assert list(parse_csv(StringIO(content), Path("empty.csv"))) == []


def test_empty_jsonl() -> None:
    assert list(parse_jsonl(StringIO(), Path("empty.jsonl"))) == []


@pytest.mark.parametrize("header", ["level,message\n", '"open,quote\n', "\n"])
def test_invalid_csv_header(header: str) -> None:
    with pytest.raises(CliConfigError, match=r"broken\.csv:1:"):
        next(parse_csv(StringIO(header), Path("broken.csv")))


def test_csv_error_uses_first_physical_line_and_recovers() -> None:
    stream = StringIO(
        CSV_HEADER
        + 'bad date,INFO,core,"message\ncontinued"\n'
        + "2026-10-14T09:00:00,ERROR,Сеть,\n"
    )
    error, event = parse_csv(stream, Path("broken.csv"))

    assert isinstance(error, TimestampError)
    assert error.line == 2
    assert str(error).startswith("broken.csv:2: invalid ISO 8601 timestamp")
    assert event == EVENT


@pytest.mark.parametrize("row", ["\n", "a,b,c\n", "a,b,c,d,e\n", '"a"b,c,d\n'])
def test_wrong_csv_record_yields_error(row: str) -> None:
    records = parse_csv([CSV_HEADER, row], Path("broken.csv"))
    error = next(records)

    assert isinstance(error, InvalidEventError)
    assert error.line == 2


@pytest.mark.parametrize(
    ("row", "detail"),
    [
        ("{", "invalid JSON"),
        ("\n", "invalid JSON"),
        ("[1, 2]", "expected a JSON object"),
        ('{"x": NaN}', "invalid JSON"),
        ("{}", "missing field: timestamp"),
        (EVENT_JSON.replace("ERROR", "FATAL"), "invalid level"),
        (EVENT_JSON.replace('"Сеть"', '"  "'), "source"),
        (EVENT_JSON.replace('""', "null"), "message"),
        (EVENT_JSON.replace('"Сеть"', r'"\ud800"'), "source"),
    ],
)
def test_jsonl_error_on_seventh_line(row: str, detail: str) -> None:
    good_rows = [EVENT_JSON] * 6
    records = parse_jsonl([*good_rows, row, EVENT_JSON], Path("broken.jsonl"))
    assert [next(records) for _ in good_rows] == [EVENT] * 6
    error = next(records)

    assert isinstance(error, InvalidEventError)
    assert str(error).startswith(f"broken.jsonl:7: {detail}")
    assert next(records) == EVENT


def test_invalid_utf8_in_extra_field(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    broken = EVENT_JSON[:-1].encode() + b',"extra":"\xff"}\n'
    path.write_bytes(broken + EVENT_JSON.encode())

    with path.open(encoding="utf-8-sig", errors="surrogateescape") as stream:
        error, event = parse_jsonl(stream, path)

    assert isinstance(error, InvalidEventError)
    assert str(error).endswith(":1: invalid UTF-8")
    assert event == EVENT


def test_jsonl_reads_on_demand() -> None:
    def lines() -> Iterator[str]:
        yield EVENT_JSON
        pytest.fail("second line was read eagerly")

    assert next(parse_jsonl(lines(), Path("events.jsonl"))) == EVENT


def test_binary_recovers_after_a_complete_invalid_record() -> None:
    bad = b"{"
    good = json.dumps(json.loads(EVENT_JSON), ensure_ascii=False).encode()
    stream = BytesIO(pack(">I", len(bad)) + bad + pack(">I", len(good)) + good)

    error, event = parse_binary(stream, Path("events.bin"))

    assert isinstance(error, InvalidEventError)
    assert str(error) == "events.bin:1: invalid JSON"
    assert event == EVENT


@pytest.mark.parametrize(
    ("data", "detail"),
    [
        (b"\x00\x00", "truncated binary header"),
        (pack(">I", 3) + b"{}", "truncated binary payload"),
        (pack(">I", 0), "invalid binary length"),
        (pack(">I", 1024 * 1024 + 1), "invalid binary length"),
    ],
)
def test_binary_invalid_framing(data: bytes, detail: str) -> None:
    (error,) = parse_binary(BytesIO(data), Path("events.bin"))

    assert isinstance(error, InvalidEventError)
    assert str(error) == f"events.bin:1: {detail}"
