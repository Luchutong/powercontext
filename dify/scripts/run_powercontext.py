"""Run a fresh, project-local PowerContext service; never touch another installation."""
import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18380)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--data", default=str(Path(__file__).parents[1] / ".runtime/powercontext"))
    args = parser.parse_args()
    data = Path(args.data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    os.environ["POWERCONTEXT_HOME"] = str(data)
    os.environ["POWERCONTEXT_SERVER_DATABASE_KIND"] = "sqlite"
    os.environ["POWERCONTEXT_SERVER_DATABASE_URL"] = "sqlite+aiosqlite:///" + (data / "memory.db").as_posix()
    os.environ["POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_ROLE"] = "all"
    os.environ["POWERCONTEXT_SERVER_DASHBOARD_ENABLED"] = "true"
    import sys
    from importlib.metadata import entry_points
    sys.argv = ["powercontext", "server", "run", "--host", args.host, "--port", str(args.port), "--no-env-file"]
    entry_points(group="console_scripts")["powercontext"].load()()


if __name__ == "__main__":
    main()
