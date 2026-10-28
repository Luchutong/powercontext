"""Load the initial task data set."""

from __future__ import annotations

import json
from pathlib import Path

from taskboard.models import Task

DEFAULT_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "tasks.json"


def load_tasks(path: str | Path | None = None) -> list[Task]:
    """Read tasks from a JSON file (defaults to data/tasks.json)."""
    data_file = Path(path) if path is not None else DEFAULT_DATA_FILE
    rows = json.loads(data_file.read_text(encoding="utf-8"))
    return [
        Task(
            id=int(row["id"]),
            title=row["title"],
            status=row["status"],
            priority=row["priority"],
            tags=tuple(row.get("tags", ())),
        )
        for row in rows
    ]
