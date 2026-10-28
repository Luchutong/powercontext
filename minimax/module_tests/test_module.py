"""Tests for the SDD context module itself (not the sample project)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sdd_context import config
from sdd_context.ingest import parse_python, parse_spec
from sdd_context.minimax import EditError, apply_files, flatten_messages, parse_files, unified_diff
from sdd_context.state import plan
from sdd_context.testrun import meets_expectation

MINIMAX_DIR = Path(__file__).resolve().parents[1]
SAMPLE = MINIMAX_DIR / "sample_app"

SPEC = """# 标题

- 组件: GET /things
- 版本: v1

## ABC-001 第一条
- 类型: 变更
- 关键词: tag, 过滤

正文第一行。
正文第二行。

## ABC-002 第二条
- 类型: 保持

保持的行为。
"""


def test_parse_spec_items_types_and_lines():
    items = parse_spec("specs/x.md", SPEC)
    assert [(i.item_id, i.item_type, i.component) for i in items] == [
        ("ABC-001", "change", "GET /things"),
        ("ABC-002", "preserve", "GET /things"),
    ]
    first = items[0]
    assert first.keywords == "tag, 过滤"
    assert first.body == "正文第一行。\n正文第二行。"
    assert (first.start_line, first.end_line) == (6, 11)
    assert SPEC.splitlines()[first.end_line - 1] == "正文第二行。"


def test_spec_sha_changes_with_content():
    old = parse_spec("specs/x.md", SPEC)[0].sha256
    new = parse_spec("specs/x.md", SPEC.replace("正文第一行", "正文改了"))[0].sha256
    assert old != new


def test_parse_python_tests_and_code_refs():
    tests = parse_python("tests/test_tag_filter.py", (SAMPLE / "tests/test_tag_filter.py").read_text(), tests=True)
    by_name = {t.symbol: t for t in tests}
    assert by_name["test_blank_tag_is_rejected"].spec_refs == ["TAG-004"]
    assert {"search_tasks", "QueryError"} <= by_name["test_blank_tag_is_rejected"].names
    code = parse_python("taskboard/service.py", (SAMPLE / "taskboard/service.py").read_text(), tests=False)
    search = next(s for s in code if s.symbol == "search_tasks")
    assert search.spec_refs == ["LIST-001", "LIST-002", "LIST-003", "LIST-004", "LIST-005"]


def test_parse_files_strips_reasoning_and_reads_blocks():
    text = "<think>secret</think>\nFILE: `pkg/a.py`\n```python\nx = 1\n```\n说明。\nFILE: tests/t.py\n```python\ny = 2\n```"
    assert parse_files(text) == [("pkg/a.py", "x = 1\n"), ("tests/t.py", "y = 2\n")]


def test_apply_files_rejects_tests_and_syntax_errors(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/a.py").write_text("x = 0\n")
    written, rejected = apply_files(tmp_path, [("pkg/a.py", "x = 1\n"), ("tests/t.py", "y = 1\n")], {"pkg/a.py"})
    assert written == ["pkg/a.py"] and (tmp_path / "pkg/a.py").read_text() == "x = 1\n"
    assert rejected[0]["path"] == "tests/t.py"
    with pytest.raises(EditError):
        apply_files(tmp_path, [("pkg/a.py", "def broken(:\n")], {"pkg/a.py"})


def test_unified_diff_headers():
    diff = unified_diff({"a.py": "x = 0\n"}, {"a.py": "x = 1\n"})
    assert diff.startswith("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n")
    assert "-x = 0\n+x = 1\n" in diff


def test_flatten_messages_labels_later_turns():
    text = flatten_messages(
        [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}, {"role": "assistant", "content": "A"}, {"role": "user", "content": "F"}]
    )
    assert text == "S\n\nU\n\n## 你上一轮的回复\nA\n\n## 新的反馈（请据此修正）\nF"


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


def test_call_live_parses_minimax_response(monkeypatch):
    from sdd_context import minimax

    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, body=json, auth=headers["Authorization"])
        return _FakeResponse(
            200,
            {
                "id": "resp-1",
                "model": "MiniMax-M3",
                "choices": [{"finish_reason": "stop", "message": {"content": "FILE: a.py\n```python\nx = 1\n```", "reasoning_content": "hidden"}}],
                "usage": {"total_tokens": 42},
                "base_resp": {"status_code": 0, "status_msg": ""},
            },
        )

    monkeypatch.setenv("MINIMAX_API_KEY", "placeholder")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.example.invalid/v1")
    monkeypatch.delenv("MINIMAX_MODEL", raising=False)
    monkeypatch.setattr(minimax.httpx, "post", fake_post)
    text, meta = minimax.call_live([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    assert seen["url"] == "https://api.example.invalid/v1/chat/completions"
    assert seen["auth"] == "Bearer placeholder"
    assert seen["body"]["model"] == "MiniMax-M3" and seen["body"]["reasoning_split"] is True
    assert parse_files(text) == [("a.py", "x = 1\n")]
    assert meta["reasoning_chars_omitted"] == len("hidden") and meta["usage"] == {"total_tokens": 42}

    monkeypatch.setattr(minimax.httpx, "post", lambda *a, **k: _FakeResponse(200, {"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}}))
    with pytest.raises(EditError, match="1008"):
        minimax.call_live([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    monkeypatch.delenv("MINIMAX_API_KEY")
    with pytest.raises(EditError, match="MINIMAX_API_KEY"):
        minimax.call_live([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])


def _summary(**overrides):
    base = {"total": 2, "passed": 0, "failed": 2, "errors": 0, "skipped": 0, "failure_kinds": {"AssertionError": 2}}
    return base | overrides


def test_expectation_rejects_environment_failures():
    assert meets_expectation(_summary(), "fail")[0]
    assert not meets_expectation(_summary(failure_kinds={"ModuleNotFoundError": 2}), "fail")[0]
    assert not meets_expectation(_summary(errors=1), "fail")[0]
    assert not meets_expectation(_summary(total=0, failed=0, failure_kinds={}), "fail")[0]
    assert meets_expectation(_summary(passed=2, failed=0, failure_kinds={}), "pass")[0]


def test_plan_flags_spec_changes_after_verification():
    context = {
        "specs": {"change": [{"id": "T-001", "sha256": "new", "revision": 2}, {"id": "T-002", "sha256": "b", "revision": 1}], "preserve": []},
        "coverage_gaps": ["T-002"],
    }
    state = {
        "status": "restored",
        "done": [{"step": s} for s in ("ingest", "context", "baseline", "edit", "verify")],
        "verification": [{"phase": "verified", "spec_items": {"T-001": "old"}, "code_digest": "c"}],
    }
    result = plan(state, context, "c")
    assert [t["step"] for t in result["todo"]] == ["respec", "respec", "tests"]
    assert result["verification"][0]["spec_changed_since"] == ["T-001"]
    assert result["verification"][0]["spec_added_since"] == ["T-002"]
    assert plan({"status": "empty"}, {"specs": {"change": [], "preserve": []}}, "c")["next_step"] == "baseline"


def test_replay_applies_recording_and_failed_edit_restores_code(tmp_path, monkeypatch):
    pytest.importorskip("pylibseekdb")
    import json

    from sdd_context.ingest import ingest_project
    from sdd_context.minimax import run_edit
    from sdd_context.seekdb_store import SeekdbStore
    from sdd_context.selector import select_context

    project = tmp_path / "project"
    shutil.copytree(SAMPLE, project)
    for name, value in {
        "SDD_RUNTIME_DIR": tmp_path / "runtime",
        "SDD_EVIDENCE_DIR": tmp_path / "evidence",
        "SDD_SNAPSHOTS_DIR": tmp_path / "snapshots",
        "SDD_PROJECT_DIR": project,
    }.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.setenv("SDD_SEEKDB_HOST", "")
    settings = config.load_settings()
    task = config.load_task(MINIMAX_DIR / "sample_task" / "task.toml")
    recorded = MINIMAX_DIR / "evidence" / "minimax" / "session_recorded.json"
    trial = json.loads((MINIMAX_DIR / "evidence/minimax/sessions/mmx-20261028T040706Z-7cb5e1.json").read_text())
    failing = json.loads(recorded.read_text())
    failing["attempts"] = [dict(failing["attempts"][0], response={"text": trial["response"]["text"]})]
    failing_path = tmp_path / "failing.json"
    failing_path.write_text(json.dumps(failing, ensure_ascii=False))
    original = (project / "taskboard/service.py").read_text()

    with SeekdbStore(settings, actor="module-test") as store:
        store.ensure_schema()
        ingest_project(store, task.project, project)
        context = select_context(store, task)
        context["project"] = task.project
        state = {"status": "empty", "done": [], "todo": [], "verification": [], "memories": []}
        with pytest.raises(EditError, match="恢复为修改前"):
            run_edit(settings, task, store, context, state, "replay", failing_path, record=False)
        assert (project / "taskboard/service.py").read_text() == original
        summary = run_edit(settings, task, store, context, state, "replay", recorded, record=False)
    assert summary["final_check_passed"] and summary["diff_sha256"] == json.loads(recorded.read_text())["result"]["diff_sha256"]
    assert (project / "taskboard/service.py").read_text() != original


def test_ingest_and_select_with_embedded_seekdb(tmp_path, monkeypatch):
    pytest.importorskip("pylibseekdb")
    project = tmp_path / "project"
    shutil.copytree(SAMPLE, project)
    monkeypatch.setenv("SDD_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("SDD_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("SDD_PROJECT_DIR", str(project))
    monkeypatch.setenv("SDD_SEEKDB_HOST", "")
    from sdd_context.ingest import ingest_project
    from sdd_context.seekdb_store import SeekdbStore
    from sdd_context.selector import select_context

    settings = config.load_settings()
    task = config.load_task(MINIMAX_DIR / "sample_task" / "task.toml")
    with SeekdbStore(settings, actor="module-test") as store:
        store.ensure_schema()
        first = ingest_project(store, task.project, project)
        context = select_context(store, task)
        assert first["first_import"] and first["stored"]["spec_items"] == 14
        assert [e["id"] for e in context["specs"]["change"]] == ["TAG-001", "TAG-002", "TAG-003", "TAG-004"]
        assert [t["symbol_id"] for t in context["code_targets"] if t["role"] == "modify"] == ["taskboard/service.py::search_tasks"]

        shutil.copy(MINIMAX_DIR / "sample_task/spec_updates/v2/tag-filter.md", project / "specs/tag-filter.md")
        second = ingest_project(store, task.project, project)
        updated = select_context(store, task)
    assert second["spec_changes"]["modified"] == ["TAG-001"] and second["spec_changes"]["added"] == ["TAG-005"]
    tag1 = next(e for e in updated["specs"]["change"] if e["id"] == "TAG-001")
    assert tag1["revision"] == 2 and tag1["changed_in_latest_import"] and "同时包含全部指定标签" in tag1["text"]
    assert updated["coverage_gaps"] == ["TAG-005"]
    assert (tmp_path / "evidence" / "seekdb_queries.log").read_text().count("[module-test] Q1 ") == 2
