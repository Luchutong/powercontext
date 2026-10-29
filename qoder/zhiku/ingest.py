"""读取本地资料（Markdown / TXT / PDF），切成适合检索的小段。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

SUPPORTED = {".md", ".markdown", ".txt", ".pdf"}
CHUNK_SIZE = 400  # 每段大约多少字
OVERLAP = 60  # 相邻两段重叠的字数，避免一句话被切断后搜不到


def read_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        return "\n\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    return path.read_text(encoding="utf-8", errors="ignore")


def split_text(text: str, size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list[str]:
    """按段落聚合到 size 左右；单个超长段落再按固定窗口切开。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(p) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            step = size - overlap
            chunks.extend(p[i : i + size] for i in range(0, len(p), step))
            continue
        if len(buf) + len(p) + 1 > size and buf:
            chunks.append(buf)
            buf = buf[-overlap:] + "\n" + p if overlap else p
        else:
            buf = f"{buf}\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return chunks


def file_chunks(path: Path, root: Path) -> list[dict]:
    rel = path.relative_to(root).as_posix()
    parts = Path(rel).parts
    tag = parts[0] if len(parts) > 1 else "未分类"
    text = read_text(path)
    out = []
    for i, chunk in enumerate(split_text(text)):
        out.append(
            {
                "id": hashlib.sha1(f"{rel}#{i}".encode()).hexdigest()[:24],
                "text": chunk,
                "meta": {"source": rel, "title": path.stem, "tag": tag, "chunk": i},
            }
        )
    return out


def iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith("."):
            yield p


def ingest_dir(store, root: str | Path) -> dict:
    root = Path(root).resolve()
    files, chunks, seen = 0, 0, set()
    for p in iter_files(root):
        rel = p.relative_to(root).as_posix()
        chunks += store.replace_source(rel, file_chunks(p, root))
        seen.add(rel)
        files += 1
    # 文件夹里已经删掉的文件，也从索引里删掉
    removed = [f["source"] for f in store.stats()["files"] if f["source"] not in seen]
    for source in removed:
        store.delete_source(source)
    return {"files": files, "chunks": chunks, "removed": len(removed)}
