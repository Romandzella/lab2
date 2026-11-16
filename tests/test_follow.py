"""Тесты чтения дописываемого файла"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from streamstats.parsers import follow_lines


def test_follow_waits_for_complete_utf8_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"\xef\xbb\xbffirst\n")
    text = "база\n".encode()
    writes = iter([text[:1], text[1:]])

    def append(_: float) -> None:
        with path.open("ab") as writer:
            writer.write(next(writes))

    monkeypatch.setattr("streamstats.parsers.sleep", append)
    with path.open("rb") as stream:
        lines = follow_lines(stream)
        assert next(lines) == "first\n"
        assert next(lines) == "база\n"


def test_follow_stops_on_interrupt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "partial.jsonl"
    path.write_bytes(b"first\npartial")

    def interrupt(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("streamstats.parsers.sleep", interrupt)
    with path.open("rb") as stream:
        lines: Iterator[str] = follow_lines(stream)
        assert list(lines) == ["first\n"]
