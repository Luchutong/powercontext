"""Read a known synthetic Cloud save after deploying the final service image."""
import json
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).parents[1]

if __name__ == "__main__":
    config = dotenv_values(ROOT / ".runtime/app.env")
    cloud = json.loads((ROOT / "evidence/cloud-live.json").read_text(encoding="utf-8"))
    assert cloud.get("passed") and not any(row.get("raw_reasoning_detected") for row in cloud["runs"])
    client = httpx.Client(base_url="http://127.0.0.1:18401", trust_env=False, timeout=5)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            response = client.get("/health")
            if response.status_code == 200 and response.json().get("ok"):
                break
        except httpx.TransportError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("Final service did not become ready")
    response = client.post("/game/prepare", headers={"Authorization": "Bearer " + config["APP_TOKEN"]},
                           json={"passport": cloud["demo_passport"], "command": "ARCHIVE", "event_id": "final-deployment-readback"})
    response.raise_for_status()
    data = response.json()
    assert data["revision"] == 2 and data["state"]["turn"] == 1
    assert data["state"]["stats"] == {"money": 65, "friendship": 3, "absurdity": 0}
    result = {"passed": True, "health": "ready", "cloud_save_readback": {
        "artifact_id": data["artifact_id"], "revision": data["revision"], "state": data["state"]}}
    (ROOT / "evidence/final-deployment.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PASS: final service image is ready and reads the actual Cloud v2 save unchanged")
