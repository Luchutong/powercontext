from __future__ import annotations

import hashlib
import re
import threading
from datetime import date

COLLECTION = "zhiku_todos"
UNSPECIFIED = "未指定"
PAGE_SIZE = 100

_TASK = re.compile(r"^\s*- \[(?P<done>[ xX])\]\s+(?P<body>.+?)\s*$")
_OWNER = re.compile(r"^(?P<owner>[^：:]{1,30}?)[：:]\s*(?P<text>.+)$")
_DUE = re.compile(
    r"(?<!\d)(?:(?P<year>20\d{2})[-/.])?(?P<month>\d{1,2})[-/.](?P<day>\d{1,2})(?!\d)"
)


def _parse_body(body: str) -> tuple[str, str, str]:
    owner = UNSPECIFIED
    owner_match = _OWNER.match(body)
    if owner_match:
        owner = owner_match.group("owner").strip()
        body = owner_match.group("text").strip()

    due = UNSPECIFIED
    due_match = _DUE.search(body)
    if due_match:
        year = due_match.group("year")
        month = int(due_match.group("month"))
        day = int(due_match.group("day"))
        try:
            parsed = date(int(year) if year else 2000, month, day)
        except ValueError:
            parsed = None
        if parsed:
            due = parsed.isoformat() if year else f"{month:02d}/{day:02d}"
            body = (body[: due_match.start()] + body[due_match.end() :]).strip()
            body = re.sub(r"截止(?:日期)?\s*[:：]?\s*$", "", body)
            body = re.sub(r"[（(]\s*[）)]", "", body)
            body = re.sub(r"\s{2,}", " ", body).strip(" -—–，,；;")

    return body, owner, due


def parse_tasks(chunks: list[dict]) -> list[dict]:
    tasks: dict[str, dict] = {}
    ordered = sorted(chunks, key=lambda c: (c["meta"].get("source", ""), c["meta"].get("chunk", 0)))
    for chunk in ordered:
        meta = chunk["meta"]
        source = meta.get("source", "")
        for line in chunk["text"].splitlines():
            match = _TASK.match(line)
            if not match:
                continue
            body = re.sub(r"\s+", " ", match.group("body").strip())
            todo_id = _todo_id(source, line.strip())
            if todo_id in tasks:
                continue
            text, owner, due = _parse_body(body)
            source_done = match.group("done").lower() == "x"
            tasks[todo_id] = {
                "text": text,
                "owner": owner,
                "due": due,
                "source": source,
                "title": meta.get("title", ""),
                "tag": meta.get("tag", ""),
                "chunk": int(meta.get("chunk", 0)),
                "raw": line.strip(),
                "source_done": source_done,
                "done": source_done,
                "user_override": False,
            }
    return list(tasks.values())


class TodoStore:
    def __init__(self, client, lock: threading.RLock | None = None):
        self.col = client.get_or_create_collection(COLLECTION, embedding_function=None)
        self._lock = lock or threading.RLock()

    def _all(self, source: str | None = None) -> dict[str, dict]:
        items: dict[str, dict] = {}
        offset = 0
        where = {"source": {"$eq": source}} if source else None
        while True:
            res = self.col.get(where=where, limit=PAGE_SIZE, offset=offset, include=["metadatas"])
            ids = res.get("ids") or []
            metadatas = res.get("metadatas") or []
            items.update({id_: meta for id_, meta in zip(ids, metadatas)})
            if len(ids) < PAGE_SIZE:
                return items
            offset += len(ids)

    def sync_source(self, source: str, chunks: list[dict]) -> int:
        with self._lock:
            parsed = {_todo_id(source, todo["raw"]): todo for todo in parse_tasks(chunks)}
            existing = self._all(source)
            for todo_id, todo in parsed.items():
                old = existing.get(todo_id)
                if old:
                    todo["user_override"] = bool(old.get("user_override", False))
                    if todo["user_override"]:
                        todo["done"] = bool(old.get("done", False))
            if parsed:
                self.col.upsert(ids=list(parsed), metadatas=list(parsed.values()))
            stale = [id_ for id_ in existing if id_ not in parsed]
            if stale:
                self.col.delete(ids=stale)
            return len(parsed)

    def query(
        self,
        owner: str | None = None,
        status: str = "all",
        due: str | None = None,
    ) -> dict:
        with self._lock:
            todos = [{"id": id_, **meta} for id_, meta in self._all().items()]
        owners = sorted({t["owner"] for t in todos}, key=_unspecified_last)
        due_dates = sorted({t["due"] for t in todos}, key=_unspecified_last)
        if owner:
            todos = [t for t in todos if t["owner"] == owner]
        if status == "open":
            todos = [t for t in todos if not t["done"]]
        elif status == "done":
            todos = [t for t in todos if t["done"]]
        if due:
            todos = [t for t in todos if t["due"] == due]
        todos.sort(key=lambda t: (t["done"], _unspecified_last(t["due"]), t["source"], t["chunk"], t["text"]))
        return {"todos": todos, "owners": owners, "due_dates": due_dates}

    def set_done(self, todo_id: str, done: bool) -> dict | None:
        with self._lock:
            res = self.col.get(ids=todo_id, include=["metadatas"])
            ids = res.get("ids") or []
            if not ids:
                return None
            meta = (res.get("metadatas") or [])[0]
            meta["done"] = done
            meta["user_override"] = True
            self.col.update(ids=todo_id, metadatas=meta)
            return {"id": todo_id, **meta}

    def delete_source(self, source: str) -> None:
        with self._lock:
            self.col.delete(where={"source": {"$eq": source}})


def _todo_id(source: str, raw: str) -> str:
    body = _TASK.match(raw).group("body")
    body = re.sub(r"\s+", " ", body.strip())
    return hashlib.sha256(f"{source}\0{body}".encode()).hexdigest()[:24]


def _unspecified_last(value: str):
    return value == UNSPECIFIED, value
