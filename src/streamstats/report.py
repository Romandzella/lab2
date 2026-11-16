"""Форматирование статистики в JSON и CSV"""

import csv
import json
from io import StringIO

from streamstats.analysis import Stats
from streamstats.errors import UnsupportedFormatError


def format_report(stats: Stats, output_format: str = "json") -> str:
    """Собирает отчёт из готовой статистики"""
    first = stats.first_timestamp.isoformat() if stats.first_timestamp else None
    last = stats.last_timestamp.isoformat() if stats.last_timestamp else None
    sources = sorted(stats.by_source.items())

    if output_format == "json":
        data = {
            "total": stats.total,
            "skipped": stats.skipped,
            "by_level": stats.by_level,
            "by_source": dict(sources),
            "top_error_sources": [
                {"source": source, "count": count}
                for source, count in stats.top_error_sources
            ],
            "first_timestamp": first,
            "last_timestamp": last,
        }
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    if output_format == "csv":
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(("section", "key", "value"))
        writer.writerow(("total", "", stats.total))
        writer.writerow(("skipped", "", stats.skipped))
        for level, count in stats.by_level.items():
            writer.writerow(("level", level, count))
        for source, count in sources:
            writer.writerow(("source", source, count))
        for source, count in stats.top_error_sources:
            writer.writerow(("top_error_source", source, count))
        writer.writerow(("first_timestamp", "", first))
        writer.writerow(("last_timestamp", "", last))
        return buffer.getvalue()

    raise UnsupportedFormatError(f"unsupported report format: {output_format}")
