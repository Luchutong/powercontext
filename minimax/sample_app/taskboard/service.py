"""Business rules for listing and creating tasks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from taskboard.models import PRIORITY_ORDER, VALID_STATUSES, Task

MAX_LIMIT = 100
MAX_TITLE_LENGTH = 80


class QueryError(ValueError):
    """Invalid GET /tasks query parameter; the API maps it to HTTP 400 (LIST-002, LIST-003)."""


class ValidationError(ValueError):
    """Invalid POST /tasks payload; the API maps it to HTTP 422 (CRT-001)."""


def _parse_limit(raw: str | None) -> int | None:
    """Parse the ``limit`` query parameter: an integer from 1 to 100 (LIST-003)."""
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise QueryError(f"limit must be an integer between 1 and {MAX_LIMIT}") from None
    if not 1 <= value <= MAX_LIMIT:
        raise QueryError(f"limit must be an integer between 1 and {MAX_LIMIT}")
    return value


def search_tasks(tasks: Iterable[Task], params: Mapping[str, str]) -> dict[str, Any]:
    """Filter, sort and paginate tasks for GET /tasks.

    Query parameters handled here: ``status`` (LIST-002) and ``limit`` (LIST-003).
    Results are sorted by priority, then id (LIST-001); ``total`` counts matches
    before ``limit`` is applied (LIST-004). Unknown parameters are ignored (LIST-005).
    """
    status = params.get("status")
    if status is not None and status not in VALID_STATUSES:
        raise QueryError(f"status must be one of {', '.join(VALID_STATUSES)}")
    limit = _parse_limit(params.get("limit"))

    matched = [task for task in tasks if status is None or task.status == status]
    matched.sort(key=lambda task: (PRIORITY_ORDER[task.priority], task.id))
    total = len(matched)
    if limit is not None:
        matched = matched[:limit]
    return {"items": [task.to_dict() for task in matched], "total": total}


def _normalize_tags(raw: Any) -> tuple[str, ...]:
    """Normalize tags for storage: strip, lower-case, drop empties, keep first occurrence (CRT-003)."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(tag, str) for tag in raw):
        raise ValidationError("tags must be a list of strings")
    normalized: list[str] = []
    for tag in raw:
        value = tag.strip().lower()
        if value and value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def create_task(tasks: list[Task], payload: Mapping[str, Any]) -> Task:
    """Validate a POST /tasks payload and append the new task (CRT-001, CRT-002, CRT-003)."""
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValidationError("title is required")
    title = title.strip()
    if len(title) > MAX_TITLE_LENGTH:
        raise ValidationError(f"title must be at most {MAX_TITLE_LENGTH} characters")
    priority = payload.get("priority", "medium")
    if priority not in PRIORITY_ORDER:
        raise ValidationError("priority must be one of high, medium, low")
    task = Task(
        id=max((existing.id for existing in tasks), default=0) + 1,
        title=title,
        status="open",
        priority=priority,
        tags=_normalize_tags(payload.get("tags")),
    )
    tasks.append(task)
    return task
