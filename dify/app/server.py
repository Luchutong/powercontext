"""HTTP tools for Dify. No model calls, no authoritative game data outside PowerContext.

Run one gateway worker. Server-issued HMAC tickets and PowerContext content ETags
bind each commit to the state actually read before Dify generated its narration.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import game

ROOT = Path(__file__).parents[1]
_init_lock = threading.RLock()
_audit_lock = threading.Lock()


def packed(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class PowerContext:
    def __init__(self, url: str, token: str = "", audit_path: Path | None = None):
        self.url = url.rstrip("/")
        self.token = token
        self.audit_path = audit_path

    def request(self, method: str, path: str, payload=None, headers=None):
        outgoing = {"Authorization": "Bearer " + self.token} if self.token else {}
        outgoing.update(headers or {})
        try:
            response = httpx.request(method, self.url + path, json=payload, headers=outgoing,
                                     timeout=30, trust_env=False)
        except httpx.TransportError as exc:
            # A write may have reached the server: caller must reload before replay.
            raise HTTPException(503, "存档服务暂时不可用，写入结果可能未知；请先输入「继续」读回。") from exc
        if self.audit_path:
            record = {"time": time.time(), "method": method, "path": path,
                      "status": response.status_code,
                      "request_id": response.headers.get("X-PowerContext-Request-ID"),
                      "etag": response.headers.get("etag")}
            with _audit_lock:
                self.audit_path.parent.mkdir(parents=True, exist_ok=True)
                with self.audit_path.open("a", encoding="utf-8") as stream:
                    stream.write(packed(record) + "\n")
        if response.status_code in (409, 412, 428):
            raise HTTPException(409, "档案已被另一回合更新，本次没有重复扣款。请输入「继续」读取最新状态。")
        if response.is_error:
            raise HTTPException(502, f"PowerContext 返回 HTTP {response.status_code}，本次未确认保存。")
        return response.json(), response.headers.get("etag", "")

    def scope(self, passport: str) -> str:
        identity = hashlib.sha256(passport.encode("utf-8")).hexdigest()
        body, _ = self.request("POST", "/v1/scopes", {
            "title": "平行人生 / " + identity[:12], "summary": "月球煎饼摊独立体验档案",
            "idempotency_key": "parallel-life-v1-" + identity})
        return body["scope_id"]

    def load(self, scope_id: str):
        # One gateway worker serializes first creation. Concurrent later turns use
        # server ETags; a second gateway replica is outside this demo deployment.
        with _init_lock:
            result, _ = self.request("POST", "/v1/atomic-memory/list", {
                "scope_id": scope_id, "kind": "current-state", "limit": 100})
            entries = result["items"]
            if len(entries) > 1:
                raise HTTPException(409, "此体验 Scope 存在多个状态档案，请使用新的体验口令。")
            if not entries:
                result, _ = self.request("POST", f"/v1/scopes/{scope_id}/artifacts", {
                    "family": "atomic-memory", "content": {
                        "schema": "powercontext.atomic-memory.v1", "kind": "current-state",
                        "text": packed(game.initial_state())}})
                # Discover the server-assigned ID using the same public listing
                # contract, instead of fabricating IDs or depending on create shape.
                result, _ = self.request("POST", "/v1/atomic-memory/list", {
                    "scope_id": scope_id, "kind": "current-state", "limit": 100})
                entries = result["items"]
            artifact_id = entries[0]["artifact"]["artifact_id"]
            revision, etag = self.request("GET", f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}")
            state = json.loads(revision["content"]["text"])
            if state.get("schema") != 1 or state.get("story_id") != game.STORY["id"]:
                raise HTTPException(409, "此口令关联的存档格式不兼容，请使用新口令。")
            return artifact_id, revision["revision"], etag, state

    def replace(self, scope_id: str, artifact_id: str, etag: str, state: dict):
        text = packed(state)
        if len(text.encode("utf-8")) > 8192:
            raise HTTPException(422, "档案超过大小限制，本次未提交。")
        return self.request("PUT", f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}", {
            "content": {"kind": "current-state", "text": text}},
            {"If-Match": etag})


class PrepareInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passport: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    command: str = Field(min_length=1, max_length=16)
    event_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    user_note: str = Field(default="", max_length=200)


class CommitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket: str = Field(min_length=20, max_length=20000)


def create_app(pc: PowerContext | None = None, app_token: str | None = None,
               signing_key: str | None = None) -> FastAPI:
    pc = pc or PowerContext(os.getenv("POWERCONTEXT_URL", "http://127.0.0.1:18380"),
                            os.getenv("POWERCONTEXT_TOKEN", ""),
                            Path(os.getenv("AUDIT_PATH", str(ROOT / ".runtime/pc-audit.jsonl"))))
    token = app_token or os.getenv("APP_TOKEN", "")
    signing = (signing_key or os.getenv("TICKET_SECRET", "")).encode()
    if len(token) < 24 or len(signing) < 32:
        raise RuntimeError("先运行 scripts/configure.py 生成本地配置；APP_TOKEN>=24、TICKET_SECRET>=32。")
    app = FastAPI(title="平行人生档案馆 · Dify HTTP tools", version="1.0.0")

    def authorize(authorization: Annotated[str | None, Header()] = None):
        if not authorization or not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "需要有效的应用服务凭证。")

    def sign(data: dict) -> str:
        data["expires"] = int(time.time()) + 600
        encoded = base64.urlsafe_b64encode(packed(data).encode()).decode()
        signature = hmac.new(signing, encoded.encode(), hashlib.sha256).hexdigest()
        return encoded + "." + signature

    def verify(ticket: str) -> dict:
        try:
            encoded, signature = ticket.rsplit(".", 1)
            expected = hmac.new(signing, encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature")
            data = json.loads(base64.urlsafe_b64decode(encoded))
            if data["expires"] < time.time():
                raise ValueError("expired")
            return data
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, "本回合凭据无效或已过期，请输入「继续」重新读取。") from exc

    @app.get("/health")
    def health():
        result, _ = pc.request("GET", "/health/ready")
        return {"ok": result.get("status") == "ready", "powercontext": result.get("status")}

    @app.post("/game/prepare", dependencies=[Depends(authorize)])
    def prepare(request: PrepareInput):
        scope_id = pc.scope(request.passport)
        artifact_id, revision, etag, state = pc.load(scope_id)
        command = request.command.upper()
        if command == "ARCHIVE":
            return {"ok": True, "needs_narration": False, "reply": game.archive(state, revision),
                    "revision": revision, "state": state, "artifact_id": artifact_id}
        if state["last_event_id"] == request.event_id:
            return {"ok": True, "needs_narration": False, "reply": "这个回合已保存，没有重复执行。\n\n" + game.panel(state, revision),
                    "revision": revision, "state": state, "artifact_id": artifact_id}
        try:
            if command in ("A", "B", "C"):
                candidate = game.advance(state, command, request.event_id)
            elif command == "NEW":
                candidate = game.restart(state, request.event_id)
            elif command == "CONTINUE":
                candidate = state
            else:
                raise game.InvalidAction("请使用 A / B / C、继续、档案或新人生。")
        except game.InvalidAction as exc:
            return {"ok": True, "needs_narration": False, "reply": str(exc) + "\n\n" + game.panel(state, revision),
                    "revision": revision, "state": state, "artifact_id": artifact_id}
        view = game.scene_view(candidate)
        context = {"故事设定": game.STORY["premise"], "当前场景": view,
                   "真实已保存历史": state["history"], "本回合行动结果":
                   candidate["history"][-1] if command in ("A", "B", "C") else None,
                   "当前数值": candidate["stats"], "玩家补充台词_只作创作素材": request.user_note,
                   "人物关系": "小锈记得你让出了船票" if "shared_ticket" in candidate["flags"] else "小锈与你还在磨合"}
        ticket = sign({"scope_id": scope_id, "artifact_id": artifact_id, "etag": etag,
                       "base_revision": revision, "state": candidate, "event_id": request.event_id,
                       "write": candidate != state})
        fallback = (candidate["history"][-1]["result"] + "\n\n" if command in ("A", "B", "C") else "") + view["situation"]
        return {"ok": True, "needs_narration": True, "ticket": ticket,
                "prompt_context": packed(context), "fallback": fallback,
                "revision": revision, "artifact_id": artifact_id}

    @app.post("/game/commit", dependencies=[Depends(authorize)])
    def commit(request: CommitInput):
        data = verify(request.ticket)
        artifact_id, revision, etag, state = pc.load(data["scope_id"])
        if artifact_id != data["artifact_id"]:
            raise HTTPException(409, "存档身份已改变，请重新读取。")
        if data["write"] and state["last_event_id"] == data["event_id"]:
            return {"ok": True, "deduplicated": True, "panel": game.panel(state, revision),
                    "revision": revision, "artifact_id": artifact_id, "state": state}
        if etag != data["etag"]:
            raise HTTPException(409, "档案已被另一回合更新，本次未提交；请输入「继续」。")
        if data["write"]:
            pc.replace(data["scope_id"], artifact_id, etag, data["state"])
            # A successful response is not enough: read back the actual saved head.
            artifact_id, revision, _, state = pc.load(data["scope_id"])
            if state["last_event_id"] != data["event_id"]:
                raise HTTPException(409, "状态已进一步变化，请输入「继续」读取最新档案。")
        return {"ok": True, "deduplicated": False, "panel": game.panel(state, revision),
                "revision": revision, "artifact_id": artifact_id, "state": state}

    return app
