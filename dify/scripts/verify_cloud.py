"""Run the actual AI application through its user-provided Dify app API key.

Reads only ignored .runtime/cloud.env. Does not log keys, node inputs, tickets,
raw HTTP receipts or model prompts. Provider credentials remain inside Dify.
"""
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).parents[1]


def main():
    config = dotenv_values(ROOT / ".runtime/cloud.env")
    key = config.get("DIFY_CLOUD_API_KEY", "")
    if not key:
        raise SystemExit("Fill the application's API key in .runtime/cloud.env first; never send it in chat.")
    base_url = config.get("DIFY_CLOUD_BASE_URL", "https://api.dify.ai/v1").rstrip("/")
    if not base_url.startswith("https://"):
        raise SystemExit("HTTPS required for Cloud app credentials")
    client = httpx.Client(base_url=base_url, headers={"Authorization": "Bearer " + key}, timeout=240)
    passport = "cloud-judge-" + secrets.token_hex(8)
    user = "parallel-life-check-" + secrets.token_hex(8)
    evidence = {"recorded_at": datetime.now(timezone.utc).isoformat(), "kind": "Dify Cloud app API",
                "demo_passport": passport, "runs": [], "passed": False}

    def run(query, conversation=None, expect_ai=True):
        response = client.post("/chat-messages", json={"inputs": {"passport": passport}, "query": query,
                               "response_mode": "streaming", "user": user,
                               "conversation_id": conversation or "", "files": []})
        if response.is_error:
            raise RuntimeError(f"Cloud HTTP {response.status_code}: {response.text[:400]}")
        events = []
        for line in response.text.splitlines():
            if line.startswith("data:"):
                try:
                    events.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    pass
        safe = []
        for event in events:
            name = event.get("event")
            if name in ("node_started", "node_finished"):
                data = event.get("data", {})
                safe.append({"event": name, "node_id": data.get("node_id"), "node_type": data.get("node_type"),
                             "status": data.get("status"), "error": data.get("error"),
                             "execution_metadata": {k: v for k, v in (data.get("execution_metadata") or {}).items()
                                                    if k in ("total_tokens", "total_price", "currency")}})
            elif name == "message_end":
                safe.append({"event": name, "usage": (event.get("metadata") or {}).get("usage")})
            elif name == "error":
                safe.append({k: event.get(k) for k in ("event", "code", "message")})
        answer = "".join(item.get("answer", "") for item in events if item.get("event") == "message")
        cid = next((item.get("conversation_id") for item in events if item.get("conversation_id")), None)
        reasoning_present = bool(re.search(r"<think\b|dify-deepseek-reasoning", answer, flags=re.I))
        safe_answer = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", answer, flags=re.S | re.I)
        if re.search(r"<think\b", safe_answer, flags=re.I):
            safe_answer = re.sub(r"<think\b.*", "[incomplete reasoning omitted]", safe_answer, flags=re.S | re.I)
        evidence["runs"].append({"query": query, "conversation_id": cid, "answer": safe_answer,
                                 "raw_reasoning_detected": reasoning_present, "execution": safe})
        save()
        assert answer, "No Cloud response; inspect evidence/cloud-live.json"
        assert not reasoning_present, "Update the narration-output node: reasoning tags must not appear in the player answer."
        if expect_ai:
            assert "叙事方式：AI 叙事" in answer, "Model was not confirmed; fallback output is not AI acceptance."
        print("Cloud turn passed:", query)
        return cid, answer

    def save():
        path = ROOT / "evidence/cloud-live.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    first, opening = run("开始")
    assert "80" in opening and "第一天" in opening
    _, changed = run("A", first)
    assert "65" in changed and "v2" in changed
    second, restored = run("继续")
    assert first != second and "65" in restored and "船票" in restored
    _, archived = run("档案", second, expect_ai=False)
    assert "船票" in archived and "第 1 天" in archived
    evidence["passed"] = True
    evidence["real_model_executed"] = True
    save()
    print("PASS: Dify Cloud AI narration + actual save + new conversation recall")


if __name__ == "__main__":
    main()
