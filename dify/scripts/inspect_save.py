"""Inspect a demo passport's actual PowerContext save, without printing credentials."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    from dotenv import dotenv_values
    from app.server import PowerContext
    parser = argparse.ArgumentParser()
    parser.add_argument("--passport", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = dotenv_values(ROOT / ".runtime/app.env")
    client = PowerContext(config.get("POWERCONTEXT_URL", "http://127.0.0.1:18380"), config.get("POWERCONTEXT_TOKEN", ""))
    scope_id = client.scope(args.passport)
    identity, revision, etag, state = client.load(scope_id)
    result = {"scope_id": scope_id, "artifact_id": identity, "revision": revision, "etag": etag, "state": state}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
