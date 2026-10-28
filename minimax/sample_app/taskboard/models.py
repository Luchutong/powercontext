"""Domain model for tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VALID_STATUSES = ("open", "in_progress", "done")
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class Task:
    """One task record. Tags are stored lower-case and de-duplicated (CRT-003)."""

    id: int
    title: str
    status: str = "open"
    priority: str = "medium"
    tags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Serialize with the public field order used by GET /tasks (LIST-004)."""
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "priority": self.priority,
            "tags": list(self.tags),
        }
