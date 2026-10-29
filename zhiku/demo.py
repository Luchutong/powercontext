"""把一组示例问题的真实 seekdb 检索结果导出成 JS 文件，供静态项目网站（Qoder Sites）展示。

Qoder Sites 托管的是静态网页，跑不了 Python 和 seekdb，所以网站上的演示
用的是这里预先跑好的真实结果，并在页面上注明。
"""

from __future__ import annotations

import json
from pathlib import Path

DEMO_QUERIES = [
    "发票什么时候要交给财务",
    "实验二的消融分析谁负责，什么时候截止",
    "RAG 里混合检索为什么比纯向量好",
    "项目A 需求评审有哪些遗留问题",
    "B+ 树和 LSM 树的区别",
    "出差住宿标准是多少",
]


def export(store, out: str) -> None:
    data = {
        "stats": store.stats(),
        "queries": [
            {"q": q, "hits": [h.to_dict() for h in store.search(q, k=4)]} for q in DEMO_QUERIES
        ],
    }
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(
        "// 由 `python -m zhiku export-demo` 生成，请勿手改\nwindow.ZHIKU_DEMO = "
        + json.dumps(data, ensure_ascii=False, indent=1)
        + ";\n",
        encoding="utf-8",
    )
