"""CSV export of tasks."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable

from taskboard.models import Task

EXPORT_COLUMNS = ("id", "title", "status", "priority", "tags")


def export_csv(tasks: Iterable[Task]) -> str:
    """Render tasks as CSV ordered by id; tags are joined with ';' (EXP-001, EXP-002)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    for task in sorted(tasks, key=lambda item: item.id):
        writer.writerow([task.id, task.title, task.status, task.priority, ";".join(task.tags)])
    return buffer.getvalue()
