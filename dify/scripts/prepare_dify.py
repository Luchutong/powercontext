"""Derive a small classic-Chatflow deployment from official Dify 1.17.1.

All changes are made in ignored .runtime/. Original source is pinned and untouched.
The new Agent services and vector DB are omitted because this app uses neither.
"""
import argparse
import copy
import json
import secrets
import shutil
import subprocess
from pathlib import Path

import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).parents[1]
PIN = "8387590ace4a094de812b7847fc6a4c3a27cd52b"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--port", type=int, default=18400)
    parser.add_argument("--refresh", action="store_true", help="Regenerate deployment, preserving existing credentials and data")
    args = parser.parse_args()
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    source = args.source or runtime / "dify-source"
    if not source.exists():
        subprocess.run(["git", "clone", "--depth", "1", "--branch", "1.17.1",
                        "https://github.com/langgenius/dify.git", str(source)], check=True)
    ref = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if ref != PIN:
        raise SystemExit("Dify source must be exactly " + PIN)
    target = runtime / "dify-host"
    if (target / "compose.yaml").exists() and not args.refresh:
        print("已存在部署配置，保留原环境和凭证：", target)
        return
    shutil.copytree(source / "docker", target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("volumes", ".env", ".git"))
    # Sandbox config is versioned input, unlike mutable volume data.
    shutil.copytree(source / "docker/volumes/sandbox/conf", target / "volumes/sandbox/conf", dirs_exist_ok=True)
    config = dict(dotenv_values(source / "docker/.env.example"))
    existing = dotenv_values(target / ".env") if (target / ".env").exists() else {}
    for name in ("DB_PASSWORD", "REDIS_PASSWORD", "SANDBOX_API_KEY", "PLUGIN_DAEMON_KEY",
                 "PLUGIN_DIFY_INNER_API_KEY", "SECRET_KEY", "DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN"):
        config[name] = existing.get(name) or secrets.token_urlsafe(36)
    config.update({"CELERY_BROKER_URL": "redis://:" + config["REDIS_PASSWORD"] + "@redis:6379/1",
                   "CODE_EXECUTION_API_KEY": config["SANDBOX_API_KEY"],
                   "COMPOSE_PROFILES": "", "CELERY_WORKER_AMOUNT": "1", "SERVER_WORKER_AMOUNT": "1",
                   "ENABLE_COLLABORATION_MODE": "false", "NGINX_SOCKET_IO_UPSTREAM": "api:5001",
                   "SSRF_PROXY_ALLOW_PRIVATE_DOMAINS": "life_tools", "SSRF_PROXY_ALLOW_PRIVATE_IPS": "",
                   "CONSOLE_WEB_URL": f"http://localhost:{args.port}", "CONSOLE_API_URL": f"http://localhost:{args.port}",
                   "SERVICE_API_URL": f"http://localhost:{args.port}", "APP_WEB_URL": f"http://localhost:{args.port}",
                   "APP_API_URL": f"http://localhost:{args.port}", "FILES_URL": f"http://localhost:{args.port}",
                   "LOG_TZ": "Asia/Shanghai"})
    app_config = dotenv_values(runtime / "app.env")
    if not app_config.get("APP_TOKEN"):
        raise SystemExit("Run scripts/configure.py first")
    config.update({"LIFE_APP_TOKEN": app_config["APP_TOKEN"], "LIFE_TICKET_SECRET": app_config["TICKET_SECRET"]})
    (target / ".env").write_text("\n".join(k + "=" + json.dumps(v or "", ensure_ascii=False)
                                          for k, v in config.items()) + "\n", encoding="utf-8")
    upstream = yaml.safe_load((source / "docker/docker-compose.yaml").read_text(encoding="utf-8"))
    selected = {"init_permissions", "api", "worker", "web", "db_postgres", "redis", "sandbox", "plugin_daemon", "ssrf_proxy", "nginx"}
    services = {key: copy.deepcopy(value) for key, value in upstream["services"].items() if key in selected}
    for name, service in services.items():
        service.pop("profiles", None)
        dependencies = service.get("depends_on")
        if isinstance(dependencies, dict):
            service["depends_on"] = {k: v for k, v in dependencies.items() if k in selected}
        elif isinstance(dependencies, list):
            service["depends_on"] = [k for k in dependencies if k in selected]
        service["restart"] = "no" if name == "init_permissions" else "unless-stopped"
    services["nginx"]["image"] = "nginx:1.28"
    services["nginx"]["ports"] = [f"127.0.0.1:{args.port}:80"]
    services["plugin_daemon"].pop("ports", None)
    services["ssrf_proxy"].setdefault("environment", {})["SSRF_PROXY_ALLOW_PRIVATE_DOMAINS"] = "life_tools"
    data_volumes = {"./volumes/app/storage": "life_dify_storage", "./volumes/db/data": "life_postgres",
                    "./volumes/redis/data": "life_redis", "./volumes/plugin_daemon": "life_plugins",
                    "./volumes/sandbox/dependencies": "life_sandbox_deps"}
    used = {"life_data": {}}
    for service in services.values():
        volumes = []
        for volume in service.get("volumes", []):
            for prefix, replacement in data_volumes.items():
                if volume.startswith(prefix + ":"):
                    volume = replacement + volume[len(prefix):]
                    used[replacement] = {}
                    break
            volumes.append(volume)
        service["volumes"] = volumes
    services["life_tools"] = {"build": {"context": "../..", "dockerfile": "Dockerfile"},
                              "restart": "unless-stopped", "volumes": ["life_data:/data"],
                              "ports": ["127.0.0.1:18401:18381"],
                              "environment": {"APP_TOKEN": "${LIFE_APP_TOKEN}", "TICKET_SECRET": "${LIFE_TICKET_SECRET}"},
                              "healthcheck": {"test": ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:18381/health',timeout=3)"],
                                              "interval": "15s", "timeout": "5s", "retries": 20, "start_period": "90s"}}
    output = {"name": "parallel-life", "services": services,
              "networks": upstream.get("networks", {}), "volumes": used}
    (target / "compose.yaml").write_text(yaml.safe_dump(output, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (target / "source-version.txt").write_text("Dify 1.17.1\n" + PIN + "\n", encoding="utf-8")
    print("部署配置已生成（密钥保存在忽略目录）：", target)
    print("启动：docker compose -f .runtime/dify-host/compose.yaml --project-name parallel-life up -d --build")


if __name__ == "__main__":
    main()
