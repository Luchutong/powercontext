"""Import and execute in an isolated *local* Dify through its real console API.

Creates a local-only administrator if this fresh deployment is uninitialized.
Credentials remain in ignored .runtime/. Never use this bootstrap on Dify Cloud.
"""
import argparse
import base64
import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:18400")
    parser.add_argument("--reimport", action="store_true", help="Create another clean app import for portability verification")
    parser.add_argument("--dsl", type=Path, default=ROOT / "dsl/parallel-life.script-check.yml")
    args = parser.parse_args()
    if urlparse(args.base_url).hostname not in ("localhost", "127.0.0.1"):
        raise SystemExit("Local bootstrap only: refusing remote account initialization")
    private = ROOT / ".runtime/dify-admin.json"
    if private.exists():
        admin = json.loads(private.read_text(encoding="utf-8"))
    else:
        admin = {"email": "moon-admin@example.com", "password": "Moon9-" + secrets.token_urlsafe(24)}
        private.write_text(json.dumps(admin), encoding="utf-8")
    client = httpx.Client(base_url=args.base_url, trust_env=False, timeout=180,
                          headers={"Origin": args.base_url, "Referer": args.base_url + "/"})
    api = "/console/api"

    def checked(response, operation):
        if response.is_error:
            raise RuntimeError(f"{operation} HTTP {response.status_code}: {response.text[:1600]}")
        return response.json()

    status = checked(client.get(api + "/setup"), "setup status")
    if status["step"] == "not_started":
        checked(client.post(api + "/setup", json={"email": admin["email"], "name": "月球档案管理员",
                                                   "password": admin["password"], "language": "zh-Hans"}), "setup")
    checked(client.post(api + "/login", json={"email": admin["email"],
                                                "password": base64.b64encode(admin["password"].encode()).decode(),
                                                "remember_me": False}), "login")
    csrf = next((cookie.value for cookie in client.cookies.jar if "csrf" in cookie.name), "")
    client.headers["X-CSRF-Token"] = csrf
    config = dotenv_values(ROOT / ".runtime/app.env")
    data = yaml.safe_load(args.dsl.read_text(encoding="utf-8"))
    for item in data["workflow"]["environment_variables"]:
        if item["name"] == "life_service_token":
            item["value"] = config["APP_TOKEN"]
        elif item["name"] == "narration_mode":
            item["value"] = "script"
    payload = {"mode": "yaml-content", "yaml_content": yaml.safe_dump(data, allow_unicode=True, sort_keys=False)}
    if admin.get("app_id") and not args.reimport:
        payload["app_id"] = admin["app_id"]
    result = checked(client.post(api + "/apps/imports", json=payload), "import")
    print("Import:", json.dumps(result, ensure_ascii=False))
    if result["status"] == "pending":
        result = checked(client.post(api + "/apps/imports/" + result["id"] + "/confirm"), "confirm import")
    if not result.get("app_id") or result["status"] not in ("completed", "completed-with-warnings"):
        raise RuntimeError("Import incomplete")
    app_id = result["app_id"]
    admin["app_id"] = app_id
    private.write_text(json.dumps(admin), encoding="utf-8")
    evidence = {"recorded_at": datetime.now(timezone.utc).isoformat(), "dify_version": "1.17.1",
                "dsl_file": args.dsl.name,
                "base_url": args.base_url, "app_id": app_id, "import": result,
                "narration_mode": "script", "real_model_executed": False, "runs": []}
    passport = "dify-judge-" + secrets.token_hex(8)

    def run(query, conversation_id=None):
        response = client.post(api + f"/apps/{app_id}/advanced-chat/workflows/draft/run", json={
            "inputs": {"passport": passport}, "query": query, "files": [], "conversation_id": conversation_id})
        if response.is_error:
            raise RuntimeError(f"Dify run HTTP {response.status_code}: {response.text[:2000]}")
        events = []
        for line in response.text.splitlines():
            if line.startswith("data:"):
                try:
                    events.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    pass
        # Store only selected execution outputs; HTTP node inputs contain auth.
        safe_events = []
        for event in events:
            name = event.get("event")
            if name in ("node_started", "node_finished"):
                details = event.get("data", {})
                safe_events.append({"event": name, "node_id": details.get("node_id"), "node_type": details.get("node_type"),
                                    "status": details.get("status"), "error": details.get("error")})
            elif name in ("message", "message_end", "workflow_started", "workflow_finished", "error"):
                if name == "workflow_finished":
                    details = event.get("data", {})
                    safe_events.append({"event": name, "status": details.get("status"), "error": details.get("error"),
                                        "total_steps": details.get("total_steps")})
                else:
                    safe_events.append({key: event[key] for key in ("event", "conversation_id", "answer", "message", "code") if key in event})
        answer = "".join(event.get("answer", "") for event in events if event.get("event") == "message")
        cid = next((event.get("conversation_id") for event in events if event.get("conversation_id")), None)
        evidence["runs"].append({"query": query, "conversation_id": cid, "answer": answer, "events": safe_events})
        evidence_dir = ROOT / "evidence/dify-live"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "runs.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        errors = [event for event in events if event.get("event") == "error"]
        if errors:
            raise RuntimeError("Dify execution error: " + json.dumps(errors, ensure_ascii=False))
        print("Run:", query, "conversation", cid, "answer", answer[:200].replace("\n", " "))
        if not answer:
            raise RuntimeError("No answer from Dify; inspect evidence/dify-live/runs.json")
        return cid, answer

    first_id, opening = run("开始")
    assert "80" in opening and "第一天" in opening, opening
    _, after = run("A", first_id)
    assert "65" in after and "第二天" in after and "v2" in after, after
    second_id, restored = run("继续")
    assert first_id != second_id and "65" in restored and "船票" in restored, restored
    _, archived = run("档案", second_id)
    assert "第 1 天" in archived and "船票" in archived, archived
    for _ in range(5):
        _, ending = run("A", second_id)
    assert "银河合伙人" in ending and "40" in ending and "v7" in ending, ending
    _, new_life = run("新人生", second_id)
    assert "第 2 段人生" in new_life and "80" in new_life and "v8" in new_life, new_life
    _, old_life = run("档案", second_id)
    assert "旧人生 #1" in old_life and "银河合伙人" in old_life, old_life
    published = checked(client.post(api + f"/apps/{app_id}/workflows/publish", json={"marked_name": "剧本实机验收"}), "publish")
    evidence["publish"] = published
    evidence["passed"] = True
    evidence["demo_passport"] = passport
    (ROOT / "evidence/dify-live/runs.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PASS: real import, nodes, choice, new conversation, archive, full ending, new life, publish")


if __name__ == "__main__":
    main()
