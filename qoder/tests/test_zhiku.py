from pathlib import Path

import pytest

from zhiku.ingest import file_chunks, ingest_dir, split_text
from zhiku.store import Store, keyword_query


def test_split_keeps_short_text_whole():
    assert split_text("第一段\n\n第二段") == ["第一段\n第二段"]


def test_split_long_paragraph_with_overlap():
    chunks = split_text("字" * 1000, size=400, overlap=60)
    assert all(len(c) <= 400 for c in chunks)
    assert sum(len(c) for c in chunks) > 1000  # 有重叠


def test_keyword_query_drops_question_words():
    assert keyword_query("发票什么时候要交给财务？") == "发票 交给财务"
    assert keyword_query("什么") == "什么"  # 全是虚词时保留原句


def test_file_chunks_uses_top_folder_as_tag(tmp_path: Path):
    (tmp_path / "科研").mkdir()
    f = tmp_path / "科研" / "纪要.md"
    f.write_text("实验二由陈浩负责", encoding="utf-8")
    [c] = file_chunks(f, tmp_path)
    assert c["meta"] == {"source": "科研/纪要.md", "title": "纪要", "tag": "科研", "chunk": 0}


@pytest.mark.slow
def test_ingest_and_hybrid_search(tmp_path: Path):
    docs = tmp_path / "docs"
    (docs / "行政").mkdir(parents=True)
    (docs / "科研").mkdir()
    (docs / "行政" / "报销.md").write_text("发票需在当月 25 日前提交给财务。", encoding="utf-8")
    (docs / "科研" / "纪要.md").write_text("实验二的消融分析由陈浩负责。", encoding="utf-8")

    store = Store(path=str(tmp_path / "db"))
    assert ingest_dir(store, docs)["files"] == 2

    hits = store.search("发票什么时候交", k=2)
    assert hits[0].source == "行政/报销.md"
    assert [h.source for h in store.search("负责", tag="科研")] == ["科研/纪要.md"]

    (docs / "科研" / "纪要.md").unlink()
    assert ingest_dir(store, docs)["removed"] == 1
