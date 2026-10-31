from __future__ import annotations

import io
import json
from typing import Any

import pytest

from taskboard.api import make_app
from taskboard.repository import load_tasks


@pytest.fixture
def tasks():
    """A fresh copy of data/tasks.json for every test."""
    return load_tasks()


class WsgiClient:
    """Call the WSGI app in-process and decode the response."""

    def __init__(self, app) -> None:
        self.app = app

    def request(self, method: str, path: str, query: str = "", body: Any = None) -> tuple[int, Any]:
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "CONTENT_LENGTH": str(len(raw)),
            "wsgi.input": io.BytesIO(raw),
        }
        captured: dict[str, Any] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = int(status.split(" ", 1)[0])
            captured["headers"] = dict(headers)

        payload = b"".join(self.app(environ, start_response))
        if captured["headers"]["Content-Type"].startswith("application/json"):
            return captured["status"], json.loads(payload.decode("utf-8"))
        return captured["status"], payload.decode("utf-8")

    def get(self, path: str, query: str = "") -> tuple[int, Any]:
        return self.request("GET", path, query)

    def post(self, path: str, body: Any) -> tuple[int, Any]:
        return self.request("POST", path, body=body)


@pytest.fixture
def client(tasks):
    return WsgiClient(make_app(tasks))
