"""Консольный интерфейс (CLI) для пакета streamstats"""

import argparse
import logging
import sys
from collections.abc import Callable, Generator, Iterable, Iterator, Sequence
from contextlib import closing
from datetime import datetime
from importlib import import_module
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import NoReturn

from streamstats.analysis import analyze
from streamstats.errors import (
    CliConfigError,
    InvalidEventError,
    StreamStatsError,
    TimestampError,
    UnsupportedFormatError,
)
from streamstats.models import Event, Level, parse_timestamp
from streamstats.parsers import follow_lines, parse_binary, parse_csv, parse_jsonl
from streamstats.report import format_report

type Record = Event | InvalidEventError
type TextParser = Callable[[Iterable[str], Path], Iterator[Record]]


class ArgumentParser(argparse.ArgumentParser):
    """Парсер аргументов с общей обработкой ошибок"""

    def error(self, message: str) -> NoReturn:
        """Выбрасывает ошибку с подсказкой по запуску"""
        raise CliConfigError(f"{message} (см. `{self.prog} --help`)")


def create_parser() -> argparse.ArgumentParser:
    """Настраивает команду analyze и её параметры"""
    parser = ArgumentParser(prog="streamstats", description="Анализатор событий")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("analyze", help="Построить отчёт по файлам")
    command.add_argument("inputs", nargs="+", type=Path, metavar="INPUT")
    command.add_argument(
        "--format", required=True, help="csv, jsonl, binary или плагин"
    )
    command.add_argument("--output", type=Path, help="Файл отчёта вместо stdout")
    command.add_argument("--report-format", choices=("json", "csv"), default="json")
    command.add_argument("--skip-invalid", action="store_true")
    command.add_argument(
        "--level", action="append", type=str.upper, choices=list(Level)
    )
    command.add_argument("--source", action="append")
    command.add_argument("--since", help="Нижняя граница времени включительно")
    command.add_argument("--until", help="Верхняя граница времени исключительно")
    command.add_argument("--plugin", action="append", metavar="MODULE")
    command.add_argument(
        "--follow", action="store_true", help="Ждать дописывания JSONL"
    )
    command.add_argument("--idle-timeout", type=float, metavar="SECONDS")
    return parser


def _text_parsers(modules: Sequence[str]) -> dict[str, TextParser]:
    """Собирает встроенные парсеры и парсеры из плагинов"""
    parsers: dict[str, TextParser] = {"csv": parse_csv, "jsonl": parse_jsonl}
    for name in modules:
        try:
            module = import_module(name)
        except Exception as exc:
            raise CliConfigError(f"cannot import plugin {name}: {exc}") from exc

        extra_parsers = getattr(module, "PARSERS", None)
        if not isinstance(extra_parsers, dict) or not extra_parsers:
            raise CliConfigError(f"plugin {name} must export a nonempty PARSERS dict")
        for label, parse in extra_parsers.items():
            if not isinstance(label, str) or not label or not callable(parse):
                raise CliConfigError(f"invalid parser entry in plugin {name}")
            if label in parsers or label == "binary":
                raise CliConfigError(f"parser already registered: {label}")
            parsers[label] = parse
    return parsers


def _time_option(value: str | None, option: str) -> datetime | None:
    """Разбирает время из аргументов CLI"""
    if value is None:
        return None
    try:
        return parse_timestamp(value)
    except TimestampError as exc:
        raise CliConfigError(f"{option}: {exc}") from exc


def _check_output(output: Path, inputs: Sequence[Path]) -> None:
    """Проверяет, что путь отчёта не совпадает с входным файлом"""
    target = output.resolve()
    for path in inputs:
        if target == path.resolve():
            raise CliConfigError("--output must not be an input file")
        try:
            same_file = output.samefile(path)
        except FileNotFoundError:
            continue
        if same_file:
            raise CliConfigError("--output must not be an input file")


def _read_records(
    paths: Sequence[Path],
    text_parser: TextParser | None,
    *,
    follow: bool,
    idle_timeout: float | None,
) -> Generator[Record, None, None]:
    """Читает записи из входных файлов по очереди"""
    for path in paths:
        if follow:
            with path.open("rb") as stream:
                lines = follow_lines(stream, idle_timeout=idle_timeout)
                yield from parse_jsonl(lines, path)
        elif text_parser is None:
            with path.open("rb") as stream:
                yield from parse_binary(stream, path)
        else:
            with path.open(
                encoding="utf-8-sig", errors="surrogateescape", newline=""
            ) as lines:
                yield from text_parser(lines, path)


def _write_report(path: Path, content: str) -> None:
    """Записывает отчёт через временный файл"""
    stream = NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
    )
    temporary = Path(stream.name)
    try:
        with stream:
            stream.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI"""
    logging.basicConfig(format="%(levelname)s: %(message)s")
    try:
        args = create_parser().parse_args(argv)
        parsers = _text_parsers(args.plugin or [])
        if args.format != "binary" and args.format not in parsers:
            raise UnsupportedFormatError(f"unsupported format: {args.format}")

        since = _time_option(args.since, "--since")
        until = _time_option(args.until, "--until")
        if since is not None and until is not None and since >= until:
            raise CliConfigError("--since must be earlier than --until")
        if args.follow and (args.format != "jsonl" or len(args.inputs) != 1):
            raise CliConfigError("--follow requires one JSONL input file")
        if args.idle_timeout is not None and (
            not args.follow or not isfinite(args.idle_timeout) or args.idle_timeout <= 0
        ):
            raise CliConfigError(
                "--idle-timeout requires --follow and positive seconds"
            )
        if args.output is not None:
            _check_output(args.output, args.inputs)

        records = _read_records(
            args.inputs,
            parsers.get(args.format),
            follow=args.follow,
            idle_timeout=args.idle_timeout,
        )
        with closing(records):
            stats = analyze(
                records,
                skip_invalid=args.skip_invalid,
                levels={Level(level) for level in args.level or []},
                sources=set(args.source or []),
                since=since,
                until=until,
            )
        report = format_report(stats, args.report_format)
        if args.output is None:
            sys.stdout.buffer.write(report.encode("utf-8"))
        else:
            _write_report(args.output, report)
    except (StreamStatsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0
