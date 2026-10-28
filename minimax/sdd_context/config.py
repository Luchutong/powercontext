"""Paths and settings, overridable through environment variables (see .env.example)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
MINIMAX_DIR = PACKAGE_DIR.parent


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines without overriding variables that are already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def _path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default


@dataclass(frozen=True)
class Settings:
    minimax_dir: Path
    template_dir: Path
    runtime_dir: Path
    project_dir: Path
    evidence_dir: Path
    task_file: Path
    seekdb_dir: Path
    seekdb_database: str
    seekdb_host: str | None
    seekdb_port: int
    seekdb_user: str
    seekdb_password: str
    pc_home: Path
    pc_host: str
    pc_port: int
    project: str

    @property
    def pc_url(self) -> str:
        return f"http://{self.pc_host}:{self.pc_port}"

    @property
    def seekdb_mode(self) -> str:
        return "server" if self.seekdb_host else "embedded"

    @property
    def queries_log(self) -> Path:
        return self.evidence_dir / "seekdb_queries.log"


def load_settings() -> Settings:
    load_dotenv(MINIMAX_DIR / ".env")
    runtime = _path("SDD_RUNTIME_DIR", MINIMAX_DIR / ".runtime")
    return Settings(
        minimax_dir=MINIMAX_DIR,
        template_dir=_path("SDD_TEMPLATE_DIR", MINIMAX_DIR / "sample_app"),
        runtime_dir=runtime,
        project_dir=_path("SDD_PROJECT_DIR", runtime / "workspace" / "sample_app"),
        evidence_dir=_path("SDD_EVIDENCE_DIR", MINIMAX_DIR / "evidence"),
        task_file=_path("SDD_TASK_FILE", MINIMAX_DIR / "sample_task" / "task.toml"),
        seekdb_dir=_path("SDD_SEEKDB_DIR", runtime / "seekdb"),
        seekdb_database=os.environ.get("SDD_SEEKDB_DATABASE", "sdd_context"),
        seekdb_host=os.environ.get("SDD_SEEKDB_HOST") or None,
        seekdb_port=int(os.environ.get("SDD_SEEKDB_PORT", "2881")),
        seekdb_user=os.environ.get("SDD_SEEKDB_USER", "root"),
        seekdb_password=os.environ.get("SDD_SEEKDB_PASSWORD", ""),
        pc_home=_path("SDD_PC_HOME", runtime / "powercontext"),
        pc_host="127.0.0.1",
        pc_port=int(os.environ.get("SDD_PC_PORT", "17439")),
        project=os.environ.get("SDD_PROJECT_NAME", "taskboard"),
    )


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    description: str
    project: str
    path: Path

    @property
    def query_text(self) -> str:
        return f"{self.title}\n{self.description}"


def load_task(path: Path) -> Task:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return Task(
        id=data["id"],
        title=data["title"],
        description="".join(line.strip() for line in data["description"].splitlines()),
        project=data.get("project", "taskboard"),
        path=path,
    )
