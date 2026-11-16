"""Потоковое чтение записей CSV, JSONL и бинарного формата"""

import csv
import json
import re
from collections.abc import Iterable, Iterator, Mapping
from io import BufferedIOBase
from pathlib import Path
from time import monotonic, sleep
from typing import BinaryIO, NoReturn

from streamstats.errors import CliConfigError, InvalidEventError
from streamstats.models import Event, Level, parse_timestamp

_FIELDS = ("timestamp", "level", "source", "message")
_SURROGATE = re.compile(r"[\ud800-\udfff]")
_MAX_BINARY_PAYLOAD = 1024 * 1024


def event_from_mapping(record: Mapping[str, object]) -> Event:
    """Проверяет обязательные поля и создаёт событие"""
    for field in _FIELDS:
        if field not in record:
            raise InvalidEventError(f"missing field: {field}")

    timestamp = parse_timestamp(record["timestamp"])
    raw_level = record["level"]
    if not isinstance(raw_level, str):
        raise InvalidEventError("level must be a string")
    try:
        level = Level(raw_level)
    except ValueError:
        raise InvalidEventError(f"invalid level: {raw_level!r}") from None

    source = record["source"]
    if not isinstance(source, str) or not source.strip() or _SURROGATE.search(source):
        raise InvalidEventError("source must be a non-empty Unicode string")

    message = record["message"]
    if not isinstance(message, str) or _SURROGATE.search(message):
        raise InvalidEventError("message must be a Unicode string")

    return Event(timestamp, level, source, message)


def _reject_constant(value: str) -> NoReturn:
    """Запрещает NaN и Infinity в JSON"""
    raise ValueError(f"invalid JSON constant: {value}")


def _event_from_json(text: str) -> Event:
    """Декодирует одну запись JSON"""
    if _SURROGATE.search(text):
        raise InvalidEventError("invalid UTF-8")
    try:
        record = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise InvalidEventError("invalid JSON") from None
    if not isinstance(record, dict):
        raise InvalidEventError("expected a JSON object")
    return event_from_mapping(record)


def parse_jsonl(
    stream: Iterable[str], path: Path
) -> Iterator[Event | InvalidEventError]:
    """Возвращает событие или ошибку для каждой строки JSONL"""
    for line, text in enumerate(stream, start=1):
        try:
            event = _event_from_json(text)
        except InvalidEventError as error:
            yield error.at(path, line)
        else:
            yield event


def parse_csv(stream: Iterable[str], path: Path) -> Iterator[Event | InvalidEventError]:
    """Проверяет заголовок CSV и читает записи по одной"""
    reader = csv.reader(stream, strict=True)
    try:
        header = next(reader, None)
    except csv.Error as error:
        raise CliConfigError(f"{path}:1: invalid CSV header: {error}") from None
    if header is None:
        return
    if any(_SURROGATE.search(name) for name in header):
        raise CliConfigError(f"{path}:1: invalid UTF-8 in CSV header")
    if len(set(header)) != len(header):
        raise CliConfigError(f"{path}:1: duplicate CSV column names")
    missing = [field for field in _FIELDS if field not in header]
    if missing:
        raise CliConfigError(f"{path}:1: missing CSV columns: {', '.join(missing)}")

    while True:
        line = reader.line_num + 1
        try:
            row = next(reader)
        except StopIteration:
            return
        except csv.Error as error:
            yield InvalidEventError(f"invalid CSV: {error}", path, line)
            continue

        try:
            if len(row) != len(header):
                raise InvalidEventError("CSV field count differs from header")
            if any(_SURROGATE.search(cell) for cell in row):
                raise InvalidEventError("invalid UTF-8")
            event = event_from_mapping(dict(zip(header, row, strict=True)))
        except InvalidEventError as error:
            yield error.at(path, line)
        else:
            yield event


def parse_binary(
    stream: BufferedIOBase, path: Path
) -> Iterator[Event | InvalidEventError]:
    """Читает записи с четырёхбайтовой длиной и телом UTF-8 JSON"""
    buffer = memoryview(bytearray(_MAX_BINARY_PAYLOAD))
    header = buffer[:4]
    record_number = 0

    while True:
        size_read = stream.readinto(header)
        if size_read == 0:
            return
        record_number += 1
        if size_read != len(header):
            yield InvalidEventError("truncated binary header", path, record_number)
            return

        size = int.from_bytes(header, "big")
        if not 1 <= size <= len(buffer):
            yield InvalidEventError("invalid binary length", path, record_number)
            return
        payload = buffer[:size]
        if stream.readinto(payload) != size:
            yield InvalidEventError("truncated binary payload", path, record_number)
            return

        try:
            event = _event_from_json(str(payload, "utf-8"))
        except UnicodeDecodeError:
            yield InvalidEventError("invalid UTF-8", path, record_number)
        except InvalidEventError as error:
            yield error.at(path, record_number)
        else:
            yield event


def follow_lines(
    stream: BinaryIO,
    *,
    idle_timeout: float | None = None,
    poll_interval: float = 0.1,
) -> Iterator[str]:
    """Ждёт дописывания файла и отдаёт только полные строки"""
    pending = bytearray()
    encoding = "utf-8-sig"
    last_read = monotonic()

    while True:
        try:
            chunk = stream.readline()
            if not chunk:
                if idle_timeout is not None and monotonic() - last_read >= idle_timeout:
                    return
                sleep(poll_interval)
                continue
        except KeyboardInterrupt:
            return

        last_read = monotonic()
        pending.extend(chunk)
        if chunk.endswith(b"\n"):
            yield pending.decode(encoding, errors="surrogateescape")
            pending.clear()
            encoding = "utf-8"
