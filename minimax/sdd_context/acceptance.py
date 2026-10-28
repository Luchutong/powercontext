"""Check the six mandatory acceptance items against the artifacts produced by scripts/demo_accept.sh.

This module only reads evidence; the context module never reads
``sample_task/required_sources.json`` (that file is the reviewer's checklist).
"""

from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
from pathlib import Path
from typing import Any

from sdd_context.config import Settings
from sdd_context.testrun import meets_expectation

ITEMS = [
    ("runnable", "能够运行"),
    ("minimax", "MiniMax 实际参与"),
    ("context", "上下文正确"),
    ("database", "数据库实际参与"),
    ("state", "状态能够复用"),
    ("code", "代码修改有效"),
]


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Checker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.minimax_dir
        self.ev = settings.evidence_dir
        self.results: dict[str, list[tuple[bool, str]]] = {key: [] for key, _ in ITEMS}

    def check(self, item: str, ok: bool, message: str) -> bool:
        self.results[item].append((bool(ok), message))
        return bool(ok)

    def load(self, item: str, rel: str) -> Any:
        path = self.ev / rel
        if not path.is_file():
            self.check(item, False, f"缺少 evidence/{rel}")
            return None
        return _load(path)

    # 1 ---------------------------------------------------------------------
    def runnable(self) -> None:
        path = self.ev / "demo_steps.tsv"
        if not self.check("runnable", path.is_file(), "evidence/demo_steps.tsv 存在（demo_accept.sh 每一步的命令与退出码）"):
            return
        rows = [line.split("\t") for line in path.read_text(encoding="utf-8").splitlines()[1:] if line.strip()]
        failed = [row for row in rows if row[2] != row[3]]
        self.check("runnable", rows and not failed, f"{len(rows)} 个步骤的退出码均符合预期" if not failed else f"退出码不符：{failed}")
        names = {row[0] for row in rows}
        required = {"init", "ingest-v1", "context-v1", "test-before-target", "test-before-regression", "save-baseline", "edit",
                    "test-after-target", "test-after-regression", "test-after-full", "save-verified", "server-stop", "server-start",
                    "resume-after-restart", "context-resume", "spec-update-v2", "ingest-v2", "context-v2"}
        self.check("runnable", required <= names, "覆盖初始化、导入、上下文、测试、MiniMax 修改、状态保存与重启、规格更新全流程"
                   if required <= names else f"缺少步骤 {sorted(required - names)}")

    # 2 ---------------------------------------------------------------------
    def minimax(self) -> None:
        recorded = self.load("minimax", "minimax/session_recorded.json")
        last = self.load("minimax", "minimax/last_edit.json")
        if recorded is None or last is None:
            return
        session = recorded
        if last.get("mode") in {"live", "hf-space"} and last.get("session_file"):
            session = _load(self.root / last["session_file"])
        self.check("minimax", session.get("transport") in {"live", "hf-space"} and session.get("model"),
                   f"开发记录来自真实 MiniMax 调用：transport={session.get('transport')}，model={session.get('model')}，endpoint={session.get('endpoint')}")
        prompt = "\n".join(message["content"] for message in session["request"]["messages"])
        task_id = session["task"]["id"]
        needed = [task_id, *session["context"]["change_specs"], *[t["symbol"].split("::")[1] for t in session["context"]["code_targets"] if t["role"] == "modify"]]
        missing = [token for token in needed if token not in prompt]
        self.check("minimax", not missing, f"记录包含任务与提供的上下文（{', '.join(needed)}）" if not missing else f"提示词缺少 {missing}")
        self.check("minimax", "FILE:" in session["response"]["text"] and session["result"]["files"],
                   f"记录包含 MiniMax 回复与修改过程（修改文件 {[f['path'] for f in session['result']['files']]}）")
        diff_text = (self.ev / "code.diff").read_text(encoding="utf-8") if (self.ev / "code.diff").is_file() else ""
        self.check("minimax", diff_text and _sha(diff_text) == session["result"]["diff_sha256"],
                   f"evidence/code.diff 与开发记录中的 diff 一致（sha256 {session['result']['diff_sha256'][:12]}）")
        snap = []
        for entry in session["result"]["files"]:
            before = (self.root / "snapshots" / "before" / entry["path"]).read_text(encoding="utf-8")
            after = (self.root / "snapshots" / "after" / entry["path"]).read_text(encoding="utf-8")
            snap.append(f"diff --git a/{entry['path']} b/{entry['path']}\n" + "".join(difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=f"a/{entry['path']}", tofile=f"b/{entry['path']}")))
            self.check("minimax", _sha(before) == entry["before_sha256"] and _sha(after) == entry["after_sha256"],
                       f"snapshots/before|after/{entry['path']} 与记录中的文件摘要一致")
        self.check("minimax", "".join(snap) == diff_text, "由 before/after 快照重新计算的 diff 与 code.diff 完全相同")
        if last.get("mode") == "replay":
            replay = self.load("minimax", "minimax/replay_check.json") or {}
            self.check("minimax", replay.get("diff_matches_recording"), "本次回放应用录制回复得到的 diff 与录制时一致")
            self.check("minimax", replay.get("prompt_matches_recording"), "本次运行生成的提示词与录制时提供给 MiniMax 的提示词逐字节一致")
        self.check("minimax", (self.ev / "minimax" / "开发记录.md").is_file(), "evidence/minimax/开发记录.md 存在")

    # 3 ---------------------------------------------------------------------
    def context(self) -> None:
        required = _load(self.root / "sample_task" / "required_sources.json")
        v1 = self.load("context", "context_v1.json")
        v2 = self.load("context", "context_v2.json")
        if v1 is None or v2 is None:
            return
        specs = {entry["id"]: entry for kind in ("change", "preserve") for entry in v1["specs"][kind]}
        missing = [item for item in required["specs"]["change"] + required["specs"]["preserve"] if item not in specs]
        wrong_type = [item for item in required["specs"]["change"] if item in specs and specs[item]["type"] != "change"]
        self.check("context", not missing and not wrong_type, f"规格条目齐全：变更 {required['specs']['change']}，保持 {required['specs']['preserve']}"
                   if not missing else f"缺少规格 {missing}")
        excluded = [item for item in required["specs"]["not_applicable"] if item in specs]
        self.check("context", not excluded, "无关组件的条目未被选入（" + ", ".join(required["specs"]["not_applicable"]) + "）")
        targets = {(t["path"], t["symbol"]): t for t in v1["code_targets"]}
        for code in required["code"]:
            entry = targets.get((code["path"], code["symbol"]))
            self.check("context", entry is not None and entry["role"] == code["role"], f"代码目标 {code['path']}::{code['symbol']}（{code['role']}）")
        for group in ("target", "regression"):
            ids = {test["id"] for test in v1["tests"][group]}
            want = set(required["tests"][group])
            self.check("context", want <= ids, f"{group} 测试 {len(want)} 个全部列出" if want <= ids else f"{group} 测试缺少 {sorted(want - ids)}")
        problems = []
        project = Path(v1["project_dir"])
        for source in v1["sources"]:
            path = project / source["path"]
            template = self.settings.template_dir / source["path"]
            if not path.is_file() and not template.is_file():
                problems.append(source["path"])
        self.check("context", not problems and not v1["source_check"], "所有来源文件真实存在且与导入摘要一致"
                   if not problems else f"来源不存在 {problems}")
        state = v1.get("state", {})
        self.check("context", {"done", "todo", "verification"} <= set(state), "输出包含已完成事项、待办事项和已有验证结果")
        new = {entry["id"]: entry for kind in ("change", "preserve") for entry in v2["specs"][kind]}
        for item, expect in required["spec_update"]["expect"].items():
            entry = new.get(item)
            ok = entry is not None and entry["revision"] == expect["revision"] and expect["contains"] in entry["text"] and entry["changed_in_latest_import"]
            self.check("context", ok, f"重新导入 v2 后 {item} 使用新内容（rev {expect['revision']}，包含“{expect['contains']}”）")
        old_text = specs.get("TAG-001", {}).get("text")
        self.check("context", old_text and new.get("TAG-001", {}).get("text") != old_text and v1["import"]["spec_digest"] != v2["import"]["spec_digest"],
                   "v1 与 v2 的输出不同（不是固定答案）")
        todo = " ".join(t["text"] for t in v2.get("state", {}).get("todo", []))
        self.check("context", all(item in todo for item in required["spec_update"]["expect"]),
                   "v2 输出的待办包含因规格变更需要重新实现/验证的条目")

    # 4 ---------------------------------------------------------------------
    def database(self) -> None:
        v1 = self.load("database", "context_v1.json")
        log = self.ev / "seekdb_queries.log"
        if v1 is None or not self.check("database", log.is_file(), "evidence/seekdb_queries.log 存在"):
            return
        text = log.read_text(encoding="utf-8")
        retrieval = v1["retrieval"]
        self.check("database", "seekdb" in retrieval["engine"].lower(), f"检索引擎：{retrieval['engine']}（{retrieval['mode']}）")
        rows = {query["id"]: query["rows"] for query in retrieval["queries"]}
        self.check("database", all(rows.get(q, 0) > 0 for q in ("Q1", "Q2", "Q3", "Q4", "Q9")), f"上下文查询返回行数 {rows}")
        self.check("database", all(f"[context] {q} " in text for q in ("Q1", "Q2", "Q3")) and "MATCH(search_text) AGAINST" in text,
                   "查询日志记录了 context 命令执行的全文检索与关联查询（含 SQL 与返回行）")
        entries = [e for kind in ("change", "preserve") for e in v1["specs"][kind]] + v1["code_targets"] + v1["tests"]["target"] + v1["tests"]["regression"]
        self.check("database", all(entry.get("via") for entry in entries), f"{len(entries)} 条上下文条目都标注了产生它们的 seekdb 查询编号")
        if self.settings.seekdb_mode == "embedded":
            self.check("database", (self.settings.seekdb_dir / "seekdb.db").is_dir(), f"嵌入式 seekdb 数据目录存在：{self.settings.seekdb_dir / 'seekdb.db'}")
        session = self.ev / "minimax" / "session_recorded.json"
        if session.is_file():
            prompt = _load(session)["request"]["messages"][1]["content"]
            spec = next(entry for entry in v1["specs"]["change"] if entry["id"] == "TAG-004")
            self.check("database", spec["text"] in prompt, "交给 MiniMax 的提示词中的规格正文来自 seekdb 查询结果")

    # 5 ---------------------------------------------------------------------
    def state(self) -> None:
        empty = self.load("state", "state/resume_empty.json")
        baseline = self.load("state", "state/save-baseline.json")
        verified = self.load("state", "state/save-verified.json")
        before = self.load("state", "state/server_before_restart.json")
        stopped = self.load("state", "state/server_stop.json")
        started = self.load("state", "state/server_start.json")
        resumed = self.load("state", "state/resume_after_restart.json")
        ctx = self.load("state", "context_resume.json")
        skip = self.load("state", "state/edit_after_restart.json")
        if None in (empty, baseline, verified, before, stopped, started, resumed, ctx, skip):
            return
        self.check("state", empty["state"]["status"] == "empty", "从空状态开始：首次 resume 时 PowerContext 中没有该任务的记录")
        self.check("state", baseline["handoff"]["revision"] == 1 and verified["handoff"]["revision"] == 2,
                   f"已写入：Memory {len(baseline['memories']) + len(verified['memories'])} 条、Handoff 两个修订、验证记录与 MiniMax 修改记录 Source")
        restarted = before.get("pid") and started.get("pid") and before["pid"] != started["pid"] and stopped.get("stopped_pid") == before["pid"]
        self.check("state", restarted and not stopped.get("live") and started.get("started"),
                   f"PowerContext Server 已停止（pid {before.get('pid')}，停止后 live={stopped.get('live')}）并重新启动（pid {started.get('pid')}）")
        self.check("state", resumed.get("process_pid") not in {None, empty.get("process_pid")}, "读回状态的是新的模块进程（进程内无残留状态）")
        state = resumed["state"]
        ref = (state.get("handoff") or {}).get("reference") or {}
        self.check("state", state["status"] == "restored" and ref.get("revision") == verified["handoff"]["revision"],
                   f"重启后读回 Handoff {ref.get('family')}/{ref.get('artifact_id')}@{ref.get('revision')}")
        phases = {record["phase"]: record for record in state.get("verification", [])}
        after = phases.get("verified", {}).get("suites", {})
        self.check("state", phases.keys() >= {"baseline", "verified"} and after and all(s["passed"] == s["total"] for s in after.values()),
                   "重启后读回修改前/修改后的验证结果")
        self.check("state", (state.get("edit") or {}).get("session_id") == verified.get("edit_session"),
                   f"重启后读回 MiniMax 修改记录 {(state.get('edit') or {}).get('session_id')}")
        self.check("state", ctx["state"]["status"] == "restored" and not ctx["state"]["todo"] and {d["step"] for d in ctx["state"]["done"]} >= {"edit", "verify"},
                   "重启后的上下文输出使用读回的状态：已完成 edit/verify，待办为空")
        self.check("state", skip.get("skipped") is True, "重启后 edit 读取状态判断任务已完成，跳过重复修改")

    # 6 ---------------------------------------------------------------------
    def code(self) -> None:
        loaded = {name: self.load("code", f"tests/{name}.json") for name in
                  ("before-target", "before-regression", "after-target", "after-regression", "after-full")}
        if None in loaded.values():
            return
        ok, reason = meets_expectation(loaded["before-target"], "fail")
        self.check("code", ok, f"修改前目标测试：{reason}")
        for name in ("before-regression", "after-target", "after-regression", "after-full"):
            ok, reason = meets_expectation(loaded[name], "pass")
            data = loaded[name]
            self.check("code", ok, f"{name}: {data['passed']}/{data['total']} 通过（exit {data['exit_code']}）")
        before_cases = {case["nodeid"] for case in loaded["before-target"]["cases"]}
        after_cases = {case["nodeid"] for case in loaded["after-target"]["cases"]}
        self.check("code", before_cases == after_cases, f"修改前后运行的是同一组目标测试（{len(before_cases)} 个用例）")
        digests = {loaded[name]["tests_digest"] for name in loaded}
        self.check("code", len(digests) == 1, "所有测试运行中测试文件摘要一致（测试集未被修改）")
        self.check("code", loaded["before-target"]["code_digest"] != loaded["after-target"]["code_digest"], "修改前后代码摘要不同（代码确实被修改）")

    def run(self) -> dict[str, Any]:
        for key, _ in ITEMS:
            try:
                getattr(self, key)()
            except Exception as error:  # report, do not hide
                self.check(key, False, f"检查出错：{type(error).__name__}: {error}")
        items = []
        for key, title in ITEMS:
            checks = self.results[key]
            items.append({"key": key, "title": title, "passed": bool(checks) and all(ok for ok, _ in checks),
                          "checks": [{"ok": ok, "message": message} for ok, message in checks]})
        return {"generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "passed": all(item["passed"] for item in items), "items": items}


def run_acceptance(settings: Settings) -> dict[str, Any]:
    report = Checker(settings).run()
    (settings.evidence_dir / "acceptance_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 六项验收核对结果", "", f"生成时间：{report['generated_at']}；由 `./sdd accept` 根据本次运行产物自动核对。", ""]
    for index, item in enumerate(report["items"], start=1):
        lines.append(f"## {index}. {item['title']}：{'通过' if item['passed'] else '未通过'}")
        lines.append("")
        lines += [f"- [{'PASS' if c['ok'] else 'FAIL'}] {c['message']}" for c in item["checks"]]
        lines.append("")
    (settings.evidence_dir / "验收结果.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n六项验收：")
    for index, item in enumerate(report["items"], start=1):
        print(f"  {index}. {item['title']:<12} {'PASS' if item['passed'] else 'FAIL'}")
        for check in item["checks"]:
            if not check["ok"]:
                print(f"       [FAIL] {check['message']}")
    print(f"\n总体：{'PASS（六项全部通过）' if report['passed'] else 'FAIL'}；明细见 evidence/验收结果.md")
    return report
