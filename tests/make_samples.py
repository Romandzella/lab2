"""Входные файлы для ручной проверки"""

import argparse
import csv
import json
from pathlib import Path


def main() -> None:
    """Записывает примеры в новый каталог"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    directory = parser.parse_args().directory
    directory.mkdir()

    events = [
        {
            "timestamp": f"2025-01-01T00:0{minute}:00Z",
            "level": "CRITICAL" if minute == 2 else "ERROR",
            "source": source,
            "message": "Ошибка, подробности в журнале",
        }
        for minute, source in enumerate(
            ("zeta", "delta", "beta", "alpha", "gamma", "база"), start=1
        )
    ]
    extra = {
        "timestamp": "2025-01-01T03:00:00+03:00",
        "level": "INFO",
        "source": "база",
        "message": "Готово",
    }
    rows = [json.dumps(event, ensure_ascii=False) for event in events]
    extra_row = json.dumps(extra, ensure_ascii=False)
    (directory / "events.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (directory / "extra.jsonl").write_text(extra_row + "\n", encoding="utf-8")
    broken = "\n".join([*rows, "{broken", extra_row]) + "\n"
    (directory / "broken.jsonl").write_text(broken, encoding="utf-8")
    (directory / "empty.jsonl").touch()

    for name, records in (
        ("events.csv", events),
        ("bad-date.csv", [{**extra, "timestamp": "bad-date"}]),
    ):
        with (directory / name).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(extra))
            writer.writeheader()
            writer.writerows(records)

    with (directory / "events.bin").open("wb") as stream:
        for row in rows:
            payload = row.encode("utf-8")
            stream.write(len(payload).to_bytes(4, "big"))
            stream.write(payload)


if __name__ == "__main__":
    main()
