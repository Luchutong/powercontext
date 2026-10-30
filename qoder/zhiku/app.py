"""Web 服务：提供检索 API 和一个本地网页界面。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .ingest import SUPPORTED, file_chunks, ingest_dir
from .store import Store

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DOCS_DIR = Path(os.environ.get("ZHIKU_DOCS", "./sample_docs")).resolve()

app = FastAPI(title="知库 ZhiKu")
store = Store()


class TodoUpdate(BaseModel):
    done: bool


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/search")
def search(q: str, k: int = 8, tag: str | None = None):
    return {"q": q, "hits": [h.to_dict() for h in store.search(q, k=k, tag=tag or None)]}


@app.get("/api/stats")
def stats():
    return {"docs_dir": str(DOCS_DIR), **store.stats()}


@app.get("/api/todos")
def todos(
    owner: str | None = None,
    status: Literal["all", "open", "done"] = "all",
    due: str | None = None,
):
    return store.todos.query(owner=owner or None, status=status, due=due or None)


@app.patch("/api/todos/{todo_id}")
def update_todo(todo_id: str, update: TodoUpdate):
    todo = store.todos.set_done(todo_id, update.done)
    if todo is None:
        raise HTTPException(404, "待办不存在")
    return todo


@app.post("/api/reindex")
def reindex():
    return ingest_dir(store, DOCS_DIR)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), tag: str = "上传"):
    name = Path(file.filename or "").name
    if Path(name).suffix.lower() not in SUPPORTED:
        raise HTTPException(400, f"只支持 {', '.join(sorted(SUPPORTED))}")
    dest = DOCS_DIR / Path(tag).name / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())
    n = store.replace_source(dest.relative_to(DOCS_DIR).as_posix(), file_chunks(dest, DOCS_DIR))
    return {"source": dest.relative_to(DOCS_DIR).as_posix(), "chunks": n}
