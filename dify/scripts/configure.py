"""Create ignored configuration with independent random tokens. Prints no secrets."""
import json
import secrets
from pathlib import Path

ROOT = Path(__file__).parents[1]


def main():
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    path = runtime / "app.env"
    if path.exists():
        print("已有配置，保留原凭证：", path)
        return
    values = {"POWERCONTEXT_URL": "http://127.0.0.1:18380", "POWERCONTEXT_TOKEN": "",
              "APP_TOKEN": secrets.token_urlsafe(32), "TICKET_SECRET": secrets.token_urlsafe(48),
              "AUDIT_PATH": str(runtime / "pc-audit.jsonl")}
    # JSON quoted values preserve Windows paths and non-ASCII in dotenv.
    path.write_text("\n".join(k + "=" + json.dumps(v, ensure_ascii=False) for k, v in values.items()) + "\n", encoding="utf-8")
    print("配置已生成（未输出密钥）：", path)


if __name__ == "__main__":
    main()
