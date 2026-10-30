"""One container, private loopback PowerContext, authenticated gateway on its network."""
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    os.environ.setdefault("POWERCONTEXT_URL", "http://127.0.0.1:18380")
    os.environ.setdefault("AUDIT_PATH", "/data/pc-audit.jsonl")
    pc = subprocess.Popen([sys.executable, str(ROOT / "scripts/run_powercontext.py"), "--data", "/data/powercontext"])
    try:
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if pc.poll() is not None:
                raise RuntimeError("PowerContext exited during startup")
            try:
                ready = httpx.get(os.environ["POWERCONTEXT_URL"] + "/health/ready", timeout=2, trust_env=False)
                if ready.status_code == 200 and ready.json().get("status") == "ready":
                    break
            except httpx.TransportError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("PowerContext startup timed out")
        import uvicorn
        from app.server import create_app
        uvicorn.run(create_app(), host="0.0.0.0", port=18381, workers=1, access_log=False)
    finally:
        pc.terminate()
        try:
            pc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pc.kill()
            pc.wait()
