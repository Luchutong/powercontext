"""Produce reproducible real-memory evidence; this is not a Dify execution claim."""
import argparse
import json
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dotenv import dotenv_values
    from fastapi.testclient import TestClient
    from app.server import PowerContext, create_app
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "evidence/memory-verification.json")
    args = parser.parse_args()
    config = dotenv_values(ROOT / ".runtime/app.env")
    pc_url = config.get("POWERCONTEXT_URL", "http://127.0.0.1:18380")
    pc = PowerContext(pc_url, config.get("POWERCONTEXT_TOKEN", ""), ROOT / ".runtime/verification-pc-audit.jsonl")
    client = TestClient(create_app(pc, config["APP_TOKEN"], config["TICKET_SECRET"]))
    headers = {"Authorization": "Bearer " + config["APP_TOKEN"]}
    passport = "judge-" + secrets.token_hex(8)
    evidence = {"recorded_at": datetime.now(timezone.utc).isoformat(), "kind": "real PowerContext HTTP + gateway TestClient",
                "dify_executed": False, "model_executed": False, "demo_passport": passport, "steps": []}

    def prepare(command, event):
        response = client.post("/game/prepare", headers=headers, json={"passport": passport, "command": command, "event_id": event})
        response.raise_for_status()
        return response.json()

    initial = prepare("ARCHIVE", "initial")
    assert initial["state"]["turn"] == 0
    evidence["steps"].append({"operation": "initial", "response": initial})
    candidate = prepare("A", "first-choice")
    response = client.post("/game/commit", headers=headers, json={"ticket": candidate["ticket"]})
    response.raise_for_status()
    saved = response.json()
    assert saved["state"]["stats"]["money"] == 65
    evidence["steps"].append({"operation": "commit-choice-A", "response": saved})
    # Read in another OS process: no Python globals or in-memory session can help.
    output = subprocess.check_output([sys.executable, str(ROOT / "scripts/inspect_save.py"), "--passport", passport], text=True, encoding="utf-8")
    restored = json.loads(output)
    assert restored["state"] == saved["state"]
    assert restored["artifact_id"] == saved["artifact_id"]
    evidence["steps"].append({"operation": "independent-process-read", "response": restored})
    next_turn = prepare("CONTINUE", "new-conversation")
    assert "让出了船票" in next_turn["prompt_context"]
    evidence["steps"].append({"operation": "history-used-in-next-prompt", "context": json.loads(next_turn["prompt_context"])})
    duplicate = client.post("/game/commit", headers=headers, json={"ticket": candidate["ticket"]})
    assert duplicate.json()["revision"] == saved["revision"]
    assert duplicate.json()["deduplicated"]
    evidence["steps"].append({"operation": "idempotent-replay", "response": duplicate.json()})
    evidence["passed"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PASS: initial -> choice -> save -> independent process -> historical prompt -> idempotent replay")
    print("Evidence:", args.output)


if __name__ == "__main__":
    main()
