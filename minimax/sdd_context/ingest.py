"""Parse specs, code and tests of a project and store them in seekdb.

Specs are Markdown files whose items start with ``## <ID> <title>`` (for example
``## TAG-001 按标签过滤任务``) followed by ``- 类型:`` / ``- 关键词:`` metadata lines.
Code and tests are Python files chunked per top-level function/class with ``ast``.
Links stored in ``sdd_link``:

* ``test --verifies--> spec item``   from ``@pytest.mark.spec("TAG-001", ...)``
* ``code --implements--> spec item`` from spec IDs mentioned in docstrings/comments
* ``test --uses--> code symbol``     from project symbols referenced in a test body
"""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sdd_context.seekdb_store import SeekdbStore

SPEC_ID = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{3})\b")
ITEM_HEADING = re.compile(r"^##\s+([A-Z][A-Z0-9]{1,9}-\d{3})\s+(.+?)\s*$")
META_LINE = re.compile(r"^-\s*([^:：]{1,16})\s*[:：]\s*(.+?)\s*$")
CHANGE_TYPES = {"变更": "change", "新增": "change", "change": "change", "保持": "preserve", "preserve": "preserve"}
WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_files(contents: dict[str, str]) -> str:
    joined = "\n".join(f"{path}\0{sha256_text(text)}" for path, text in sorted(contents.items()))
    return sha256_text(joined)


def split_identifier(name: str) -> str:
    return " ".join(part for part in re.split(r"[_\W]+", name) if part)


@dataclass
class SpecItem:
    item_id: str
    title: str
    doc_path: str
    doc_title: str
    component: str
    item_type: str
    keywords: str
    body: str
    start_line: int
    end_line: int

    @property
    def sha256(self) -> str:
        return sha256_text(f"{self.item_id}\n{self.title}\n{self.item_type}\n{self.keywords}\n{self.body}")

    @property
    def search_text(self) -> str:
        return f"{self.item_id} {self.title} {self.component} {self.keywords} {self.body}"


@dataclass
class Symbol:
    symbol_id: str
    path: str
    symbol: str
    kind: str
    start_line: int
    end_line: int
    signature: str
    docstring: str
    source: str
    spec_refs: list[str]
    names: set[str] = field(default_factory=set)

    @property
    def sha256(self) -> str:
        return sha256_text(self.source)


def parse_spec(rel_path: str, text: str) -> list[SpecItem]:
    lines = text.splitlines()
    doc_title = ""
    doc_meta: dict[str, str] = {}
    items: list[SpecItem] = []
    current: dict[str, Any] | None = None

    def finish(end_index: int) -> None:
        if current is None:
            return
        body_lines = [line for line in current["body"] if line.strip()]
        last = end_index
        while last > current["start"] and not lines[last - 1].strip():
            last -= 1
        items.append(
            SpecItem(
                item_id=current["id"],
                title=current["title"],
                doc_path=rel_path,
                doc_title=doc_title,
                component=current["meta"].get("组件", doc_meta.get("组件", "")),
                item_type=CHANGE_TYPES.get(current["meta"].get("类型", "保持"), "preserve"),
                keywords=current["meta"].get("关键词", ""),
                body="\n".join(body_lines).strip(),
                start_line=current["start"],
                end_line=last,
            )
        )

    for index, line in enumerate(lines, start=1):
        if line.startswith("# ") and not doc_title:
            doc_title = line[2:].strip()
            continue
        heading = ITEM_HEADING.match(line)
        if heading:
            finish(index - 1)
            current = {"id": heading.group(1), "title": heading.group(2), "start": index, "meta": {}, "body": []}
            continue
        if line.startswith("## "):
            finish(index - 1)
            current = None
            continue
        meta = META_LINE.match(line.strip())
        if current is None:
            if meta:
                doc_meta[meta.group(1).strip()] = meta.group(2).strip()
            continue
        if meta and not current["body"]:
            current["meta"][meta.group(1).strip()] = meta.group(2).strip()
        else:
            current["body"].append(line)
    finish(len(lines))
    return items


def _decorator_spec_refs(node: ast.AST) -> list[str]:
    refs: list[str] = []
    for decorator in getattr(node, "decorator_list", []):
        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "spec":
            refs.extend(arg.value for arg in decorator.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str))
    return refs


def _names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
        elif isinstance(child, ast.arg):
            names.add(child.arg)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str) and len(child.value) <= 40:
            names.update(WORD.findall(child.value))
    return names


def _signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(base) for base in node.bases)
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"


def parse_python(rel_path: str, text: str, *, tests: bool) -> list[Symbol]:
    tree = ast.parse(text, filename=rel_path)
    lines = text.splitlines()
    symbols: list[Symbol] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if tests and not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")):
            continue
        start = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
        end = node.end_lineno or node.lineno
        source = "\n".join(lines[start - 1 : end])
        docstring = ast.get_docstring(node) or ""
        if tests:
            refs = _decorator_spec_refs(node)
        else:
            comments = " ".join(line.split("#", 1)[1] for line in source.splitlines() if "#" in line)
            refs = SPEC_ID.findall(f"{docstring} {comments}")
        symbols.append(
            Symbol(
                symbol_id=f"{rel_path}::{node.name}",
                path=rel_path,
                symbol=node.name,
                kind="class" if isinstance(node, ast.ClassDef) else "function",
                start_line=start,
                end_line=end,
                signature=_signature(node),
                docstring=docstring,
                source=source,
                spec_refs=sorted(dict.fromkeys(refs)),
                names=_names(node),
            )
        )
    return symbols


def symbol_search_text(symbol: Symbol) -> str:
    words = sorted(name for name in symbol.names if len(name) > 1)
    return (
        f"{symbol.path} {symbol.symbol} {split_identifier(symbol.symbol)} {symbol.signature} "
        f"{symbol.docstring} {' '.join(split_identifier(word) for word in words)}"
    )


@dataclass
class ProjectFiles:
    specs: dict[str, str]
    code: dict[str, str]
    tests: dict[str, str]
    support: dict[str, str]


def collect_project(root: Path) -> ProjectFiles:
    def read(paths: list[Path]) -> dict[str, str]:
        return {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8") for path in sorted(paths)}

    specs = read(list((root / "specs").rglob("*.md")))
    tests_dir = root / "tests"
    tests = read([path for path in tests_dir.rglob("test_*.py")])
    support = read([path for path in tests_dir.rglob("conftest.py")])
    code = read(
        [
            path
            for path in root.rglob("*.py")
            if tests_dir not in path.parents and not any(part.startswith(".") for part in path.relative_to(root).parts)
        ]
    )
    return ProjectFiles(specs=specs, code=code, tests=tests, support=support)


def ingest_project(store: SeekdbStore, project: str, root: Path) -> dict[str, Any]:
    """(Re)import specs, code and tests; spec items keep a revision history across imports."""
    files = collect_project(root)
    spec_items = [item for rel, text in files.specs.items() for item in parse_spec(rel, text)]
    ids = [item.item_id for item in spec_items]
    duplicates = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
    if duplicates:
        raise ValueError(f"duplicate spec item ids: {', '.join(duplicates)}")
    code_symbols = [symbol for rel, text in files.code.items() for symbol in parse_python(rel, text, tests=False)]
    test_cases = [symbol for rel, text in files.tests.items() for symbol in parse_python(rel, text, tests=True)]

    previous = {row["item_id"]: row for row in store.fetch(
        "SELECT item_id, sha256, revision FROM sdd_spec_item WHERE project = %s", (project,)
    )}
    added, modified, unchanged = [], [], []
    revisions: dict[str, int] = {}
    for item in spec_items:
        old = previous.get(item.item_id)
        if old is None:
            added.append(item.item_id)
            revisions[item.item_id] = 1
        elif old["sha256"] != item.sha256:
            modified.append(item.item_id)
            revisions[item.item_id] = int(old["revision"]) + 1
        else:
            unchanged.append(item.item_id)
            revisions[item.item_id] = int(old["revision"])
    removed = sorted(set(previous) - set(ids))

    conn = store.conn
    conn.begin()
    try:
        # Explicit ids: seekdb/OceanBase AUTO_INCREMENT values jump after an instance restart.
        import_id = int(store.fetch("SELECT COALESCE(MAX(import_id), 0) + 1 AS next_id FROM sdd_import")[0]["next_id"])
        store.execute(
            "INSERT INTO sdd_import (import_id, project, root_path, imported_at, spec_digest, code_digest, test_digest, "
            "spec_items, code_symbols, test_cases, added_items, modified_items, removed_items) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                import_id,
                project,
                str(root),
                dt.datetime.now(dt.UTC).replace(tzinfo=None),
                digest_files(files.specs),
                digest_files(files.code),
                digest_files(files.tests),
                len(spec_items),
                len(code_symbols),
                len(test_cases),
                ",".join(added),
                ",".join(modified),
                ",".join(removed),
            ),
        )

        for table in ("sdd_document", "sdd_spec_item", "sdd_code_symbol", "sdd_test_case", "sdd_link"):
            store.execute(f"DELETE FROM {table} WHERE project = %s", (project,))

        documents = [
            (project, rel, kind, sha256_text(text), len(text.splitlines()), text, import_id)
            for kind, group in (("spec", files.specs), ("code", files.code), ("test", files.tests), ("test-support", files.support))
            for rel, text in group.items()
        ]
        store.executemany(
            "INSERT INTO sdd_document (project, path, kind, sha256, line_count, content, import_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            documents,
        )
        store.executemany(
            "INSERT INTO sdd_spec_item (project, item_id, doc_path, doc_title, component, item_type, title, keywords, body, "
            "start_line, end_line, sha256, revision, import_id, search_text) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (project, item.item_id, item.doc_path, item.doc_title, item.component, item.item_type, item.title,
                 item.keywords, item.body, item.start_line, item.end_line, item.sha256, revisions[item.item_id],
                 import_id, item.search_text)
                for item in spec_items
            ],
        )
        history = [
            (project, item.item_id, revisions[item.item_id], "added" if item.item_id in added else "modified",
             item.sha256, item.title, item.body, item.doc_path, import_id)
            for item in spec_items
            if item.item_id in added or item.item_id in modified
        ]
        history += [
            (project, item_id, int(previous[item_id]["revision"]), "removed", previous[item_id]["sha256"], "", "", "", import_id)
            for item_id in removed
        ]
        store.executemany(
            "INSERT INTO sdd_spec_history (project, item_id, revision, change_kind, sha256, title, body, doc_path, import_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE sha256 = VALUES(sha256), body = VALUES(body), import_id = VALUES(import_id)",
            history,
        )
        store.executemany(
            "INSERT INTO sdd_code_symbol (project, symbol_id, path, symbol, kind, start_line, end_line, signature, docstring, "
            "source, sha256, spec_refs, import_id, search_text) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (project, s.symbol_id, s.path, s.symbol, s.kind, s.start_line, s.end_line, s.signature, s.docstring,
                 s.source, s.sha256, ",".join(s.spec_refs), import_id, symbol_search_text(s))
                for s in code_symbols
            ],
        )
        project_symbols = {symbol.symbol: symbol.symbol_id for symbol in code_symbols}
        test_rows, links = [], set()
        for test in test_cases:
            uses = sorted(project_symbols[name] for name in test.names if name in project_symbols)
            test_rows.append(
                (project, test.symbol_id, test.path, test.symbol, test.start_line, test.end_line, test.docstring,
                 test.source, test.sha256, ",".join(test.spec_refs), ",".join(uses), import_id,
                 f"{test.symbol_id} {split_identifier(test.symbol)} {test.docstring} {' '.join(test.spec_refs)}")
            )
            links.update((project, test.symbol_id, "verifies", ref, import_id) for ref in test.spec_refs)
            links.update((project, test.symbol_id, "uses", symbol_id, import_id) for symbol_id in uses)
        for symbol in code_symbols:
            links.update((project, symbol.symbol_id, "implements", ref, import_id) for ref in symbol.spec_refs if ref in revisions)
        store.executemany(
            "INSERT INTO sdd_test_case (project, test_id, path, name, start_line, end_line, docstring, source, sha256, "
            "spec_refs, uses, import_id, search_text) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            test_rows,
        )
        store.executemany(
            "INSERT INTO sdd_link (project, src_id, relation, dst_id, import_id) VALUES (%s, %s, %s, %s, %s)",
            sorted(links),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    counts = store.fetch(
        "SELECT (SELECT COUNT(*) FROM sdd_spec_item WHERE project = %s) AS spec_items, "
        "(SELECT COUNT(*) FROM sdd_code_symbol WHERE project = %s) AS code_symbols, "
        "(SELECT COUNT(*) FROM sdd_test_case WHERE project = %s) AS test_cases, "
        "(SELECT COUNT(*) FROM sdd_link WHERE project = %s) AS links, "
        "(SELECT COUNT(*) FROM sdd_document WHERE project = %s) AS documents",
        (project,) * 5,
    )[0]
    return {
        "import_id": import_id,
        "project": project,
        "root": str(root),
        "stored": {key: int(value) for key, value in counts.items()},
        "spec_changes": {"added": added if previous else [], "modified": modified, "removed": removed,
                         "unchanged": len(unchanged)},
        "first_import": not previous,
    }
