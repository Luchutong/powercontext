"""Task state in PowerContext 1.4.1 (HTTP API through the official Python Client).

Writes: Scope (one per task), Memory (decisions/constraints/notes), content Sources
(verification records and the MiniMax edit record), Work Contract, Handoff
(``handoff_current_work`` preview committed with ``commit_handoff``) and Task Outcome.
Reads after a restart: ``list_scopes`` -> ``continue_handoff(selection="latest")`` ->
``get_source`` for every cited record -> ``list_memory_entries`` / ``search_memory``.
Nothing about task progress is kept in local files.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
import uuid
from typing import Any

from powercontext.client import PowerContextClient
from powercontext.http import (
    CommitHandoffRequest,
    ContinueHandoffRequest,
    CreateScopeRequest,
    CreateSourceRequest,
    CreateWorkContractRequest,
    HandoffCurrentWorkRequest,
    ListMemoryEntriesRequest,
    RecordTaskOutcomeRequest,
    RememberMemoryRequest,
    SearchMemoryRequest,
)

from sdd_context.config import Settings, Task

MARKER = re.compile(r"^\[(DONE|VERIFIED|TODO):([a-z-]+)\]\s*")
VERIFICATION_SCHEMA = "minimax-sdd.verification.v1"
EDIT_SCHEMA = "minimax-sdd.edit.v1"
STEPS = ("ingest", "context", "baseline", "edit", "verify")
STEP_TEXT = {
    "ingest": "导入规格、代码和测试到 seekdb",
    "context": "从 seekdb 查询并生成开发上下文",
    "baseline": "运行修改前的目标测试与回归测试并记录基线",
    "edit": "把上下文交给 MiniMax 修改代码",
    "verify": "运行修改后的目标测试、回归测试和全量测试",
}


def scope_title(task: Task) -> str:
    return f"minimax-sdd · {task.project} · {task.id}"


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _citation(source_id: str) -> dict[str, Any]:
    return {"kind": "source", "source_ref": {"name": "content", "source_id": source_id}}


def _claim(text: str, source_ids: list[str] | None = None) -> dict[str, Any]:
    ids = source_ids or []
    return {"text": text, "basis": "verified" if ids else "declared", "evidence": [_citation(i) for i in ids]}


def _suite_line(label: str, suites: dict[str, Any]) -> str:
    parts = []
    for name in ("target", "regression", "full"):
        suite = suites.get(name)
        if suite:
            parts.append(f"{name} {suite['passed']}/{suite['total']} 通过")
    return f"{label}: " + "，".join(parts)


class TaskState:
    def __init__(self, settings: Settings, task: Task) -> None:
        self.settings = settings
        self.task = task
        self.title = scope_title(task)

    def _client(self) -> PowerContextClient:
        return PowerContextClient(self.settings.pc_url, timeout=60)

    async def _find_scope(self, client: PowerContextClient):
        page = await client.list_scopes(self.title, query_field="title", limit=50)
        for scope in page.items:
            if scope.title == self.title:
                return scope
        return None

    async def _ensure_scope(self, client: PowerContextClient):
        scope = await self._find_scope(client)
        if scope is not None:
            return scope
        return await client.create_scope(
            CreateScopeRequest(
                title=self.title,
                summary=f"{self.task.title}（SDD 上下文模块的任务状态）",
                external_references=[{"kind": "sdd-task", "value": f"{self.task.project}/{self.task.id}"}],
                idempotency_key=f"minimax-sdd:{self.task.project}:{self.task.id}",
            )
        )

    # ------------------------------------------------------------------ writes

    def save(self, phase: str, context: dict[str, Any], verification: dict[str, Any], edit: dict[str, Any] | None) -> dict[str, Any]:
        return asyncio.run(self._save(phase, context, verification, edit))

    async def _save(self, phase: str, context: dict[str, Any], verification: dict[str, Any], edit: dict[str, Any] | None) -> dict[str, Any]:
        async with self._client() as client:
            scope = await self._ensure_scope(client)
            scope_id = scope.scope_id
            stamp = uuid.uuid4().hex[:8]
            written: dict[str, Any] = {"scope_id": scope_id, "scope_title": self.title, "phase": phase, "memories": [], "sources": {}}
            previous = await self._resume_with(client, scope_id)

            spec_ids = {kind: [entry["id"] for entry in context["specs"][kind]] for kind in ("change", "preserve")}
            modify = [t["symbol_id"] for t in context["code_targets"] if t["role"] == "modify"]
            if phase == "baseline":
                memories = [
                    ("decision", f"[{self.task.id}] 上下文选择：变更规格 {', '.join(spec_ids['change'])}；必须保持 {', '.join(spec_ids['preserve'])}；"
                     f"修改目标 {', '.join(modify)}；目标测试 {len(context['tests']['target'])} 个、回归测试 {len(context['tests']['regression'])} 个（seekdb import #{context['import']['import_id']}）。"),
                    ("constraint", f"[{self.task.id}] 约束：只修改 {', '.join(sorted({m.split('::')[0] for m in modify}))}；不得修改 tests/ 与 specs/；"
                     f"必须继续满足 {', '.join(spec_ids['preserve'])}（含未知查询参数被忽略）。"),
                ]
            else:
                memories = [
                    ("working_note", f"[{self.task.id}] MiniMax 修改已验证：{edit['session_id'] if edit else '-'} 修改 {', '.join(edit['files']) if edit else '-'}；"
                     f"{_suite_line('修改后', verification['suites'])}。"),
                ]
            for kind, text in memories:
                result = await client.remember_memory(RememberMemoryRequest(scope_id=scope_id, kind=kind, text=text, reason=f"sdd save-state {phase}"))
                written["memories"].append({"kind": kind, "text": text, "artifact": result.records[0].artifact.model_dump(mode="json")})

            record = dict(verification, schema=VERIFICATION_SCHEMA, task_id=self.task.id, phase=phase, recorded_at=_now(),
                          spec_items={e["id"]: e["sha256"] for kind in ("change", "preserve") for e in context["specs"][kind]})
            source = await client.create_source(scope_id, CreateSourceRequest(content=record))
            written["sources"]["verification"] = source.source_id

            edit_source_id = None
            written["edit_session"] = edit["session_id"] if edit else None
            if edit is not None:
                edit_record = dict(edit, schema=EDIT_SCHEMA, task_id=self.task.id, recorded_at=_now())
                edit_source = await client.create_source(scope_id, CreateSourceRequest(content=edit_record))
                edit_source_id = edit_source.source_id
                written["sources"]["edit"] = edit_source_id

            if phase == "baseline":
                contract = await client.create_work_contract(
                    CreateWorkContractRequest.model_validate(
                        {
                            "scope_id": scope_id,
                            "source_id": f"contract-{self.task.id.lower()}-{stamp}",
                            "contract": {
                                "schema": "powercontext.work-contract.v1",
                                "trust": "untrusted_input",
                                "objective": f"{self.task.id} {self.task.title}",
                                "facts": [],
                                "in_scope": [f"修改 {symbol}" for symbol in modify],
                                "exclusions": ["修改 tests/ 或 specs/", "修改样例工程以外的文件"],
                                "completion_criteria": [
                                    "目标测试（" + ", ".join(spec_ids["change"]) + "）全部通过",
                                    "回归测试（" + ", ".join(spec_ids["preserve"]) + "）与全量测试全部通过",
                                ],
                                "authorization_notes": ["只在 .runtime/workspace 中的工作副本上修改"],
                                "open_questions": [],
                            },
                        }
                    )
                )
                written["sources"]["work_contract"] = contract.source.source_id
                state = [
                    _claim(f"[DONE:ingest] {STEP_TEXT['ingest']}（import #{context['import']['import_id']}，spec_digest {context['import']['spec_digest'][:12]}）"),
                    _claim(f"[DONE:context] 选出规格 {', '.join(spec_ids['change'] + spec_ids['preserve'])}；修改目标 {', '.join(modify)}"),
                    _claim(f"[VERIFIED:baseline] {_suite_line('修改前', verification['suites'])}；目标测试失败均为断言失败", [source.source_id]),
                ]
                handoff = {
                    "state": state,
                    "disposition": "continuable",
                    "next_action": _claim(f"[TODO:edit] {STEP_TEXT['edit']}（{', '.join(modify)}），随后运行目标与回归测试"),
                    "omissions": ["尚未修改代码，也没有修改后的测试结果。"],
                }
            else:
                baseline_source = previous.get("step_sources", {}).get("baseline", [None])[0]
                state = [
                    _claim(f"[DONE:ingest] {STEP_TEXT['ingest']}（import #{context['import']['import_id']}）"),
                    _claim(f"[DONE:context] 选出规格 {', '.join(spec_ids['change'] + spec_ids['preserve'])}；修改目标 {', '.join(modify)}"),
                ]
                if baseline_source:
                    state.append(_claim(f"[VERIFIED:baseline] {previous['baseline_line']}", [baseline_source]))
                if edit_source_id:
                    state.append(_claim(f"[DONE:edit] MiniMax 会话 {edit['session_id']}（{edit['transport']} / {edit['model']}）修改 {', '.join(edit['files'])}，diff sha256 {edit['diff_sha256'][:12]}", [edit_source_id]))
                state.append(_claim(f"[VERIFIED:verify] {_suite_line('修改后', verification['suites'])}", [source.source_id]))
                handoff = {"state": state, "disposition": "complete", "next_action": None, "omissions": []}

            prepared = await client.handoff_current_work(
                HandoffCurrentWorkRequest.model_validate(
                    {
                        "scope_id": scope_id,
                        "source_id": f"handoff-{phase}-{self.task.id.lower()}-{stamp}",
                        "handoff": {
                            "schema": "powercontext.current-work-handoff.v1",
                            "trust": "untrusted_input",
                            "objective": f"{self.task.id} {self.task.title}",
                            **handoff,
                        },
                    }
                )
            )
            committed = await client.commit_handoff(CommitHandoffRequest(scope_id=scope_id, handoff=prepared.handoff))
            written["handoff"] = committed.reference.model_dump(mode="json")

            if phase == "verified":
                passed = all(s["failed"] == 0 and s["errors"] == 0 for s in verification["suites"].values())
                outcome = await client.record_task_outcome(
                    RecordTaskOutcomeRequest.model_validate(
                        {
                            "scope_id": scope_id,
                            "source_id": f"outcome-{self.task.id.lower()}-{stamp}",
                            "outcome": {
                                "schema": "powercontext.task-outcome.v1",
                                "trust": "untrusted_observation",
                                "objective": f"{self.task.id} {self.task.title}",
                                "status": "succeeded" if passed else "failed",
                                "summary": _suite_line("修改后", verification["suites"]),
                                "observations": [_claim(_suite_line("修改后", verification["suites"]), [source.source_id])],
                                "checks": [
                                    {
                                        "name": f"{name} tests after MiniMax edit",
                                        "status": "passed" if suite["failed"] == 0 and suite["errors"] == 0 else "failed",
                                        "details": f"{suite['passed']}/{suite['total']} passed",
                                        "basis": "verified",
                                        "evidence": [_citation(source.source_id)],
                                    }
                                    for name, suite in verification["suites"].items()
                                ],
                                "produced_artifacts": [committed.reference.model_dump(mode="json")],
                                "remaining_work": [],
                            },
                        }
                    )
                )
                written["sources"]["task_outcome"] = outcome.source.source_id
            return written

    # ------------------------------------------------------------------- reads

    def resume(self) -> dict[str, Any]:
        return asyncio.run(self._resume())

    async def _resume(self) -> dict[str, Any]:
        async with self._client() as client:
            scope = await self._find_scope(client)
            if scope is None:
                return {"status": "empty", "source": "powercontext", "server": self.settings.pc_url, "scope_title": self.title,
                        "done": [], "todo": [], "verification": [], "memories": []}
            return await self._resume_with(client, scope.scope_id)

    async def _resume_with(self, client: PowerContextClient, scope_id: str) -> dict[str, Any]:
        resolution = await client.continue_handoff(ContinueHandoffRequest(scope_id=scope_id, selection="latest"))
        entries = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=scope_id, limit=100))
        found = await client.search_memory(SearchMemoryRequest(scope_id=scope_id, query=self.task.id, mode="fts", limit=20))
        memories = [
            {"kind": entry.kind, "text": entry.text, "artifact": entry.artifact.model_dump(mode="json")}
            for entry in entries.entries
        ]
        result: dict[str, Any] = {
            "status": "empty",
            "source": "powercontext",
            "server": self.settings.pc_url,
            "scope_id": scope_id,
            "scope_title": self.title,
            "memories": memories,
            "memory_search": {"query": self.task.id, "mode": found.mode, "hits": len(found.hits)},
            "done": [],
            "todo": [],
            "verification": [],
            "step_sources": {},
        }
        if resolution.content is None:
            return result
        content = resolution.content.model_dump(mode="json", by_alias=True)
        result["status"] = "restored"
        result["handoff"] = {
            "reference": resolution.selected_revision.model_dump(mode="json") if resolution.selected_revision else None,
            "resolution": str(resolution.status),
            "disposition": content["disposition"],
            "objective": content["objective"],
        }
        cited: list[str] = []
        for statement in content["state"]:
            match = MARKER.match(statement["text"])
            sources = [c["source_ref"]["source_id"] for c in statement["citations"] if c["kind"] == "source"]
            if not match:
                continue
            kind, step = match.groups()
            text = statement["text"][match.end():]
            result["done"].append({"step": step, "kind": kind, "text": text})
            evidence = [s for s in sources if not s.startswith("handoff-")]
            if evidence:
                result["step_sources"][step] = evidence
                cited.extend(evidence)
            if step == "baseline":
                result["baseline_line"] = text
        if content.get("next_action"):
            match = MARKER.match(content["next_action"]["text"])
            if match:
                result["todo"].append({"step": match.group(2), "text": content["next_action"]["text"][match.end():]})
        result["omissions"] = [item["text"] for item in content.get("omissions", [])]
        for source_id in dict.fromkeys(cited):
            record = await client.get_source(scope_id, "content", source_id)
            data = record.content if isinstance(record.content, dict) else {}
            if data.get("schema") == VERIFICATION_SCHEMA:
                result["verification"].append(
                    {
                        "source_id": source_id,
                        "phase": data.get("phase"),
                        "label": data.get("label"),
                        "recorded_at": data.get("recorded_at"),
                        "code_digest": data.get("code_digest"),
                        "git_commit": data.get("git_commit"),
                        "suites": data.get("suites"),
                        "spec_items": data.get("spec_items", {}),
                        "logs": data.get("logs", {}),
                    }
                )
            elif data.get("schema") == EDIT_SCHEMA:
                result["edit"] = {key: data.get(key) for key in ("session_id", "transport", "model", "files", "diff_sha256", "mode", "recorded_at")}
                result["edit"]["source_id"] = source_id
        return result


def plan(state: dict[str, Any], context: dict[str, Any], code_digest: str) -> dict[str, Any]:
    """Derive done/todo/verification for the context output from the PowerContext state."""
    done_steps = {entry["step"] for entry in state.get("done", [])}
    current = {entry["id"]: entry for kind in ("change", "preserve") for entry in context["specs"][kind]}
    verification = []
    for record in state.get("verification", []):
        recorded = record.get("spec_items", {})
        changed = sorted(item for item, sha in recorded.items() if item in current and current[item]["sha256"] != sha)
        added = sorted(item for item in current if item not in recorded)
        verification.append(
            dict(record, spec_changed_since=changed, spec_added_since=added,
                 code_matches_current=record.get("code_digest") == code_digest,
                 still_valid=not changed and not added and record.get("code_digest") == code_digest)
        )
    todo: list[dict[str, str]] = []
    for step in STEPS[2:]:
        if step not in done_steps:
            todo.append({"step": step, "text": STEP_TEXT[step], "origin": "workflow"})
    latest = next((record for record in reversed(verification) if record.get("phase") == "verified"), None)
    if latest is not None:
        for item in latest["spec_changed_since"]:
            todo.append({"step": "respec", "text": f"{item} 在验证之后被修改（当前 rev {current[item]['revision']}），需按新规格修改代码并重新验证", "origin": "spec-change"})
        for item in latest["spec_added_since"]:
            todo.append({"step": "respec", "text": f"{item} 是验证之后新增的规格条目，需补充实现与测试", "origin": "spec-change"})
    for item in context.get("coverage_gaps", []):
        todo.append({"step": "tests", "text": f"{item} 没有覆盖测试", "origin": "coverage"})
    return {
        "status": state.get("status", "empty"),
        "source": "powercontext",
        "scope_id": state.get("scope_id"),
        "handoff": state.get("handoff"),
        "done": state.get("done", []),
        "todo": todo,
        "verification": verification,
        "edit": state.get("edit"),
        "memories": state.get("memories", []),
        "next_step": todo[0]["step"] if todo else None,
    }
