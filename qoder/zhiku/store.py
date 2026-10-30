"""seekdb 存储层：把文档切片写入 seekdb，并用「全文 + 向量」混合检索找回来。

seekdb 以嵌入式模式运行（数据就是本地的一个目录），不需要单独部署数据库服务。
- 全文索引默认使用 IK 中文分词，负责关键词精确命中；
- 向量索引默认使用中文 embedding 模型 bge-small-zh（本地 ONNX 推理），负责语义相近的召回；
- 两路结果用 RRF（Reciprocal Rank Fusion）融合排序。

通过环境变量 ZHIKU_EMBEDDING 切换向量模型：
- bge-zh（默认）：本地运行，首次使用会从 HF_ENDPOINT（默认 hf-mirror.com）下载约 95MB 模型
- qwen：调用阿里云百炼 text-embedding-v4，需要设置 DASHSCOPE_API_KEY
- default：pyseekdb 自带的英文模型 all-MiniLM-L6-v2
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass

import pyseekdb

from .todos import TodoStore

COLLECTION = "zhiku_chunks"
DEFAULT_DB_PATH = os.environ.get("ZHIKU_DB", "./zhiku_data")
BATCH = 64

# 问句里的虚词对关键词检索没有帮助，反而会让几乎所有段落都"命中"
_STOPWORDS = re.compile(
    r"什么时候|什么|怎么样|怎么|如何|为什么|哪些|哪个|多少|是否|有没有|吗|呢|吧|啊|"
    r"的|了|是|和|与|及|在|要|谁|请问|一下|[\s，。？！、；：,.?!;:()（）“”\"']+"
)


def keyword_query(q: str) -> str:
    kept = " ".join(t for t in _STOPWORDS.split(q) if t)
    return kept or q


def make_embedding_function():
    kind = os.environ.get("ZHIKU_EMBEDDING", "bge-zh")
    if kind == "default":
        return pyseekdb.DefaultEmbeddingFunction()
    if kind == "qwen":
        from pyseekdb.utils.embedding_functions.qwen_embedding_function import (
            QwenEmbeddingFunction,
        )

        return QwenEmbeddingFunction(model_name="text-embedding-v4")
    from pyseekdb.utils.embedding_functions.onnx_embedding_function import (
        OnnxEmbeddingFunction,
    )

    return OnnxEmbeddingFunction(
        model_name="bge-small-zh-v1.5", hf_model_id="Xenova/bge-small-zh-v1.5", dimension=512
    )


@dataclass
class Hit:
    id: str
    text: str
    source: str
    title: str
    tag: str
    chunk: int
    score: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or DEFAULT_DB_PATH
        self.client = pyseekdb.Client(path=self.path)
        self._lock = threading.RLock()
        self.col = self.client.get_or_create_collection(
            COLLECTION, embedding_function=make_embedding_function()
        )
        self.todos = TodoStore(self.client, self._lock)
        for source in self.stats()["files"]:
            self.todos.sync_source(source["source"], self.source_chunks(source["source"]))

    # ---------- 写入 ----------
    def replace_source(self, source: str, chunks: list[dict]) -> int:
        """用新切片整体替换某个文件之前的切片，返回写入条数。"""
        with self._lock:
            self.col.delete(where={"source": {"$eq": source}})
            for i in range(0, len(chunks), BATCH):
                batch = chunks[i : i + BATCH]
                self.col.upsert(
                    ids=[c["id"] for c in batch],
                    documents=[c["text"] for c in batch],
                    metadatas=[c["meta"] for c in batch],
                )
            self.todos.sync_source(source, self.source_chunks(source))
        return len(chunks)

    def delete_source(self, source: str) -> None:
        with self._lock:
            self.col.delete(where={"source": {"$eq": source}})
            self.todos.delete_source(source)

    # ---------- 读取 ----------
    def source_chunks(self, source: str) -> list[dict]:
        chunks: list[dict] = []
        offset = 0
        while True:
            res = self.col.get(
                where={"source": {"$eq": source}},
                limit=100,
                offset=offset,
                include=["documents", "metadatas"],
            )
            ids = res.get("ids") or []
            documents = res.get("documents") or []
            metadatas = res.get("metadatas") or []
            chunks.extend(
                {"id": id_, "text": text, "meta": meta}
                for id_, text, meta in zip(ids, documents, metadatas)
            )
            if len(ids) < 100:
                break
            offset += len(ids)
        return sorted(chunks, key=lambda c: c["meta"].get("chunk", 0))

    def search(self, q: str, k: int = 8, tag: str | None = None) -> list[Hit]:
        q = q.strip()
        if not q:
            return []
        where = {"tag": {"$eq": tag}} if tag else None
        fulltext: dict = {"where_document": {"$contains": keyword_query(q)}, "n_results": k * 3}
        knn: dict = {"query_texts": [q], "n_results": k * 3}
        if where:
            fulltext["where"] = where
            knn["where"] = where
        res = self.col.hybrid_search(
            query=fulltext,
            knn=knn,
            rank={"rrf": {}},
            n_results=k,
            include=["documents", "metadatas"],
        )
        return _hits(res)

    # 下面两个只走单一路径，用于验证样例里对比「全文 / 向量 / 混合」三种结果
    def search_fulltext(self, q: str, k: int = 3) -> list[Hit]:
        res = self.col.hybrid_search(
            query={"where_document": {"$contains": keyword_query(q)}, "n_results": k},
            n_results=k,
            include=["documents", "metadatas"],
        )
        return _hits(res)

    def search_vector(self, q: str, k: int = 3) -> list[Hit]:
        res = self.col.query(query_texts=[q], n_results=k, include=["documents", "metadatas"])
        return _hits(res)

    def stats(self) -> dict:
        res = self.col.get(include=["metadatas"])
        sources: dict[str, dict] = {}
        for meta in res.get("metadatas") or []:
            s = sources.setdefault(
                meta["source"], {"source": meta["source"], "tag": meta.get("tag", ""), "chunks": 0}
            )
            s["chunks"] += 1
        tags = sorted({s["tag"] for s in sources.values()})
        return {
            "chunks": sum(s["chunks"] for s in sources.values()),
            "files": sorted(sources.values(), key=lambda s: s["source"]),
            "tags": tags,
        }


def _hits(res: dict) -> list[Hit]:
    return [
        Hit(
            id=id_,
            text=doc,
            source=meta.get("source", ""),
            title=meta.get("title", ""),
            tag=meta.get("tag", ""),
            chunk=int(meta.get("chunk", 0)),
            score=round(float(score), 4),
        )
        for id_, doc, meta, score in zip(
            res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]
        )
    ]
