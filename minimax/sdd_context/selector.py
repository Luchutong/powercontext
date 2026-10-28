"""Select the development context for a task from seekdb.

Every entry of the returned context carries the IDs of the seekdb queries that
produced it (``via``); the SQL and the returned rows are in the query log.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from pathlib import Path
from typing import Any

from sdd_context.config import Task
from sdd_context.ingest import sha256_text
from sdd_context.seekdb_store import SeekdbStore

CONTEXT_SCHEMA = "minimax-sdd.context.v1"
CHANGE_SCORE_RATIO = 0.3
CODE_SCORE_RATIO = 0.5
MAX_RELATED_SYMBOLS = 6


class ContextError(RuntimeError):
    pass


def _source(path: str, start: int, end: int) -> str:
    return f"{path}#L{start}-L{end}"


def _split(value: str) -> list[str]:
    return [part for part in (value or "").split(",") if part]


def select_context(store: SeekdbStore, task: Task) -> dict[str, Any]:
    project = task.project
    query_text = task.query_text

    imports = store.query(
        "Q0",
        "最近一次导入批次",
        "SELECT import_id, imported_at, spec_digest, code_digest, test_digest, spec_items, code_symbols, test_cases, "
        "added_items, modified_items, removed_items, "
        "(SELECT MIN(import_id) FROM sdd_import WHERE project = %s) AS first_import_id "
        "FROM sdd_import WHERE project = %s ORDER BY import_id DESC LIMIT 1",
        (project, project),
    )
    if not imports:
        raise ContextError("seekdb 中没有该项目的导入记录，请先运行 `sdd ingest`")
    latest_import = imports[0]
    latest_import_id = int(latest_import["import_id"])
    is_first_import = latest_import_id == int(latest_import["first_import_id"])

    hits = store.query(
        "Q1",
        "规格条目全文检索（任务描述，ik 分词）",
        "SELECT item_id, component, item_type, title, revision, "
        "ROUND(MATCH(search_text) AGAINST(%s IN NATURAL LANGUAGE MODE), 4) AS score "
        "FROM sdd_spec_item WHERE project = %s AND MATCH(search_text) AGAINST(%s IN NATURAL LANGUAGE MODE) "
        "ORDER BY score DESC, item_id LIMIT 30",
        (query_text, project, query_text),
    )
    if not hits:
        raise ContextError("全文检索没有命中任何规格条目")
    change_hits = [hit for hit in hits if hit["item_type"] == "change"]
    if change_hits:
        top_score = float(change_hits[0]["score"])
        matched_change = [hit for hit in change_hits if float(hit["score"]) >= CHANGE_SCORE_RATIO * top_score]
        components = sorted({hit["component"] for hit in matched_change})
    else:
        matched_change = []
        components = [hits[0]["component"]]
    hit_scores = {hit["item_id"]: float(hit["score"]) for hit in hits}

    items = store.query(
        "Q2",
        "命中组件下的全部有效规格条目（变更 + 必须保持）",
        "SELECT item_id, doc_path, doc_title, component, item_type, title, keywords, body, start_line, end_line, "
        "sha256, revision, import_id FROM sdd_spec_item WHERE project = %s AND component IN %s ORDER BY item_type, item_id",
        (project, tuple(components)),
    )
    if not items:
        raise ContextError(f"组件 {components} 下没有规格条目")
    selected: dict[str, dict[str, Any]] = {row["item_id"]: dict(row, via=["Q1", "Q2"] if row["item_id"] in hit_scores else ["Q2"]) for row in items}

    tests_by_item = _tests_for_items(store, project, list(selected), "Q3")

    target_test_ids = sorted({row["test_id"] for row in tests_by_item if selected[row["spec_id"]]["item_type"] == "change"})
    implementers = store.query(
        "Q4",
        "实现这些规格条目的代码符号（docstring/注释中的规格编号）",
        "SELECT l.dst_id AS spec_id, s.symbol_id, s.path, s.symbol, s.kind, s.start_line, s.end_line, s.signature "
        "FROM sdd_link l JOIN sdd_code_symbol s ON s.project = l.project AND s.symbol_id = l.src_id "
        "WHERE l.project = %s AND l.relation = 'implements' AND l.dst_id IN %s ORDER BY s.path, s.start_line",
        (project, tuple(selected)),
    )
    used_by_targets = store.query(
        "Q5",
        "目标测试直接调用/引用的代码符号",
        "SELECT l.src_id AS test_id, s.symbol_id, s.path, s.symbol, s.kind, s.start_line, s.end_line, s.signature "
        "FROM sdd_link l JOIN sdd_code_symbol s ON s.project = l.project AND s.symbol_id = l.dst_id "
        "WHERE l.project = %s AND l.relation = 'uses' AND l.src_id IN %s ORDER BY s.path, s.start_line",
        (project, tuple(target_test_ids) or ("",)),
    )
    change_items = [row for row in selected.values() if row["item_type"] == "change"]
    code_query = " ".join(f"{row['title']} {row['keywords']} {row['body']}" for row in change_items) or query_text
    code_hits = store.query(
        "Q6",
        "代码符号全文检索（变更条目正文，ik 分词）",
        "SELECT symbol_id, path, symbol, kind, start_line, end_line, signature, "
        "ROUND(MATCH(search_text) AGAINST(%s IN NATURAL LANGUAGE MODE), 4) AS score "
        "FROM sdd_code_symbol WHERE project = %s AND MATCH(search_text) AGAINST(%s IN NATURAL LANGUAGE MODE) "
        "ORDER BY score DESC, symbol_id LIMIT 15",
        (code_query, project, code_query),
    )

    code_targets = _rank_code(implementers, used_by_targets, code_hits)
    modify_ids = [target["symbol_id"] for target in code_targets if target["role"] == "modify"]
    impacted = store.query(
        "Q7",
        "修改目标实现的规格条目（影响分析）",
        "SELECT l.src_id AS symbol_id, i.item_id, i.doc_path, i.doc_title, i.component, i.item_type, i.title, i.keywords, "
        "i.body, i.start_line, i.end_line, i.sha256, i.revision, i.import_id "
        "FROM sdd_link l JOIN sdd_spec_item i ON i.project = l.project AND i.item_id = l.dst_id "
        "WHERE l.project = %s AND l.relation = 'implements' AND l.src_id IN %s ORDER BY i.item_id",
        (project, tuple(modify_ids) or ("",)),
    )
    extra = {}
    for row in impacted:
        if row["item_id"] not in selected and row["item_id"] not in extra:
            data = {key: value for key, value in row.items() if key != "symbol_id"}
            extra[row["item_id"]] = dict(data, via=["Q7"], impacted_by=[row["symbol_id"]])
    if extra:
        selected.update(extra)
        tests_by_item += _tests_for_items(store, project, list(extra), "Q3b")

    history = store.query(
        "Q8",
        "选中规格条目的修订历史",
        "SELECT item_id, revision, change_kind, sha256, import_id FROM sdd_spec_history "
        "WHERE project = %s AND item_id IN %s ORDER BY item_id, revision",
        (project, tuple(selected)),
    )

    tests_for: dict[str, list[str]] = defaultdict(list)
    test_rows: dict[str, dict[str, Any]] = {}
    for row in tests_by_item:
        tests_for[row["spec_id"]].append(row["test_id"])
        test_rows.setdefault(row["test_id"], row)
    implemented_by: dict[str, list[str]] = defaultdict(list)
    for row in implementers:
        implemented_by[row["spec_id"]].append(row["symbol_id"])
    history_by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        history_by_item[row["item_id"]].append(
            {"revision": int(row["revision"]), "change": row["change_kind"], "import_id": int(row["import_id"]), "sha256": row["sha256"]}
        )

    specs: dict[str, list[dict[str, Any]]] = {"change": [], "preserve": []}
    for item_id in sorted(selected):
        row = selected[item_id]
        changes = history_by_item.get(item_id, [])
        specs[row["item_type"]].append(
            {
                "id": item_id,
                "title": row["title"],
                "component": row["component"],
                "type": row["item_type"],
                "text": row["body"],
                "source": _source(row["doc_path"], int(row["start_line"]), int(row["end_line"])),
                "path": row["doc_path"],
                "lines": [int(row["start_line"]), int(row["end_line"])],
                "sha256": row["sha256"],
                "revision": int(row["revision"]),
                "changed_in_latest_import": not is_first_import and any(
                    change["import_id"] == latest_import_id and change["change"] in {"added", "modified"} for change in changes
                ),
                "history": changes,
                "score": hit_scores.get(item_id),
                "tests": sorted(dict.fromkeys(tests_for.get(item_id, []))),
                "implemented_by": sorted(dict.fromkeys(implemented_by.get(item_id, []))),
                "via": row["via"],
                **({"impacted_by": row["impacted_by"]} if "impacted_by" in row else {}),
            }
        )

    change_ids = {entry["id"] for entry in specs["change"]}
    target_tests, regression_tests = [], []
    for test_id in sorted(test_rows):
        row = test_rows[test_id]
        verifies = _split(row["spec_refs"])
        entry = {
            "id": test_id,
            "path": row["path"],
            "name": row["name"],
            "lines": [int(row["start_line"]), int(row["end_line"])],
            "source": _source(row["path"], int(row["start_line"]), int(row["end_line"])),
            "verifies": verifies,
            "uses": _split(row["uses"]),
            "via": ["Q3"],
        }
        (target_tests if change_ids.intersection(verifies) else regression_tests).append(entry)

    paths = sorted(
        {entry["path"] for group in specs.values() for entry in group}
        | {target["path"] for target in code_targets}
        | {test["path"] for test in target_tests + regression_tests}
    )
    documents = store.query(
        "Q9",
        "上下文引用的源文件（路径、摘要、行数）",
        "SELECT path, kind, sha256, line_count, import_id FROM sdd_document WHERE project = %s AND path IN %s ORDER BY path",
        (project, tuple(paths)),
    )

    gaps = [entry["id"] for entry in specs["change"] if not entry["tests"]]
    return {
        "schema": CONTEXT_SCHEMA,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "task": {"id": task.id, "title": task.title, "description": task.description, "file": str(task.path)},
        "import": {
            "import_id": latest_import_id,
            "first_import": is_first_import,
            "imported_at": str(latest_import["imported_at"]),
            "spec_digest": latest_import["spec_digest"],
            "code_digest": latest_import["code_digest"],
            "test_digest": latest_import["test_digest"],
            "added": _split(latest_import["added_items"]),
            "modified": _split(latest_import["modified_items"]),
            "removed": _split(latest_import["removed_items"]),
        },
        "selection": {
            "components": components,
            "matched_change_items": [hit["item_id"] for hit in matched_change],
            "rule": f"Q1 中类型为变更且得分 ≥ 最高分×{CHANGE_SCORE_RATIO} 的条目确定组件；Q2 取这些组件的全部有效条目；Q7 补充修改目标影响到的条目",
        },
        "specs": specs,
        "code_targets": code_targets,
        "tests": {
            "target": target_tests,
            "regression": regression_tests,
            "commands": {
                "target": "python -m pytest " + " ".join(test["id"] for test in target_tests),
                "regression": "python -m pytest " + " ".join(test["id"] for test in regression_tests),
                "full": "python -m pytest",
            },
        },
        "coverage_gaps": gaps,
        "sources": [
            {"path": row["path"], "kind": row["kind"], "sha256": row["sha256"], "lines": int(row["line_count"]), "via": ["Q9"]}
            for row in documents
        ],
        "retrieval": {
            "engine": store.version,
            "mode": store.settings.seekdb_mode,
            "database": store.settings.seekdb_database,
            "query_log": str(store.log_path),
            "queries": list(store.queries),
        },
    }


def _tests_for_items(store: SeekdbStore, project: str, item_ids: list[str], qid: str) -> list[dict[str, Any]]:
    return store.query(
        qid,
        "验证这些规格条目的测试（pytest spec 标记）",
        "SELECT l.dst_id AS spec_id, t.test_id, t.path, t.name, t.start_line, t.end_line, t.spec_refs, t.uses "
        "FROM sdd_link l JOIN sdd_test_case t ON t.project = l.project AND t.test_id = l.src_id "
        "WHERE l.project = %s AND l.relation = 'verifies' AND l.dst_id IN %s ORDER BY t.path, t.start_line",
        (project, tuple(item_ids) or ("",)),
    )


def _rank_code(
    implementers: list[dict[str, Any]], used_by_targets: list[dict[str, Any]], code_hits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}

    def entry(row: dict[str, Any]) -> dict[str, Any]:
        return candidates.setdefault(
            row["symbol_id"],
            {
                "symbol_id": row["symbol_id"],
                "path": row["path"],
                "symbol": row["symbol"],
                "kind": row["kind"],
                "lines": [int(row["start_line"]), int(row["end_line"])],
                "source": _source(row["path"], int(row["start_line"]), int(row["end_line"])),
                "signature": row["signature"],
                "implements": [],
                "used_by_target_tests": [],
                "fulltext_score": 0.0,
                "via": [],
            },
        )

    for row in implementers:
        target = entry(row)
        target["implements"].append(row["spec_id"])
        target["via"].append("Q4")
    for row in used_by_targets:
        target = entry(row)
        target["used_by_target_tests"].append(row["test_id"])
        target["via"].append("Q5")
    top = max((float(row["score"]) for row in code_hits), default=0.0) or 1.0
    for row in code_hits:
        score = float(row["score"])
        if row["symbol_id"] not in candidates and score < CODE_SCORE_RATIO * top:
            continue
        target = entry(row)
        target["fulltext_score"] = round(score / top, 4)
        target["via"].append("Q6")

    ranked = []
    for target in candidates.values():
        target["implements"] = sorted(dict.fromkeys(target["implements"]))
        target["used_by_target_tests"] = sorted(dict.fromkeys(target["used_by_target_tests"]))
        target["via"] = sorted(dict.fromkeys(target["via"]))
        is_function = target["kind"] == "function"
        target["rank_score"] = round(
            2.0 * bool(target["used_by_target_tests"] and is_function) + 1.0 * bool(target["implements"]) + target["fulltext_score"], 4
        )
        reasons = []
        if target["used_by_target_tests"]:
            reasons.append(f"被 {len(target['used_by_target_tests'])} 个目标测试直接调用/引用")
        if target["implements"]:
            reasons.append("实现规格 " + ", ".join(target["implements"]))
        if target["fulltext_score"]:
            reasons.append(f"与变更条目全文相关度 {target['fulltext_score']}")
        target["reasons"] = reasons
        ranked.append(target)
    ranked.sort(key=lambda item: (-item["rank_score"], item["symbol_id"]))
    modify = [t for t in ranked if t["kind"] == "function" and t["used_by_target_tests"]]
    if not modify:
        modify = [t for t in ranked if t["kind"] == "function"][:1]
    modify_ids = {t["symbol_id"] for t in modify}
    for target in ranked:
        target["role"] = "modify" if target["symbol_id"] in modify_ids else "related"
    related = [t for t in ranked if t["role"] == "related"][:MAX_RELATED_SYMBOLS]
    return [t for t in ranked if t["role"] == "modify"] + related


def verify_sources(context: dict[str, Any], project_dir: Path) -> list[str]:
    """Return problems found when checking that every cited source exists with the recorded digest."""
    problems = []
    for source in context["sources"]:
        path = project_dir / source["path"]
        if not path.is_file():
            problems.append(f"missing: {source['path']}")
        elif sha256_text(path.read_text(encoding="utf-8")) != source["sha256"]:
            problems.append(f"changed since import: {source['path']}")
    return problems
