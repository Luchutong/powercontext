"""Run the sample project's tests and keep auditable logs (command, exit code, code version)."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from sdd_context.config import Settings
from sdd_context.ingest import collect_project, digest_files

FUNCTIONAL_FAILURES = {"AssertionError", "DidNotRaise"}
EXCEPTION_LINE = re.compile(r"^E\s+([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt))\b", re.MULTILINE)


def code_version(settings: Settings) -> dict[str, Any]:
    files = collect_project(settings.project_dir)
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=settings.minimax_dir, capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=settings.minimax_dir, capture_output=True, text=True, check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = "unknown", None
    return {
        "git_commit": commit,
        "git_dirty": dirty,
        "code_digest": digest_files(files.code),
        "tests_digest": digest_files({**files.tests, **files.support}),
        "spec_digest": digest_files(files.specs),
    }


def _classify(element: ET.Element) -> str:
    message = element.attrib.get("message", "")
    if message.startswith("Failed: DID NOT RAISE"):
        return "DidNotRaise"
    if message.startswith("assert ") or message.startswith("AssertionError"):
        return "AssertionError"
    found = EXCEPTION_LINE.findall(element.text or "")
    return found[-1] if found else "Unknown"


def parse_junit(path: Path) -> list[dict[str, Any]]:
    cases = []
    for case in ET.parse(path).getroot().iter("testcase"):
        module = case.attrib.get("classname", "").replace(".", "/")
        nodeid = f"{module}.py::{case.attrib.get('name')}"
        outcome, kind, message = "passed", None, None
        for child in case:
            if child.tag in {"failure", "error"}:
                outcome = "failed" if child.tag == "failure" else "error"
                kind = _classify(child) if child.tag == "failure" else "SetupOrCollectionError"
                message = child.attrib.get("message", "")[:300]
                break
            if child.tag == "skipped":
                outcome = "skipped"
        cases.append({"nodeid": nodeid, "outcome": outcome, "failure_kind": kind, "message": message})
    return cases


def run_suite(settings: Settings, suite: str, selectors: list[str], label: str, out_dir: Path | None = None) -> dict[str, Any]:
    out_dir = out_dir or settings.evidence_dir / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    junit = settings.runtime_dir / "junit" / f"{label}-{suite}.xml"
    junit.parent.mkdir(parents=True, exist_ok=True)
    junit.unlink(missing_ok=True)
    command = [sys.executable, "-m", "pytest", "-q", "-rfE", f"--junitxml={junit}", *selectors]
    display = "python -m pytest -q -rfE " + " ".join(shlex.quote(s) for s in selectors)
    version = code_version(settings)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    started = dt.datetime.now(dt.UTC)
    clock = time.perf_counter()
    proc = subprocess.run(command, cwd=settings.project_dir, capture_output=True, text=True, env=env)
    duration = round(time.perf_counter() - clock, 2)
    cases = parse_junit(junit) if junit.exists() else []
    counts = {key: sum(1 for case in cases if case["outcome"] == key) for key in ("passed", "failed", "error", "skipped")}
    kinds: dict[str, int] = {}
    for case in cases:
        if case["failure_kind"]:
            kinds[case["failure_kind"]] = kinds.get(case["failure_kind"], 0) + 1
    log_path = out_dir / f"{label}-{suite}.log"
    summary = {
        "suite": suite,
        "label": label,
        "command": display,
        "cwd": str(settings.project_dir),
        "exit_code": proc.returncode,
        "started_at": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "duration_s": duration,
        **version,
        "total": len(cases),
        "passed": counts["passed"],
        "failed": counts["failed"],
        "errors": counts["error"],
        "skipped": counts["skipped"],
        "failure_kinds": kinds,
        "cases": cases,
        "log": str(log_path.relative_to(settings.minimax_dir)) if log_path.is_relative_to(settings.minimax_dir) else str(log_path),
    }
    header = [
        f"# suite: {suite} ({label})",
        f"# started_at: {summary['started_at']}",
        f"# cwd: {summary['cwd']}",
        f"# command: {display}",
        f"# exit_code: {proc.returncode}",
        f"# git_commit: {version['git_commit']} (dirty={version['git_dirty']})",
        f"# code_digest: {version['code_digest']}",
        f"# tests_digest: {version['tests_digest']}",
        f"# result: total={len(cases)} passed={counts['passed']} failed={counts['failed']} errors={counts['error']} skipped={counts['skipped']} failure_kinds={json.dumps(kinds)}",
        "",
    ]
    log_path.write_text("\n".join(header) + proc.stdout + ("\n# stderr:\n" + proc.stderr if proc.stderr.strip() else ""), encoding="utf-8")
    (out_dir / f"{label}-{suite}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def meets_expectation(summary: dict[str, Any], expect: str) -> tuple[bool, str]:
    if summary["total"] == 0:
        return False, "没有收集到测试"
    if summary["errors"]:
        return False, f"{summary['errors']} 个用例出现 setup/collection 错误（环境问题，不算功能失败）"
    if expect == "pass":
        ok = summary["failed"] == 0 and summary["passed"] == summary["total"] - summary["skipped"] and summary["skipped"] == 0
        return ok, "全部通过" if ok else f"{summary['failed']} 个失败"
    if expect == "fail":
        non_functional = {kind: n for kind, n in summary["failure_kinds"].items() if kind not in FUNCTIONAL_FAILURES}
        if summary["passed"] or summary["skipped"]:
            return False, f"期望全部失败，但有 {summary['passed']} 个通过、{summary['skipped']} 个跳过"
        if non_functional:
            return False, f"存在非功能性失败 {non_functional}"
        return True, f"{summary['failed']} 个用例全部因目标行为未实现而失败 {summary['failure_kinds']}"
    return True, "未设置期望"
