"""Create an ignored, credential-bound Cloud import file; never commit its output."""
import argparse
from pathlib import Path
from urllib.parse import urlparse

import yaml
from dotenv import dotenv_values
from build_dsl import write_package

ROOT = Path(__file__).parents[1]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-url", required=True)
    args = parser.parse_args()
    url = args.service_url.rstrip("/")
    if urlparse(url).scheme != "https":
        raise SystemExit("Dify Cloud requires a valid HTTPS service URL")
    config = dotenv_values(ROOT / ".runtime/app.env")
    data = yaml.safe_load((ROOT / "dsl/parallel-life.chatflow.yml").read_text(encoding="utf-8"))
    values = {"life_service_url": url, "life_service_token": config["APP_TOKEN"], "narration_mode": "ai"}
    for variable in data["workflow"]["environment_variables"]:
        variable["value"] = values[variable["name"]]
    target = ROOT / ".runtime/parallel-life.cloud-private.yml"
    target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")
    write_package(data, ROOT / ".runtime/parallel-life.cloud-update.ifpkg")
    api_config = ROOT / ".runtime/cloud.env"
    if not api_config.exists():
        api_config.write_text("# Optional: paste ONLY this Dify application's API key, never the DeepSeek provider key.\n"
                              "DIFY_CLOUD_BASE_URL=https://api.dify.ai/v1\nDIFY_CLOUD_API_KEY=\n", encoding="utf-8")
    print("Private import file (contains scoped game-service credential; do not commit):", target)
    print("Service URL:", url)
