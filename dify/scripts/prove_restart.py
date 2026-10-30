"""Restart only this project's tool container and prove the save survives.

Run under Linux/WSL with docker available. All HTTP uses the public game API,
all credentials are read locally, and the evidence contains only demo state.
"""
import json
import secrets
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).parents[1]
COMPOSE = ROOT / ".runtime/dify-host/compose.yaml"
BASE = "http://127.0.0.1:18401"


def main():
    env = {}
    for line in (ROOT / ".runtime/app.env").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key] = json.loads(value)
    headers = {"Authorization": "Bearer " + env["APP_TOKEN"], "Content-Type": "application/json"}
    command = ["docker", "compose", "-f", str(COMPOSE), "--project-name", "parallel-life"]

    def post(path, data):
        request = Request(BASE + path, data=json.dumps(data).encode(), headers=headers)
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    def process():
        cid = subprocess.check_output(command + ["ps", "-q", "life_tools"], text=True).strip()
        value = subprocess.check_output(["docker", "inspect", "--format", "{{json .State}}", cid], text=True)
        state = json.loads(value)
        return {"container_id": cid, "started_at": state["StartedAt"], "pid": state["Pid"]}

    passport = "restart-" + secrets.token_hex(8)
    turn = post("/game/prepare", {"passport": passport, "command": "A", "event_id": "before-restart"})
    saved = post("/game/commit", {"ticket": turn["ticket"]})
    before = process()
    subprocess.run(command + ["restart", "life_tools"], check=True)
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        try:
            with urlopen(BASE + "/health", timeout=2) as response:
                if json.load(response).get("ok"):
                    break
        except (URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(1)
    else:
        raise RuntimeError("Restarted service did not become ready")
    after = process()
    restored = post("/game/prepare", {"passport": passport, "command": "ARCHIVE", "event_id": "after-restart"})
    assert before["started_at"] != after["started_at"] and before["pid"] != after["pid"]
    assert restored["artifact_id"] == saved["artifact_id"]
    assert restored["revision"] == saved["revision"] == 2
    assert restored["state"] == saved["state"]
    result = {"passed": True, "before_process": before, "after_process": after,
              "saved": saved, "restored": restored}
    path = ROOT / "evidence/restart-persistence.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PASS: changed container process -> same Artifact, revision and state")


if __name__ == "__main__":
    main()
