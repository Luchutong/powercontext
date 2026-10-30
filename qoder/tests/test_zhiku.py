from pathlib import Path

import pytest

from zhiku.ingest import file_chunks, ingest_dir, split_text
from zhiku.store import Store, keyword_query
from zhiku.todos import UNSPECIFIED, parse_tasks


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


def test_parse_tasks_extracts_fields_without_guessing():
    text = "\n".join(
        [
            "- [ ] 陈浩：消融实验（10/22）",
            "- [x] 李明：补充显著性检验",
            "- [ ] 整理参考文献",
            "- [ ] 王芳：提交总结，截止 2026-11-01",
            "- [ ] 周磊：错误日期（02/30）",
        ]
    )
    meta = {"source": "科研/纪要.md", "title": "纪要", "tag": "科研", "chunk": 0}
    tasks = parse_tasks([{"text": text, "meta": meta}, {"text": text, "meta": meta}])

    assert len(tasks) == 5
    assert tasks[0]["text"] == "消融实验"
    assert tasks[0]["owner"] == "陈浩"
    assert tasks[0]["due"] == "10/22"
    assert tasks[1]["done"] is True
    assert tasks[2]["owner"] == UNSPECIFIED
    assert tasks[2]["due"] == UNSPECIFIED
    assert tasks[3]["due"] == "2026-11-01"
    assert tasks[3]["text"] == "提交总结"
    assert tasks[4]["due"] == UNSPECIFIED


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


@pytest.mark.slow
def test_todo_sync_filters_persistence_and_delete(tmp_path: Path):
    docs = tmp_path / "docs"
    (docs / "科研").mkdir(parents=True)
    source = docs / "科研" / "纪要.md"
    source.write_text(
        "# 待办\n\n- [ ] 陈浩：消融实验（10/22）\n- [ ] 李明：显著性检验\n- [x] 整理参考文献",
        encoding="utf-8",
    )
    db_path = tmp_path / "db"
    store = Store(path=str(db_path))
    ingest_dir(store, docs)

    result = store.todos.query()
    assert result["owners"] == ["李明", "陈浩", UNSPECIFIED]
    assert len(result["todos"]) == 3
    chen = next(t for t in result["todos"] if t["owner"] == "陈浩")
    assert chen["due"] == "10/22"
    assert chen["source"] == "科研/纪要.md"
    assert [t["text"] for t in store.todos.query(owner="陈浩", status="open", due="10/22")["todos"]] == ["消融实验"]

    store.todos.set_done(chen["id"], True)
    ingest_dir(store, docs)
    assert len(store.todos.query()["todos"]) == 3
    assert store.todos.query(owner="陈浩")["todos"][0]["done"] is True

    restarted = Store(path=str(db_path))
    assert restarted.todos.query(owner="陈浩")["todos"][0]["done"] is True
    restarted.todos.set_done(chen["id"], False)
    ingest_dir(restarted, docs)
    assert restarted.todos.query(owner="陈浩")["todos"][0]["done"] is False

    source.write_text(
        source.read_text(encoding="utf-8")
        .replace("- [ ] 陈浩", "- [x] 陈浩")
        .replace("- [ ] 李明", "- [x] 李明"),
        encoding="utf-8",
    )
    ingest_dir(restarted, docs)
    assert restarted.todos.query(owner="陈浩")["todos"][0]["done"] is False
    assert restarted.todos.query(owner="李明")["todos"][0]["done"] is True

    source.unlink()
    ingest_dir(restarted, docs)
    assert restarted.todos.query()["todos"] == []
