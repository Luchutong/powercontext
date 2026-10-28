"""seekdb access: embedded instance via pylibseekdb (default) or a seekdb/OceanBase server.

Both modes speak the MySQL protocol through PyMySQL so that every statement can use
bound parameters. All context queries go through ``SeekdbStore.query`` which appends
the SQL, parameters and returned rows to ``evidence/seekdb_queries.log``.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

import pymysql
import pymysql.cursors

from sdd_context.config import Settings

SCHEMA_VERSION = 1

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS sdd_meta (
        meta_key VARCHAR(64) NOT NULL PRIMARY KEY,
        meta_value VARCHAR(1024) NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sdd_import (
        import_id BIGINT NOT NULL PRIMARY KEY,
        project VARCHAR(64) NOT NULL,
        root_path VARCHAR(1024) NOT NULL,
        imported_at DATETIME(6) NOT NULL,
        spec_digest CHAR(64) NOT NULL,
        code_digest CHAR(64) NOT NULL,
        test_digest CHAR(64) NOT NULL,
        spec_items INT NOT NULL,
        code_symbols INT NOT NULL,
        test_cases INT NOT NULL,
        added_items VARCHAR(2048) NOT NULL,
        modified_items VARCHAR(2048) NOT NULL,
        removed_items VARCHAR(2048) NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sdd_document (
        project VARCHAR(64) NOT NULL,
        path VARCHAR(512) NOT NULL,
        kind VARCHAR(16) NOT NULL,
        sha256 CHAR(64) NOT NULL,
        line_count INT NOT NULL,
        content LONGTEXT NOT NULL,
        import_id BIGINT NOT NULL,
        PRIMARY KEY (project, path)
    )""",
    """CREATE TABLE IF NOT EXISTS sdd_spec_item (
        project VARCHAR(64) NOT NULL,
        item_id VARCHAR(32) NOT NULL,
        doc_path VARCHAR(512) NOT NULL,
        doc_title VARCHAR(512) NOT NULL,
        component VARCHAR(128) NOT NULL,
        item_type VARCHAR(16) NOT NULL,
        title VARCHAR(512) NOT NULL,
        keywords VARCHAR(1024) NOT NULL,
        body TEXT NOT NULL,
        start_line INT NOT NULL,
        end_line INT NOT NULL,
        sha256 CHAR(64) NOT NULL,
        revision INT NOT NULL,
        import_id BIGINT NOT NULL,
        search_text TEXT NOT NULL,
        PRIMARY KEY (project, item_id),
        FULLTEXT INDEX ft_spec_search (search_text) WITH PARSER ik
    )""",
    """CREATE TABLE IF NOT EXISTS sdd_spec_history (
        project VARCHAR(64) NOT NULL,
        item_id VARCHAR(32) NOT NULL,
        revision INT NOT NULL,
        change_kind VARCHAR(16) NOT NULL,
        sha256 CHAR(64) NOT NULL,
        title VARCHAR(512) NOT NULL,
        body TEXT NOT NULL,
        doc_path VARCHAR(512) NOT NULL,
        import_id BIGINT NOT NULL,
        PRIMARY KEY (project, item_id, revision, change_kind)
    )""",
    """CREATE TABLE IF NOT EXISTS sdd_code_symbol (
        project VARCHAR(64) NOT NULL,
        symbol_id VARCHAR(512) NOT NULL,
        path VARCHAR(512) NOT NULL,
        symbol VARCHAR(256) NOT NULL,
        kind VARCHAR(16) NOT NULL,
        start_line INT NOT NULL,
        end_line INT NOT NULL,
        signature VARCHAR(2048) NOT NULL,
        docstring TEXT NOT NULL,
        source MEDIUMTEXT NOT NULL,
        sha256 CHAR(64) NOT NULL,
        spec_refs VARCHAR(1024) NOT NULL,
        import_id BIGINT NOT NULL,
        search_text MEDIUMTEXT NOT NULL,
        PRIMARY KEY (project, symbol_id),
        FULLTEXT INDEX ft_code_search (search_text) WITH PARSER ik
    )""",
    """CREATE TABLE IF NOT EXISTS sdd_test_case (
        project VARCHAR(64) NOT NULL,
        test_id VARCHAR(512) NOT NULL,
        path VARCHAR(512) NOT NULL,
        name VARCHAR(256) NOT NULL,
        start_line INT NOT NULL,
        end_line INT NOT NULL,
        docstring TEXT NOT NULL,
        source MEDIUMTEXT NOT NULL,
        sha256 CHAR(64) NOT NULL,
        spec_refs VARCHAR(1024) NOT NULL,
        uses VARCHAR(2048) NOT NULL,
        import_id BIGINT NOT NULL,
        search_text MEDIUMTEXT NOT NULL,
        PRIMARY KEY (project, test_id),
        FULLTEXT INDEX ft_test_search (search_text) WITH PARSER ik
    )""",
    """CREATE TABLE IF NOT EXISTS sdd_link (
        project VARCHAR(64) NOT NULL,
        src_id VARCHAR(512) NOT NULL,
        relation VARCHAR(32) NOT NULL,
        dst_id VARCHAR(512) NOT NULL,
        import_id BIGINT NOT NULL,
        PRIMARY KEY (project, src_id, relation, dst_id)
    )""",
]


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _preview(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class SeekdbStore:
    """One open seekdb connection plus a query log."""

    def __init__(self, settings: Settings, actor: str, log_path: Path | None = None) -> None:
        self.settings = settings
        self.actor = actor
        self.log_path = log_path if log_path is not None else settings.queries_log
        self.queries: list[dict[str, Any]] = []
        self._instance = None
        self._conn: pymysql.connections.Connection | None = None
        self.version = ""

    def __enter__(self) -> SeekdbStore:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def open(self) -> None:
        settings = self.settings
        if settings.seekdb_mode == "server":
            self._conn = pymysql.connect(
                host=settings.seekdb_host,
                port=settings.seekdb_port,
                user=settings.seekdb_user,
                password=settings.seekdb_password,
                charset="utf8mb4",
                autocommit=True,
            )
        else:
            import pylibseekdb

            settings.seekdb_dir.mkdir(parents=True, exist_ok=True)
            self._instance = pylibseekdb.open(db_dir=str(settings.seekdb_dir / "seekdb.db"))
            options = self._instance.connection_options()
            self._conn = pymysql.connect(**options, charset="utf8mb4", autocommit=True)
        with self._conn.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{settings.seekdb_database}`")
            cursor.execute("SELECT VERSION()")
            self.version = cursor.fetchone()[0]
        self._conn.select_db(settings.seekdb_database)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._instance is not None:
            self._instance.close()
            self._instance = None

    @property
    def conn(self) -> pymysql.connections.Connection:
        if self._conn is None:
            raise RuntimeError("seekdb store is not open")
        return self._conn

    def ensure_schema(self) -> None:
        with self.conn.cursor() as cursor:
            for statement in SCHEMA:
                cursor.execute(statement)
            cursor.execute(
                "INSERT INTO sdd_meta (meta_key, meta_value) VALUES ('schema_version', %s) "
                "ON DUPLICATE KEY UPDATE meta_value = VALUES(meta_value)",
                (str(SCHEMA_VERSION),),
            )

    def execute(self, sql: str, params: Any = None) -> int:
        with self.conn.cursor() as cursor:
            return cursor.execute(sql, params)

    def executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> int:
        if not rows:
            return 0
        with self.conn.cursor() as cursor:
            return cursor.executemany(sql, rows)

    def fetch(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        """Run a read without logging (bookkeeping reads during ingest)."""
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())

    def query(self, qid: str, purpose: str, sql: str, params: Any = None) -> list[dict[str, Any]]:
        """Run a context query and record it (SQL, parameters, rows) in the query log."""
        started = time.perf_counter()
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            rendered = cursor.mogrify(sql, params)
            cursor.execute(sql, params)
            rows = list(cursor.fetchall())
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        entry = {
            "id": qid,
            "purpose": purpose,
            "sql": " ".join(rendered.split()),
            "rows": len(rows),
            "elapsed_ms": elapsed_ms,
        }
        self.queries.append(entry)
        self._log(entry, rows)
        return rows

    def _log(self, entry: dict[str, Any], rows: list[dict[str, Any]]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"=== {_now()} [{self.actor}] {entry['id']} {entry['purpose']} ===",
            f"engine: {self.version} ({self.settings.seekdb_mode}, database={self.settings.seekdb_database})",
            f"sql: {entry['sql']}",
            f"rows: {entry['rows']} ({entry['elapsed_ms']} ms)",
        ]
        for row in rows[:40]:
            lines.append("  " + _preview({key: value for key, value in row.items() if key not in {"source", "content"}}))
        if len(rows) > 40:
            lines.append(f"  ... {len(rows) - 40} more rows")
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n\n")
