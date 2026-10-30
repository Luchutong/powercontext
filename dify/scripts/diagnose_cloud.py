"""One targeted call: summarize reasoning flow without revealing reasoning text."""
import json
import re
import secrets
from pathlib import Path
import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).parents[1]

if __name__ == "__main__":
    config = dotenv_values(ROOT / ".runtime/cloud.env")
    client = httpx.Client(base_url=config.get("DIFY_CLOUD_BASE_URL", "https://api.dify.ai/v1").rstrip("/"),
                          headers={"Authorization": "Bearer " + config["DIFY_CLOUD_API_KEY"]}, timeout=180)
    info = client.get("/info").json()
    result = {"app_info": {k: info[k] for k in ("name", "id", "app_id", "mode") if k in info}, "nodes": []}
    response = client.post("/chat-messages", json={"inputs": {"passport": "diagnostic-" + secrets.token_hex(8)},
                           "query": "开始", "response_mode": "streaming", "user": "output-diagnostic", "files": []})
    response.raise_for_status()
    answer = ""
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:])
        except json.JSONDecodeError:
            continue
        if event.get("event") == "message":
            answer += event.get("answer", "")
        data = event.get("data") or {}
        if event.get("event") == "node_finished" and data.get("node_id") in ("writer", "ai_prose", "merge", "format"):
            outputs = data.get("outputs") or {}
            process = data.get("process_data") or {}
            result["nodes"].append({"node": data["node_id"], "status": data.get("status"),
                "text_fields": {k: {"length": len(v), "has_reasoning": bool(re.search(r"<think|dify-deepseek-reasoning", v, re.I))}
                                for k, v in outputs.items() if isinstance(v, str)},
                "model": {k: process[k] for k in ("model_name", "model_provider", "model_mode", "model_parameters", "completion_params") if k in process}})
    result["answer_length"] = len(answer)
    result["answer_has_reasoning"] = bool(re.search(r"<think|dify-deepseek-reasoning", answer, re.I))
    (ROOT / ".runtime/cloud-diagnostic.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
