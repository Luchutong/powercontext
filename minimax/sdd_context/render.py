"""Human-readable Markdown view of a context JSON document."""

from __future__ import annotations

from typing import Any


def render_context(context: dict[str, Any]) -> str:
    task = context["task"]
    lines = [
        f"# 开发上下文：{task['id']} {task['title']}",
        "",
        f"- 生成时间：{context['generated_at']}",
        f"- 资料版本：seekdb import #{context['import']['import_id']}（spec_digest `{context['import']['spec_digest'][:12]}`）",
        f"- 检索引擎：{context['retrieval']['engine']}（{context['retrieval']['mode']}，库 `{context['retrieval']['database']}`）",
        f"- 选中组件：{', '.join(context['selection']['components'])}",
        "",
        "## 适用规格条目",
        "",
        "| 编号 | 类型 | 标题 | 来源 | rev | 本次导入变化 | 测试 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for kind in ("change", "preserve"):
        for entry in context["specs"][kind]:
            lines.append(
                f"| {entry['id']} | {'变更' if kind == 'change' else '保持'} | {entry['title']} | `{entry['source']}` | "
                f"{entry['revision']} | {'是' if entry['changed_in_latest_import'] else '否'} | {len(entry['tests'])} |"
            )
    lines += ["", "## 需要修改的代码文件与符号", "", "| 角色 | 符号 | 位置 | 理由 |", "| --- | --- | --- | --- |"]
    for target in context["code_targets"]:
        role = "修改" if target["role"] == "modify" else "相关"
        lines.append(f"| {role} | `{target['symbol_id']}` | `{target['source']}` | {'；'.join(target['reasons'])} |")
    lines += ["", "## 验证测试", "", "| 用途 | 测试 | 位置 | 验证规格 |", "| --- | --- | --- | --- |"]
    for label, group in (("目标（功能）", "target"), ("回归（兼容）", "regression")):
        for test in context["tests"][group]:
            lines.append(f"| {label} | `{test['id']}` | `{test['source']}` | {', '.join(test['verifies'])} |")
    lines += ["", "运行命令（在样例工程根目录）：", ""]
    for name, command in context["tests"]["commands"].items():
        lines.append(f"- {name}: `{command}`")
    state = context.get("state") or {}
    lines += ["", "## 任务状态（PowerContext）", "", f"- 状态：{state.get('status', 'unknown')}；Scope：`{state.get('scope_id')}`"]
    handoff = state.get("handoff") or {}
    if handoff:
        ref = handoff.get("reference") or {}
        lines.append(f"- 交接：`{ref.get('family')}/{ref.get('artifact_id')}@{ref.get('revision')}`（{handoff.get('disposition')}）")
    lines.append("- 已完成：" + ("；".join(f"[{d['kind']}:{d['step']}] {d['text']}" for d in state.get("done", [])) or "无"))
    lines.append("- 待办：" + ("；".join(f"[{t['step']}] {t['text']}" for t in state.get("todo", [])) or "无"))
    for record in state.get("verification", []):
        suites = "，".join(f"{name} {s['passed']}/{s['total']}" for name, s in (record.get("suites") or {}).items())
        lines.append(
            f"- 已有验证（{record.get('phase')}，{record.get('recorded_at')}）：{suites}；"
            f"仍然有效：{'是' if record.get('still_valid') else '否'}"
            + (f"；验证后变更的规格：{', '.join(record['spec_changed_since'])}" if record.get("spec_changed_since") else "")
            + (f"；验证后新增的规格：{', '.join(record['spec_added_since'])}" if record.get("spec_added_since") else "")
        )
    if context.get("coverage_gaps"):
        lines.append("- 缺少测试的变更条目：" + ", ".join(context["coverage_gaps"]))
    lines += ["", "## seekdb 查询", "", "| 查询 | 用途 | 行数 |", "| --- | --- | --- |"]
    for query in context["retrieval"]["queries"]:
        lines.append(f"| {query['id']} | {query['purpose']} | {query['rows']} |")
    lines += ["", f"完整 SQL 与返回行见 `{context['retrieval']['query_log']}`。", ""]
    lines += ["## 规格正文", ""]
    for kind in ("change", "preserve"):
        for entry in context["specs"][kind]:
            lines += [f"### {entry['id']} {entry['title']}", "", f"来源：`{entry['source']}`（sha256 `{entry['sha256'][:12]}`）", "", entry["text"], ""]
    return "\n".join(lines)
