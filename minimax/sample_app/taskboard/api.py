"""Minimal WSGI HTTP API for the task board (standard library only)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import parse_qs

from taskboard.export import export_csv
from taskboard.models import Task
from taskboard.repository import load_tasks
from taskboard.service import QueryError, ValidationError, create_task, search_tasks

StartResponse = Callable[[str, list[tuple[str, str]]], Any]

_STATUS_TEXT = {200: "OK", 201: "Created", 400: "Bad Request", 404: "Not Found", 422: "Unprocessable Entity"}


def _respond(start_response: StartResponse, code: int, body: bytes, content_type: str) -> list[bytes]:
    start_response(f"{code} {_STATUS_TEXT[code]}", [("Content-Type", content_type), ("Content-Length", str(len(body)))])
    return [body]


def _json(start_response: StartResponse, code: int, payload: Any) -> list[bytes]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _respond(start_response, code, body, "application/json; charset=utf-8")


def _query_params(environ: dict[str, Any]) -> dict[str, str]:
    """Parse the query string; blank values are kept and the last value of a repeated key wins."""
    parsed = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def list_tasks_view(environ: dict[str, Any], start_response: StartResponse, tasks: list[Task]) -> list[bytes]:
    """GET /tasks: delegate to search_tasks and map QueryError to HTTP 400 (LIST-002, LIST-003, LIST-004)."""
    try:
        result = search_tasks(tasks, _query_params(environ))
    except QueryError as error:
        return _json(start_response, 400, {"error": str(error)})
    return _json(start_response, 200, result)


def create_task_view(environ: dict[str, Any], start_response: StartResponse, tasks: list[Task]) -> list[bytes]:
    """POST /tasks: create one task from a JSON body (CRT-001, CRT-002, CRT-003)."""
    length = int(environ.get("CONTENT_LENGTH") or 0)
    raw = environ["wsgi.input"].read(length) if length else b""
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _json(start_response, 400, {"error": "body must be JSON"})
    if not isinstance(payload, dict):
        return _json(start_response, 400, {"error": "body must be a JSON object"})
    try:
        task = create_task(tasks, payload)
    except ValidationError as error:
        return _json(start_response, 422, {"error": str(error)})
    return _json(start_response, 201, task.to_dict())


def export_view(start_response: StartResponse, tasks: list[Task]) -> list[bytes]:
    """GET /tasks/export.csv (EXP-001, EXP-002)."""
    return _respond(start_response, 200, export_csv(tasks).encode("utf-8"), "text/csv; charset=utf-8")


def make_app(tasks: list[Task] | None = None) -> Callable[[dict[str, Any], StartResponse], Iterable[bytes]]:
    """Build the WSGI application around an in-memory task list."""
    store = list(load_tasks()) if tasks is None else tasks

    def app(environ: dict[str, Any], start_response: StartResponse) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")
        if path == "/tasks" and method == "GET":
            return list_tasks_view(environ, start_response, store)
        if path == "/tasks" and method == "POST":
            return create_task_view(environ, start_response, store)
        if path == "/tasks/export.csv" and method == "GET":
            return export_view(start_response, store)
        return _json(start_response, 404, {"error": "not found"})

    return app
