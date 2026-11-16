"""Замер памяти при анализе JSONL"""

import argparse
import json
import tracemalloc
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from streamstats.analysis import analyze
from streamstats.parsers import parse_jsonl
from streamstats.report import format_report


def generate_input(path: Path, size_mb: int) -> int:
    """Пишет одинаковые записи блоками до указанного размера в МБ"""
    event = {
        "timestamp": "2025-01-01T00:00:00Z",
        "level": "ERROR",
        "source": "demo",
        "message": "x" * 4096,
    }
    line = (json.dumps(event) + "\n").encode("utf-8")
    target = size_mb * 1_000_000
    rows = (target + len(line) - 1) // len(line)
    blocks, remainder = divmod(rows, 1024)
    block = line * 1024
    with path.open("wb") as stream:
        for _ in range(blocks):
            stream.write(block)
        stream.write(line * remainder)
    return rows


def measure(path: Path) -> tuple[int, int, float]:
    """Возвращает число событий, пик в байтах и время анализа"""
    started = perf_counter()
    tracemalloc.start()
    try:
        with path.open(
            encoding="utf-8-sig", errors="surrogateescape", newline=""
        ) as lines:
            stats = analyze(parse_jsonl(lines, path))
        format_report(stats)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return stats.total, peak, perf_counter() - started


def main() -> None:
    """Создаёт временные файлы и выводит результаты замеров"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[10, 500])
    args = parser.parse_args()
    if any(size <= 0 for size in args.sizes):
        parser.error("размер в МБ должен быть положительным")

    with TemporaryDirectory() as directory:
        path = Path(directory) / "events.jsonl"
        for size in args.sizes:
            expected = generate_input(path, size)
            total, peak, elapsed = measure(path)
            if total != expected:
                raise RuntimeError(f"ожидалось {expected} событий, получено {total}")
            print(
                f"{path.stat().st_size:,} B | {total:,} events | "
                f"{peak:,} B peak | {elapsed:.2f} s"
            )


if __name__ == "__main__":
    main()
