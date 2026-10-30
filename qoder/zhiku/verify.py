"""验证样例：导入示例资料，跑一组固定问题，检查结果并生成 seekdb 检索记录。

运行：python -m zhiku verify
输出：终端里的 PASS / FAIL，以及 docs/verify-log.md（每个问题在全文、向量、混合三种检索下的结果）。
"""

from __future__ import annotations

import time
from pathlib import Path

from .ingest import ingest_dir
from .todos import UNSPECIFIED

# (问题, 期望出现在前 3 名的文件, 类别筛选)
CASES = [
    ("发票什么时候要交给财务", "行政/报销与差旅规定.md", None),
    ("实验二的消融分析谁负责，什么时候截止", "科研/组会纪要-1015.md", None),
    ("RAG 里混合检索为什么比纯向量好", "科研/论文笔记-RAG混合检索.md", None),
    ("项目A 需求评审有哪些遗留问题", "工作/项目A-需求评审.md", None),
    ("B+ 树和 LSM 树的区别", "学习/数据库课程笔记.md", None),
    ("出差住宿标准是多少", "行政/报销与差旅规定.md", None),
    ("谁负责", "科研/组会纪要-1015.md", "科研"),
]


def _fmt(hits) -> str:
    return "<br>".join(f"{i + 1}. {h.source}（{h.score}）" for i, h in enumerate(hits)) or "（无结果）"


def _verify_todos(store, docs_dir: str) -> tuple[bool, list[str], list[dict]]:
    initial = store.todos.query()["todos"]
    by_owner = {t["owner"]: t for t in initial}
    chen = by_owner.get("陈浩")
    extract_ok = (
        len(initial) == 3
        and chen is not None
        and chen["due"] == "10/22"
        and chen["source"] == "科研/组会纪要-1015.md"
        and by_owner.get("李明", {}).get("due") == UNSPECIFIED
        and by_owner.get("张悦", {}).get("due") == UNSPECIFIED
    )
    if not chen:
        return False, ["初次提取：FAIL（未找到陈浩任务）"], initial

    original_done = chen["done"]
    target_done = not original_done
    store.todos.set_done(chen["id"], target_done)
    ingest_dir(store, docs_dir)
    after_sync = store.todos.query(owner="陈浩")["todos"]
    sync_ok = len(store.todos.query()["todos"]) == 3 and len(after_sync) == 1 and after_sync[0]["done"] == target_done

    from .store import Store

    restarted = Store(path=store.path)
    persisted = restarted.todos.query(owner="陈浩")["todos"]
    restart_ok = len(persisted) == 1 and persisted[0]["done"] == target_done
    filtered = restarted.todos.query(
        owner="陈浩", status="done" if target_done else "open", due="10/22"
    )["todos"]
    filter_ok = len(filtered) == 1

    restarted.todos.set_done(chen["id"], original_done)
    ingest_dir(restarted, docs_dir)
    final = restarted.todos.query()["todos"]
    lines = [
        f"初次提取、缺失字段及出处：{'PASS' if extract_ok else 'FAIL'}",
        f"修改状态并重新导入（不重复、保留状态）：{'PASS' if sync_ok else 'FAIL'}",
        f"新建 Store 后读取持久化状态：{'PASS' if restart_ok else 'FAIL'}",
        f"负责人 + 状态 + 截止日期组合筛选：{'PASS' if filter_ok else 'FAIL'}",
    ]
    return extract_ok and sync_ok and restart_ok and filter_ok, lines, final


def run(store, docs_dir: str, log_path: str) -> bool:
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    summary = ingest_dir(store, docs_dir)
    print(f"导入完成：{summary}")

    rows, all_ok = [], True
    for q, expected, tag in CASES:
        hybrid = store.search(q, k=3, tag=tag)
        sources = [h.source for h in hybrid]
        ok = expected in sources and all(not tag or h.tag == tag for h in hybrid)
        all_ok &= ok
        rank = sources.index(expected) + 1 if expected in sources else "-"
        label = f"{q}（类别：{tag}）" if tag else q
        print(f"[{'PASS' if ok else 'FAIL'}] {label} → 期望 {expected}，实际排名 {rank}")
        rows.append(
            f"| {'✅' if ok else '❌'} | {label} | {expected}（第 {rank} 名） "
            f"| {_fmt(store.search_fulltext(q))} | {_fmt(store.search_vector(q))} | {_fmt(hybrid)} |"
        )

    todo_ok, todo_lines, todos = _verify_todos(store, docs_dir)
    all_ok &= todo_ok
    for line in todo_lines:
        print(f"[待办] {line}")

    stats = store.stats()
    todo_rows = [
        f"| {t['text']} | {t['owner']} | {t['due']} | {'已完成' if t['done'] else '未完成'} | {t['source']}（第 {t['chunk'] + 1} 段） |"
        for t in todos
    ]
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    Path(log_path).write_text(
        "\n".join(
            [
                "# 验证记录：seekdb 检索结果",
                "",
                "由 `python -m zhiku verify` 自动生成，请勿手改。",
                "",
                f"- 运行时间：{started}",
                f"- 示例资料：`{docs_dir}`，共 {len(stats['files'])} 个文件、{stats['chunks']} 个片段（存储在 seekdb 集合 `zhiku_chunks`）",
                f"- seekdb 数据目录：`{store.path}`",
                f"- 结论：{'全部通过' if all_ok else '存在未通过的样例'}",
                "",
                "判定标准：期望文件出现在混合检索前 3 名；带类别的样例，结果必须全部属于该类别。",
                "全文列是 seekdb 全文索引（IK 分词）的结果，向量列是向量索引的结果（数字为距离，越小越近），",
                "混合列是两路经 RRF 融合后的最终结果（数字为 RRF 分数，越大越相关），也就是知库界面上展示的结果。",
                "",
                "| 结果 | 问题 | 期望 | 全文检索 | 向量检索 | 混合检索（最终） |",
                "| --- | --- | --- | --- | --- | --- |",
                *rows,
                "",
                "## 纪要待办端到端验证",
                "",
                *todo_lines,
                "",
                "| 任务 | 负责人 | 截止日期 | 状态 | 出处 |",
                "| --- | --- | --- | --- | --- |",
                *todo_rows,
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"检索记录已写入 {log_path}")
    return all_ok
