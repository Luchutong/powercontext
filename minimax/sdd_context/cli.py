"""Command line entry point: ``./sdd <command>`` or ``python -m sdd_context <command>``."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

from sdd_context import __version__, pc_server
from sdd_context.config import Settings, load_settings, load_task
from sdd_context.ingest import collect_project, digest_files, ingest_project
from sdd_context.render import render_context
from sdd_context.seekdb_store import SeekdbStore
from sdd_context.selector import ContextError, select_context, verify_sources
from sdd_context.state import TaskState, plan
from sdd_context.testrun import meets_expectation, run_suite

SUITES = ("target", "regression", "full")
_SAVE_PATH: Path | None = None


def emit(payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    print(text)
    if _SAVE_PATH is not None:
        _SAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SAVE_PATH.write_text(text + "\n", encoding="utf-8")


def note(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _rel(settings: Settings, path: Path) -> str:
    return str(path.relative_to(settings.minimax_dir)) if path.is_relative_to(settings.minimax_dir) else str(path)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _require_server(settings: Settings) -> None:
    if not pc_server.is_live(settings):
        raise SystemExit(f"PowerContext Server 未运行（{settings.pc_url}）；请先运行 `./sdd server start` 或 `./sdd init`")


def _current_code_digest(settings: Settings) -> str:
    return digest_files(collect_project(settings.project_dir).code)


def build_context(settings: Settings, task_path: Path | None, actor: str, with_state: bool = True) -> dict[str, Any]:
    task = load_task(task_path or settings.task_file)
    with SeekdbStore(settings, actor=actor) as store:
        context = select_context(store, task)
    context["project"] = task.project
    context["project_dir"] = str(settings.project_dir)
    context["source_check"] = verify_sources(context, settings.project_dir)
    if with_state:
        _require_server(settings)
        raw = TaskState(settings, task).resume()
        context["state"] = plan(raw, context, _current_code_digest(settings))
    return context


def cmd_init(settings: Settings, args: argparse.Namespace) -> int:
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    workspace = settings.project_dir
    if workspace.exists() and not args.keep_workspace:
        shutil.rmtree(workspace)
    if not workspace.exists():
        shutil.copytree(settings.template_dir, workspace, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    settings.evidence_dir.mkdir(parents=True, exist_ok=True)
    settings.queries_log.write_text("", encoding="utf-8")
    with SeekdbStore(settings, actor="init") as store:
        store.ensure_schema()
        engine = store.version
    pc_server.write_env(settings)
    server = pc_server.start(settings)
    emit(
        {
            "command": "init",
            "module": f"sdd_context {__version__}",
            "python": platform.python_version(),
            "packages": {name: metadata.version(name) for name in ("powercontext", "pylibseekdb", "PyMySQL", "pytest", "httpx")},
            "workspace": str(workspace),
            "seekdb": {"engine": engine, "mode": settings.seekdb_mode, "path": str(settings.seekdb_dir), "database": settings.seekdb_database},
            "powercontext": server,
        }
    )
    return 0


def cmd_ingest(settings: Settings, args: argparse.Namespace) -> int:
    task = load_task(args.task or settings.task_file)
    with SeekdbStore(settings, actor="ingest") as store:
        store.ensure_schema()
        summary = ingest_project(store, task.project, settings.project_dir)
        summary["engine"] = store.version
    emit(dict(command="ingest", **summary))
    return 0


def cmd_context(settings: Settings, args: argparse.Namespace) -> int:
    context = build_context(settings, args.task, actor="context")
    if args.out:
        _write_json(Path(args.out), context)
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(render_context(context), encoding="utf-8")
    if args.full:
        emit(context)
        return 0
    emit(
        {
            "command": "context",
            "task": context["task"]["id"],
            "import_id": context["import"]["import_id"],
            "specs": {kind: [f"{e['id']}@rev{e['revision']} {e['source']}" for e in entries] for kind, entries in context["specs"].items()},
            "code_targets": [f"{t['role']}: {t['symbol_id']} {t['source']}" for t in context["code_targets"]],
            "tests": {group: [t["id"] for t in context["tests"][group]] for group in ("target", "regression")},
            "state": {key: context["state"][key] for key in ("status", "scope_id", "handoff", "done", "todo", "next_step")},
            "verification": [
                {k: r.get(k) for k in ("phase", "recorded_at", "still_valid", "spec_changed_since", "spec_added_since")}
                | {"suites": {n: f"{s['passed']}/{s['total']}" for n, s in (r.get("suites") or {}).items()}}
                for r in context["state"]["verification"]
            ],
            "coverage_gaps": context["coverage_gaps"],
            "seekdb_queries": [f"{q['id']} rows={q['rows']}" for q in context["retrieval"]["queries"]],
            "out": args.out,
        }
    )
    return 0


def cmd_test(settings: Settings, args: argparse.Namespace) -> int:
    if args.suite == "full":
        selectors = ["tests"]
    else:
        context = build_context(settings, args.task, actor=f"test:{args.suite}", with_state=False)
        selectors = [test["id"] for test in context["tests"][args.suite]]
        if not selectors:
            raise SystemExit(f"上下文中没有 {args.suite} 测试")
    summary = run_suite(settings, args.suite, selectors, args.label)
    ok, reason = meets_expectation(summary, args.expect) if args.expect else (summary["exit_code"] == 0, "")
    emit(
        {
            "command": "test",
            "suite": args.suite,
            "label": args.label,
            "pytest_exit_code": summary["exit_code"],
            "result": {k: summary[k] for k in ("total", "passed", "failed", "errors", "skipped", "failure_kinds")},
            "expect": args.expect,
            "meets_expectation": ok,
            "reason": reason,
            "code_digest": summary["code_digest"],
            "git_commit": summary["git_commit"],
            "log": summary["log"],
        }
    )
    return 0 if ok else 1


def _suite_records(settings: Settings, label: str, suites: tuple[str, ...]) -> dict[str, Any]:
    records, logs = {}, {}
    for suite in suites:
        path = settings.evidence_dir / "tests" / f"{label}-{suite}.json"
        if not path.is_file():
            raise SystemExit(f"缺少 {_rel(settings, path)}；请先运行 `./sdd test --suite {suite} --label {label}`")
        data = json.loads(path.read_text(encoding="utf-8"))
        records[suite] = {k: data[k] for k in ("total", "passed", "failed", "errors", "skipped", "failure_kinds", "exit_code", "command")}
        logs[suite] = data["log"]
        version = {k: data[k] for k in ("git_commit", "git_dirty", "code_digest", "tests_digest")}
    return {"label": label, "suites": records, "logs": logs, **version}


def cmd_save_state(settings: Settings, args: argparse.Namespace) -> int:
    context = build_context(settings, args.task, actor=f"save-state:{args.phase}", with_state=False)
    _require_server(settings)
    task = load_task(args.task or settings.task_file)
    if args.phase == "baseline":
        verification = _suite_records(settings, "before", ("target", "regression"))
        edit = None
    else:
        verification = _suite_records(settings, "after", SUITES)
        edit_path = settings.evidence_dir / "minimax" / "last_edit.json"
        if not edit_path.is_file():
            raise SystemExit("缺少 evidence/minimax/last_edit.json；请先运行 `./sdd edit`")
        edit = json.loads(edit_path.read_text(encoding="utf-8"))
    written = TaskState(settings, task).save(args.phase, context, verification, edit)
    log_path = settings.evidence_dir / "state" / f"save-{args.phase}.json"
    _write_json(log_path, written)
    emit(dict(command="save-state", server=settings.pc_url, server_pid=pc_server.read_pid(settings), log=_rel(settings, log_path), **written))
    return 0


def cmd_resume(settings: Settings, args: argparse.Namespace) -> int:
    _require_server(settings)
    task = load_task(args.task or settings.task_file)
    raw = TaskState(settings, task).resume()
    payload: dict[str, Any] = {
        "command": "resume",
        "server": settings.pc_url,
        "server_pid": pc_server.read_pid(settings),
        "process_pid": os.getpid(),
        "state": {k: v for k, v in raw.items() if k != "step_sources"},
    }
    try:
        context = build_context(settings, args.task, actor="resume", with_state=False)
        planned = plan(raw, context, _current_code_digest(settings))
        payload["next_step"] = planned["next_step"]
        payload["todo"] = planned["todo"]
    except ContextError as error:
        payload["next_step"] = None
        payload["note"] = str(error)
    if args.out:
        _write_json(Path(args.out), payload)
    emit(payload)
    return 0


def cmd_edit(settings: Settings, args: argparse.Namespace) -> int:
    from sdd_context.minimax import EditError, run_edit

    mode = args.mode
    if mode == "auto":
        mode = "live" if os.environ.get("MINIMAX_API_KEY", "").strip() else "replay"
    task = load_task(args.task or settings.task_file)
    context = build_context(settings, args.task, actor=f"edit:{mode}")
    state = context["state"]
    latest = next((r for r in reversed(state["verification"]) if r.get("phase") == "verified"), None)
    if latest and latest["still_valid"] and not args.force:
        emit(
            {
                "command": "edit",
                "skipped": True,
                "reason": "PowerContext 记录显示该任务已由 MiniMax 修改并通过验证，且规格与代码未变化；使用 --force 重新修改",
                "edit": state.get("edit"),
                "verified_at": latest.get("recorded_at"),
            }
        )
        return 0
    try:
        with SeekdbStore(settings, actor=f"edit:{mode}") as store:
            summary = run_edit(
                settings, task, store, context, state, mode, Path(args.session) if args.session else None, args.record, args.max_attempts
            )
    except EditError as error:
        note(f"edit 失败：{error}")
        return 1
    emit(dict(command="edit", skipped=False, **summary))
    return 0


def cmd_devlog(settings: Settings, args: argparse.Namespace) -> int:
    from sdd_context.devlog import render_devlog

    session_path = Path(args.session) if args.session else settings.evidence_dir / "minimax" / "session_recorded.json"
    target = Path(args.out) if args.out else session_path.parent / "开发记录.md"
    recorded = session_path.name == "session_recorded.json"
    link = os.path.relpath(session_path, target.parent)
    target.write_text(render_devlog(json.loads(session_path.read_text(encoding="utf-8")), link, recorded), encoding="utf-8")
    emit({"command": "devlog", "session": _rel(settings, session_path), "written": _rel(settings, target)})
    return 0


def cmd_server(settings: Settings, args: argparse.Namespace) -> int:
    if args.action == "start":
        result = pc_server.start(settings)
    elif args.action == "stop":
        result = pc_server.stop(settings)
    elif args.action == "restart":
        result = {"stop": pc_server.stop(settings), "start": pc_server.start(settings)}
    else:
        result = pc_server.status(settings)
    emit(dict(command=f"server {args.action}", **result))
    return 0


def cmd_spec_update(settings: Settings, args: argparse.Namespace) -> int:
    specs = settings.project_dir / "specs"
    if args.restore:
        source = settings.template_dir / "specs"
        for path in specs.glob("*.md"):
            path.unlink()
    else:
        source = settings.minimax_dir / "sample_task" / "spec_updates" / args.version
    if not source.is_dir():
        raise SystemExit(f"找不到规格目录 {source}")
    copied = []
    for path in sorted(source.glob("*.md")):
        shutil.copy2(path, specs / path.name)
        copied.append(path.name)
    emit({"command": "spec-update", "from": _rel(settings, source), "to": str(specs), "files": copied})
    return 0


def cmd_reset(settings: Settings, args: argparse.Namespace) -> int:
    stopped = pc_server.stop(settings) if settings.runtime_dir.exists() else None
    if settings.runtime_dir.exists():
        shutil.rmtree(settings.runtime_dir)
    emit({"command": "reset", "removed": str(settings.runtime_dir), "server": stopped})
    return 0


def cmd_accept(settings: Settings, args: argparse.Namespace) -> int:
    from sdd_context.acceptance import run_acceptance

    report = run_acceptance(settings)
    return 0 if report["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sdd", description="SDD 上下文模块（seekdb + PowerContext + MiniMax）")
    parser.add_argument("--task", type=Path, help="任务定义文件（默认 sample_task/task.toml）")
    parser.add_argument("--save", type=Path, help="同时把命令输出的 JSON 写入该文件")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="准备工作副本、seekdb 库表，并启动 PowerContext Server")
    init.add_argument("--keep-workspace", action="store_true")
    commands.add_parser("ingest", help="导入（或重新导入）规格、代码、测试到 seekdb")
    context = commands.add_parser("context", help="查询 seekdb + PowerContext，输出开发上下文")
    context.add_argument("--out", help="写出完整 JSON")
    context.add_argument("--md", help="写出 Markdown 视图")
    context.add_argument("--full", action="store_true", help="标准输出打印完整 JSON")
    test = commands.add_parser("test", help="运行上下文选出的目标/回归测试或全量测试")
    test.add_argument("--suite", choices=SUITES, required=True)
    test.add_argument("--label", choices=("before", "after"), required=True)
    test.add_argument("--expect", choices=("pass", "fail"))
    save = commands.add_parser("save-state", help="把进展与验证结果写入 PowerContext")
    save.add_argument("--phase", choices=("baseline", "verified"), required=True)
    resume = commands.add_parser("resume", help="从 PowerContext 读取任务状态")
    resume.add_argument("--out")
    edit = commands.add_parser("edit", help="把上下文交给 MiniMax 修改代码")
    edit.add_argument("--mode", choices=("auto", "live", "hf-space", "replay"), default="auto")
    edit.add_argument("--session", help="replay 使用的会话文件（默认 evidence/minimax/session_recorded.json）")
    edit.add_argument("--record", action="store_true", help="把本次 live/hf-space 会话保存为回放用的录制文件")
    edit.add_argument("--force", action="store_true", help="即使 PowerContext 显示已完成也重新修改")
    edit.add_argument("--max-attempts", type=int, default=3, help="live/hf-space 模式下最多与 MiniMax 往返的轮次")
    devlog = commands.add_parser("devlog", help="根据录制的会话重新生成 evidence/minimax/开发记录.md")
    devlog.add_argument("--session")
    devlog.add_argument("--out", help="输出文件（默认与会话同目录的 开发记录.md）")
    server = commands.add_parser("server", help="管理本地 PowerContext Server")
    server.add_argument("action", choices=("start", "stop", "restart", "status"))
    spec = commands.add_parser("spec-update", help="把更新后的规格复制到工作副本（随后需重新 ingest）")
    spec.add_argument("--version", default="v2")
    spec.add_argument("--restore", action="store_true", help="恢复样例自带的 v1 规格")
    commands.add_parser("reset", help="停止 Server 并删除 .runtime（seekdb、PowerContext 数据、工作副本）")
    commands.add_parser("accept", help="核对六项验收并生成 evidence/acceptance_report.json")

    args = parser.parse_args(argv)
    global _SAVE_PATH
    _SAVE_PATH = args.save
    settings = load_settings()
    handlers = {
        "init": cmd_init,
        "ingest": cmd_ingest,
        "context": cmd_context,
        "test": cmd_test,
        "save-state": cmd_save_state,
        "resume": cmd_resume,
        "edit": cmd_edit,
        "server": cmd_server,
        "spec-update": cmd_spec_update,
        "reset": cmd_reset,
        "accept": cmd_accept,
        "devlog": cmd_devlog,
    }
    try:
        return handlers[args.command](settings, args)
    except (ContextError, pc_server.ServerError) as error:
        note(f"错误：{error}")
        return 2
