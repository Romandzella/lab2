"""Тесты консольного интерфейса"""

import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from streamstats.cli import main

SRC = Path(__file__).resolve().parents[1] / "src"
EVENT = {
    "timestamp": "2025-01-01T03:00:00+03:00",
    "level": "ERROR",
    "source": "база",
    "message": "Ошибка, проверьте журнал",
}


def run_cli(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Запускает пакет из временного каталога"""
    return subprocess.run(
        [sys.executable, "-m", "streamstats", *args],
        cwd=directory,
        env={**os.environ, "PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=10,
    )


def write_jsonl(path: Path, *records: dict[str, str]) -> None:
    """Записывает переданные события в JSONL"""
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "analyze" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (["--format", "xml"], "unsupported format"),
        (["--format", "jsonl", "--level", "TRACE"], "invalid choice"),
        (["--format", "jsonl", "--since", "bad-date"], "--since: invalid ISO 8601"),
        (
            ["--format", "jsonl", "--since", "2025-02-02", "--until", "2025-01-01"],
            "--since must be earlier than --until",
        ),
        (["--format", "csv", "--follow"], "--follow requires one JSONL"),
        (["--format", "jsonl", "--idle-timeout", "1"], "requires --follow"),
        (["--format", "jsonl", "--follow", "--idle-timeout", "nan"], "positive"),
    ],
)
def test_cli_configuration_errors(
    options: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["analyze", "input.log", *options]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err


def test_cli_equivalent_inputs_and_aggregation(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "events.jsonl"
    csv_path = tmp_path / "events.csv"
    write_jsonl(jsonl_path, EVENT)
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(EVENT))
        writer.writeheader()
        writer.writerow(EVENT)
    original_csv, original_jsonl = csv_path.read_bytes(), jsonl_path.read_bytes()

    jsonl = run_cli(tmp_path, "analyze", jsonl_path.name, "--format", "jsonl")
    csv_result = run_cli(
        tmp_path, "analyze", csv_path.name, "--format", "csv", "--output", "report.json"
    )
    assert jsonl.returncode == csv_result.returncode == 0
    assert csv_result.stdout == csv_result.stderr == jsonl.stderr == ""
    assert (tmp_path / "report.json").read_text(encoding="utf-8") == jsonl.stdout
    assert "база" in jsonl.stdout

    combined = run_cli(
        tmp_path, "analyze", jsonl_path.name, jsonl_path.name, "--format", "jsonl"
    )
    assert combined.returncode == 0
    stats = json.loads(combined.stdout)
    assert stats["total"] == 2
    assert stats["by_source"] == {"база": 2}
    assert stats["first_timestamp"] == "2025-01-01T00:00:00+00:00"
    assert (csv_path.read_bytes(), jsonl_path.read_bytes()) == (
        original_csv,
        original_jsonl,
    )


@pytest.mark.parametrize("input_format", ["csv", "jsonl", "binary"])
def test_cli_empty_file(tmp_path: Path, input_format: str) -> None:
    (tmp_path / "empty.log").touch()
    result = run_cli(tmp_path, "analyze", "empty.log", "--format", input_format)
    assert result.returncode == 0
    assert result.stderr == ""
    stats = json.loads(result.stdout)
    assert stats["total"] == stats["skipped"] == 0
    assert stats["first_timestamp"] is None


@pytest.mark.parametrize("skip_invalid", [False, True])
def test_cli_broken_line_seven(tmp_path: Path, skip_invalid: bool) -> None:
    path = tmp_path / "broken.jsonl"
    row = json.dumps(EVENT)
    path.write_text((row + "\n") * 6 + "{bad\n" + row + "\n", encoding="utf-8")
    report = tmp_path / "report.json"
    report.write_text("previous", encoding="utf-8")
    flags = ["--skip-invalid"] if skip_invalid else []

    result = run_cli(
        tmp_path,
        "analyze",
        path.name,
        "--format",
        "jsonl",
        "--output",
        report.name,
        *flags,
    )
    assert "broken.jsonl:7:" in result.stderr
    assert result.stdout == ""
    if skip_invalid:
        assert result.returncode == 0
        assert "WARNING" in result.stderr
        stats = json.loads(report.read_text(encoding="utf-8"))
        assert stats["total"] == 7
        assert stats["skipped"] == 1
    else:
        assert result.returncode == 2
        assert report.read_text(encoding="utf-8") == "previous"


def test_cli_reports_bad_csv_timestamp(tmp_path: Path) -> None:
    (tmp_path / "events.csv").write_text(
        "timestamp,level,source,message\nbad-date,INFO,api,hello\n", encoding="utf-8"
    )
    result = run_cli(tmp_path, "analyze", "events.csv", "--format", "csv")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "events.csv:2:" in result.stderr and "timestamp" in result.stderr


def test_cli_rejects_output_hard_linked_to_input(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    alias = tmp_path / "alias.json"
    write_jsonl(path, EVENT)
    before = path.read_bytes()
    try:
        alias.hardlink_to(path)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    result = run_cli(
        tmp_path, "analyze", path.name, "--format", "jsonl", "--output", alias.name
    )
    assert result.returncode == 2
    assert "--output must not be an input file" in result.stderr
    assert path.read_bytes() == alias.read_bytes() == before


def test_cli_plugins_and_csv_output(tmp_path: Path) -> None:
    (tmp_path / "tagged_plugin.py").write_text(
        "from streamstats.parsers import parse_jsonl\n"
        "def parse_tagged(lines, path):\n"
        "    stripped = (line.removeprefix('EVENT ') for line in lines)\n"
        "    yield from parse_jsonl(stripped, path)\n"
        "PARSERS = {'tagged': parse_tagged}\n",
        encoding="utf-8",
    )
    (tmp_path / "events.log").write_text(
        "EVENT " + json.dumps(EVENT, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    result = run_cli(
        tmp_path,
        "analyze",
        "events.log",
        "--format",
        "tagged",
        "--plugin",
        "tagged_plugin",
        "--report-format",
        "csv",
        "--level",
        "error",
        "--source",
        "база",
        "--since",
        "2025-01-01T00:00:00Z",
    )
    assert result.returncode == 0
    assert result.stderr == ""
    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert rows[0] == ["section", "key", "value"]
    assert rows[1] == ["total", "", "1"]
    assert ["source", "база", "1"] in rows
    assert [] not in rows


def test_cli_follow_with_idle_timeout(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    write_jsonl(path, EVENT)
    result = run_cli(
        tmp_path,
        "analyze",
        path.name,
        "--format",
        "jsonl",
        "--follow",
        "--idle-timeout",
        "0.01",
    )
    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout)["total"] == 1
