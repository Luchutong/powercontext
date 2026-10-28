"""MiniMax coding assistant: prompt from the SDD context, call MiniMax, apply and check the returned files.

Transports
* ``live``     official MiniMax OpenAI-compatible Chat Completions API (needs MINIMAX_API_KEY)
* ``hf-space`` MiniMax's official Hugging Face Space ``MiniMaxAI/MiniMax-Text-01`` through gradio_client
* ``replay``   re-apply the responses stored in a recorded session (no network, deterministic)

After every attempt the target and regression tests selected by the context are run; failures are
sent back to MiniMax as the next turn (at most ``max_attempts`` turns). Every turn is recorded.
"""

from __future__ import annotations

import ast
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from sdd_context.config import Settings, Task
from sdd_context.seekdb_store import SeekdbStore
from sdd_context.testrun import run_suite

SESSION_SCHEMA = "minimax-sdd.minimax-session.v1"
DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MODEL = "MiniMax-M3"
DEFAULT_SPACE = "MiniMaxAI/MiniMax-Text-01"
THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S)
FILE_BLOCK = re.compile(r"FILE:\s*`?([\w./-]+?)`?\s*\n+```[A-Za-z0-9_+-]*\n(.*?)\n```", re.S)

SYSTEM_PROMPT = """你是一名资深 Python 工程师，通过“SDD 上下文模块”提供的规格、代码、测试和任务状态完成一次局部代码修改。
规则：
1. 只修改“需要修改的代码”所在的文件；不要修改 tests/ 或 specs/ 下的任何文件，不要新增第三方依赖。
2. 实现全部“需要实现的规格（变更）”，同时保持“必须保持的现有规格”中的行为不变。
3. 保持现有函数签名、异常类型和模块结构；只做完成任务所需的最小修改。
4. 输出格式：对每个需要修改的文件，先单独一行写 `FILE: <相对路径>`，紧接着用 ```python 代码块给出该文件修改后的完整内容。
5. 代码块之后用不超过 5 行说明修改点，并注明对应的规格条目编号。"""


class EditError(RuntimeError):
    pass


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def messages_sha(messages: list[dict[str, str]]) -> str:
    return sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True))


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fence(text: str) -> str:
    return f"```python\n{text.rstrip()}\n```"


def state_summary(plan: dict[str, Any]) -> list[str]:
    """Deterministic text about the PowerContext state (no IDs or timestamps)."""
    lines = []
    done = [entry["step"] for entry in plan.get("done", [])]
    lines.append("- 状态来源：PowerContext（" + ("已恢复" if plan.get("status") == "restored" else "空") + "）")
    lines.append("- 已完成：" + ("、".join(done) if done else "无"))
    for record in plan.get("verification", []):
        suites = record.get("suites") or {}
        parts = []
        for name, suite in suites.items():
            kinds = "，".join(f"{kind} {count}" for kind, count in sorted((suite.get("failure_kinds") or {}).items()))
            parts.append(f"{name} {suite['passed']}/{suite['total']} 通过" + (f"（失败类型：{kinds}）" if kinds else ""))
        lines.append(f"- 已有验证（{record.get('phase')}）：" + "；".join(parts))
    todo = [entry["step"] for entry in plan.get("todo", [])]
    lines.append("- 待办：" + ("、".join(todo) if todo else "无"))
    for memory in plan.get("memories", []):
        if memory["kind"] == "constraint":
            lines.append("- 约束：" + re.sub(r"^\[[^\]]+\]\s*", "", memory["text"]).removeprefix("约束："))
    return lines


def build_messages(store: SeekdbStore, context: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, str]]:
    task = context["task"]
    project = context.get("project") or "taskboard"
    modify = [target for target in context["code_targets"] if target["role"] == "modify"]
    related = [target for target in context["code_targets"] if target["role"] == "related"]
    modify_files = sorted({target["path"] for target in modify})
    target_test_files = sorted({test["path"] for test in context["tests"]["target"]})
    regression_test_files = sorted({test["path"] for test in context["tests"]["regression"]})
    wanted = tuple(modify_files + target_test_files + regression_test_files)
    documents = {
        row["path"]: row["content"]
        for row in store.query(
            "Q10",
            "读取提供给 MiniMax 的文件正文（待修改代码与测试）",
            "SELECT path, kind, sha256, content FROM sdd_document WHERE project = %s AND path IN %s ORDER BY path",
            (project, wanted),
        )
    }
    snippets = {
        row["symbol_id"]: row["source"]
        for row in store.query(
            "Q11",
            "读取相关代码符号的源码片段",
            "SELECT symbol_id, source FROM sdd_code_symbol WHERE project = %s AND symbol_id IN %s ORDER BY symbol_id",
            (project, tuple(t["symbol_id"] for t in related) or ("",)),
        )
    }

    parts = [f"## 任务\n{task['id']} {task['title']}\n\n{task['description']}"]
    for kind, heading in (("change", "需要实现的规格（变更）"), ("preserve", "必须保持的现有规格")):
        parts.append(f"## {heading}")
        for entry in context["specs"][kind]:
            parts.append(f"### {entry['id']} {entry['title']}（rev {entry['revision']}，来源 {entry['source']}）\n{entry['text']}")
    parts.append("## 需要修改的代码")
    for target in modify:
        parts.append(f"- {target['symbol_id']}（L{target['lines'][0]}-L{target['lines'][1]}）：{'；'.join(target['reasons'])}")
    if related:
        parts.append("相关代码（供参考，非必要不改）：")
        for target in related:
            parts.append(f"- {target['symbol_id']}（L{target['lines'][0]}-L{target['lines'][1]}）：{'；'.join(target['reasons'])}")
    for path in modify_files:
        parts.append(f"### 文件 {path}（当前完整内容）\n{_fence(documents[path])}")
    for target in related:
        if target["path"] not in modify_files and target["symbol_id"] in snippets:
            parts.append(f"### 相关片段 {target['symbol_id']}\n{_fence(snippets[target['symbol_id']])}")
    parts.append("## 验证测试")
    parts.append("目标测试（修改后必须全部通过）：\n" + "\n".join(f"- {test['id']}（验证 {', '.join(test['verifies'])}）" for test in context["tests"]["target"]))
    parts.append("回归测试（必须继续通过）：\n" + "\n".join(f"- {test['id']}（验证 {', '.join(test['verifies'])}）" for test in context["tests"]["regression"]))
    for path in target_test_files + regression_test_files:
        parts.append(f"### 测试文件 {path}\n{_fence(documents[path])}")
    parts.append("## 任务状态（来自 PowerContext）\n" + "\n".join(state_summary(plan)))
    parts.append(
        "## 输出要求\n请按系统提示中的格式输出修改后的完整文件："
        + "、".join(modify_files)
        + "。不要输出测试文件或规格文件。"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "\n\n".join(parts)}]


def feedback_message(check: dict[str, Any], modify_files: list[str]) -> str:
    lines = [f"按你上一轮给出的代码运行了上下文中的目标测试与回归测试：{check['passed']}/{check['total']} 通过，以下用例失败："]
    lines += [f"- {failure['nodeid']}：{failure['message']}" for failure in check["failures"]]
    lines.append(
        "请找出失败原因并修正。仍然只修改 " + "、".join(modify_files)
        + "，按 `FILE: <相对路径>` 加 ```python 代码块的格式输出修改后的完整文件；必须同时满足变更规格和必须保持的现有规格。"
    )
    return "\n".join(lines)


def call_live(messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        raise EditError("live 模式需要环境变量 MINIMAX_API_KEY（见 .env.example）")
    base_url = os.environ.get("MINIMAX_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("MINIMAX_MODEL", DEFAULT_MODEL)
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": int(os.environ.get("MINIMAX_MAX_TOKENS", "32768")),
    }
    if os.environ.get("MINIMAX_REASONING_SPLIT", "1") != "0":
        body["reasoning_split"] = True
    response = httpx.post(
        f"{base_url}/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=float(os.environ.get("MINIMAX_TIMEOUT", "600")),
    )
    if response.status_code != 200:
        raise EditError(f"MiniMax API 返回 HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    base_resp = data.get("base_resp") or {}
    if base_resp.get("status_code", 0) != 0:
        raise EditError(f"MiniMax API 错误 {base_resp.get('status_code')}: {base_resp.get('status_msg')}")
    choice = data["choices"][0]
    message = choice.get("message") or {}
    reasoning = message.get("reasoning_content") or ""
    meta = {
        "endpoint": f"{base_url}/chat/completions",
        "model": data.get("model", model),
        "request_params": {key: value for key, value in body.items() if key != "messages"},
        "response_id": data.get("id"),
        "finish_reason": choice.get("finish_reason"),
        "usage": data.get("usage"),
        "reasoning_chars_omitted": len(reasoning),
    }
    return message.get("content") or "", meta


def flatten_messages(messages: list[dict[str, str]]) -> str:
    parts = [messages[0]["content"], messages[1]["content"]]
    for message in messages[2:]:
        title = "## 你上一轮的回复" if message["role"] == "assistant" else "## 新的反馈（请据此修正）"
        parts.append(f"{title}\n{message['content']}")
    return "\n\n".join(parts)


def call_hf_space(messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
    try:
        from gradio_client import Client
    except ImportError as error:
        raise EditError("hf-space 模式需要 gradio_client：SDD_WITH_HF=1 bash scripts/install.sh") from error
    space = os.environ.get("MINIMAX_HF_SPACE", DEFAULT_SPACE)
    params = {"max_tokens": int(os.environ.get("MINIMAX_MAX_TOKENS", "12800")), "temperature": 0.1, "top_p": 0.9}
    client = Client(space, verbose=False)
    text = client.predict(flatten_messages(messages), params["max_tokens"], params["temperature"], params["top_p"], api_name="/chat")
    meta = {
        "endpoint": f"https://huggingface.co/spaces/{space} (gradio api_name=/chat)",
        "model": "MiniMax-Text-01（MiniMax 官方 Space 后端，模型由 Space 配置决定）",
        "request_params": params,
        "note": "Space 的 /chat 只接收单条消息：system、user 及后续轮次按顺序拼接（后续轮次带角色标题）后发送。",
    }
    return text if isinstance(text, str) else json.dumps(text, ensure_ascii=False), meta


def parse_files(text: str) -> list[tuple[str, str]]:
    visible = THINK_BLOCK.sub("", text)
    return [(path.strip(), body + "\n") for path, body in FILE_BLOCK.findall(visible)]


def unified_diff(before: dict[str, str], after: dict[str, str]) -> str:
    chunks = []
    for path in sorted(after):
        if before.get(path) == after[path]:
            continue
        lines = difflib.unified_diff(
            before.get(path, "").splitlines(keepends=True),
            after[path].splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
        chunks.append(f"diff --git a/{path} b/{path}\n" + "".join(lines))
    return "".join(chunks)


def apply_files(project_dir: Path, files: list[tuple[str, str]], allowed: set[str]) -> tuple[list[str], list[dict[str, str]]]:
    """Write the files returned by MiniMax; only code files listed in the context may change."""
    accepted, rejected = {}, []
    for rel, content in files:
        path = (project_dir / rel).resolve()
        if rel not in allowed or not path.is_relative_to(project_dir.resolve()) or rel.startswith(("tests/", "specs/")):
            rejected.append({"path": rel, "reason": "不在允许修改的代码文件内"})
            continue
        try:
            ast.parse(content, filename=rel)
        except SyntaxError as error:
            rejected.append({"path": rel, "reason": f"语法错误: {error}"})
            continue
        accepted[rel] = content
    if not accepted:
        raise EditError(f"MiniMax 回复中没有可应用的文件修改；被拒绝：{rejected}")
    for rel, content in accepted.items():
        (project_dir / rel).write_text(content, encoding="utf-8")
    return sorted(accepted), rejected


def write_snapshots(settings: Settings, before: dict[str, str], after: dict[str, str]) -> dict[str, str]:
    paths = {}
    for label, files in (("before", before), ("after", after)):
        for rel, content in files.items():
            target = settings.minimax_dir / "snapshots" / label / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            paths[f"{label}:{rel}"] = str(target.relative_to(settings.minimax_dir))
    return paths


def new_session_id() -> str:
    return "mmx-" + dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def check_attempt(settings: Settings, context: dict[str, Any], attempt: int, out_dir: Path) -> dict[str, Any]:
    selectors = [test["id"] for test in context["tests"]["target"] + context["tests"]["regression"]]
    summary = run_suite(settings, "target+regression", selectors, f"attempt{attempt}", out_dir=out_dir)
    failures = [
        {"nodeid": case["nodeid"], "kind": case["failure_kind"], "message": (case["message"] or "").splitlines()[0][:200] if case["message"] else ""}
        for case in summary["cases"]
        if case["outcome"] != "passed"
    ]
    return {
        "command": summary["command"],
        "exit_code": summary["exit_code"],
        "total": summary["total"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "errors": summary["errors"],
        "failures": failures,
        "code_digest": summary["code_digest"],
        "log": summary["log"],
    }


def _rel(settings: Settings, path: Path) -> str:
    return str(path.relative_to(settings.minimax_dir)) if path.is_relative_to(settings.minimax_dir) else str(path)


def run_edit(
    settings: Settings,
    task: Task,
    store: SeekdbStore,
    context: dict[str, Any],
    plan: dict[str, Any],
    mode: str,
    session_file: Path | None,
    record: bool,
    max_attempts: int = 3,
) -> dict[str, Any]:
    messages = build_messages(store, context, plan)
    prompt_sha = messages_sha(messages)
    modify_files = sorted({t["path"] for t in context["code_targets"] if t["role"] == "modify"})
    allowed = {t["path"] for t in context["code_targets"]}
    out_dir = settings.evidence_dir / "minimax"
    out_dir.mkdir(parents=True, exist_ok=True)
    originals = {rel: (settings.project_dir / rel).read_text(encoding="utf-8") for rel in sorted(allowed)}

    recorded: dict[str, Any] | None = None
    if mode == "replay":
        path = session_file or out_dir / "session_recorded.json"
        if not path.is_file():
            raise EditError(f"找不到录制的 MiniMax 会话 {path}")
        recorded = json.loads(path.read_text(encoding="utf-8"))
        session_id = recorded["session_id"]
        planned = len(recorded["attempts"])
        attempt_dir = out_dir / "attempts" / "replay"
    elif mode in {"live", "hf-space"}:
        session_id = new_session_id()
        planned = max_attempts
        attempt_dir = out_dir / "attempts" / session_id
    else:
        raise EditError(f"未知模式 {mode}")
    shutil.rmtree(attempt_dir, ignore_errors=True)

    conversation = list(messages)
    attempts: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    for number in range(1, planned + 1):
        feedback = None
        if attempts:
            feedback = feedback_message(attempts[-1]["check"], modify_files)
            conversation += [{"role": "assistant", "content": attempts[-1]["response"]["text"]}, {"role": "user", "content": feedback}]
        started = time.perf_counter()
        if recorded is not None:
            text, call_meta = recorded["attempts"][number - 1]["response"]["text"], {}
        elif mode == "live":
            text, call_meta = call_live(conversation)
        else:
            text, call_meta = call_hf_space(conversation)
        latency = round(time.perf_counter() - started, 2)
        meta = meta or {k: v for k, v in call_meta.items() if k in {"endpoint", "model", "request_params", "note"}}
        visible = THINK_BLOCK.sub("", text).strip() + "\n"
        written, rejected = apply_files(settings.project_dir, parse_files(visible), allowed)
        current = {rel: (settings.project_dir / rel).read_text(encoding="utf-8") for rel in originals}
        changed = {rel: content for rel, content in current.items() if content != originals[rel]}
        diff = unified_diff(originals, changed)
        check = check_attempt(settings, context, number, attempt_dir)
        attempts.append(
            {
                "attempt": number,
                "request_sha256": messages_sha(conversation),
                "feedback": feedback,
                "response": {
                    "text": visible,
                    "sha256": sha256(visible),
                    "reasoning_removed": THINK_BLOCK.search(text) is not None or bool(call_meta.get("reasoning_chars_omitted")),
                    **{k: v for k, v in call_meta.items() if k in {"response_id", "finish_reason", "usage", "reasoning_chars_omitted"}},
                },
                "latency_s": latency,
                "files_written": written,
                "rejected": rejected,
                "diff": diff,
                "diff_sha256": sha256(diff),
                "check": check,
            }
        )
        if check["failed"] == 0 and check["errors"] == 0:
            break

    final = attempts[-1]
    after = {rel: (settings.project_dir / rel).read_text(encoding="utf-8") for rel in originals}
    after = {rel: content for rel, content in after.items() if content != originals[rel]}
    before = {rel: originals[rel] for rel in after}
    snapshots = write_snapshots(settings, before, after)
    (settings.evidence_dir / "code.diff").write_text(final["diff"], encoding="utf-8")
    passed = final["check"]["failed"] == 0 and final["check"]["errors"] == 0
    edit_summary: dict[str, Any] = {
        "mode": mode,
        "session_id": session_id,
        "transport": recorded["transport"] if recorded else mode,
        "model": recorded["model"] if recorded else meta.get("model"),
        "files": sorted(after),
        "attempts": len(attempts),
        "attempt_checks": [f"attempt {a['attempt']}: {a['check']['passed']}/{a['check']['total']} 通过" for a in attempts],
        "final_check_passed": passed,
        "diff_sha256": final["diff_sha256"],
        "prompt_sha256": prompt_sha,
    }

    if recorded is not None:
        rows = []
        for now, then in zip(attempts, recorded["attempts"], strict=False):
            rows.append(
                {
                    "attempt": now["attempt"],
                    "request_matches": now["request_sha256"] == then["request_sha256"],
                    "diff_matches": now["diff_sha256"] == then["diff_sha256"],
                    "check_now": f"{now['check']['passed']}/{now['check']['total']}",
                    "check_recorded": f"{then['check']['passed']}/{then['check']['total']}",
                    "check_matches": (now["check"]["passed"], now["check"]["total"]) == (then["check"]["passed"], then["check"]["total"]),
                }
            )
        replay = {
            "schema": "minimax-sdd.replay-check.v1",
            "checked_at": _now(),
            "session_id": session_id,
            "session_file": _rel(settings, session_file or out_dir / "session_recorded.json"),
            "prompt_sha256_now": prompt_sha,
            "prompt_sha256_recorded": recorded["request"]["prompt_sha256"],
            "prompt_matches_recording": prompt_sha == recorded["request"]["prompt_sha256"] and all(r["request_matches"] for r in rows),
            "diff_sha256_now": final["diff_sha256"],
            "diff_sha256_recorded": recorded["result"]["diff_sha256"],
            "diff_matches_recording": final["diff_sha256"] == recorded["result"]["diff_sha256"],
            "attempts": rows,
            "files": sorted(after),
            "snapshots": snapshots,
        }
        (out_dir / "replay_check.json").write_text(json.dumps(replay, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        edit_summary["replay_check"] = {k: replay[k] for k in ("prompt_matches_recording", "diff_matches_recording")}
    else:
        session = {
            "schema": SESSION_SCHEMA,
            "session_id": session_id,
            "created_at": _now(),
            "transport": mode,
            "model": meta.get("model"),
            "endpoint": meta.get("endpoint"),
            "request_params": meta.get("request_params"),
            "note": meta.get("note"),
            "task": context["task"],
            "context": {
                "change_specs": [e["id"] for e in context["specs"]["change"]],
                "preserve_specs": [e["id"] for e in context["specs"]["preserve"]],
                "code_targets": [{"symbol": t["symbol_id"], "role": t["role"]} for t in context["code_targets"]],
                "target_tests": [t["id"] for t in context["tests"]["target"]],
                "regression_tests": [t["id"] for t in context["tests"]["regression"]],
                "state_status": plan.get("status"),
                "seekdb_queries": [q["id"] for q in context["retrieval"]["queries"]] + ["Q10", "Q11"],
                "import": context["import"],
            },
            "request": {"messages": messages, "prompt_sha256": prompt_sha},
            "attempts": attempts,
            "response": final["response"],
            "result": {
                "files": [
                    {"path": rel, "before_sha256": sha256(before[rel]), "after_sha256": sha256(after[rel])} for rel in sorted(after)
                ],
                "diff": final["diff"],
                "diff_sha256": final["diff_sha256"],
                "snapshots": snapshots,
                "modify_targets": modify_files,
                "final_check_passed": passed,
            },
        }
        sessions = out_dir / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        session_path = sessions / f"{session_id}.json"
        session_path.write_text(json.dumps(session, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        edit_summary["session_file"] = _rel(settings, session_path)
        if record:
            (out_dir / "session_recorded.json").write_text(json.dumps(session, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            from sdd_context.devlog import render_devlog

            (out_dir / "开发记录.md").write_text(render_devlog(session), encoding="utf-8")

    (out_dir / "last_edit.json").write_text(json.dumps(edit_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not passed:
        raise EditError(f"MiniMax 在 {len(attempts)} 轮内没有让目标与回归测试全部通过；记录见 {edit_summary.get('session_file', 'replay_check.json')}")
    return edit_summary
