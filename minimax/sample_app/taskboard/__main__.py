"""Run the task board: ``python -m taskboard serve`` or ``python -m taskboard list --status open``."""

from __future__ import annotations

import argparse
import json
import sys
from wsgiref.simple_server import make_server

from taskboard.api import make_app
from taskboard.repository import load_tasks
from taskboard.service import QueryError, search_tasks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="taskboard")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    listing = commands.add_parser("list", help="print GET /tasks results for query parameters")
    listing.add_argument("params", nargs="*", metavar="KEY=VALUE")
    args = parser.parse_args(argv)

    if args.command == "serve":
        with make_server(args.host, args.port, make_app()) as server:
            print(f"taskboard listening on http://{args.host}:{args.port}/tasks", flush=True)
            server.serve_forever()
        return 0

    params = dict(item.split("=", 1) for item in args.params)
    try:
        result = search_tasks(load_tasks(), params)
    except QueryError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
