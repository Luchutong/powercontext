"""Audit only deliverable files, without printing secret values."""
import json
import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).parents[1]
REPO = ROOT.parent


def main():
    output = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "dify"], cwd=REPO)
    paths = sorted({item.decode("utf-8") for item in output.split(b"\0") if item})
    secrets = {}
    for source in (ROOT / ".runtime/app.env", ROOT / ".runtime/cloud.env", ROOT / ".runtime/dify-host/.env"):
        if source.exists():
            for name, value in dotenv_values(source).items():
                if value and len(value) >= 20 and any(part in name for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                    secrets[name] = value
    private = ROOT / ".runtime/dify-admin.json"
    if private.exists():
        secrets["LOCAL_ADMIN_PASSWORD"] = json.loads(private.read_text(encoding="utf-8"))["password"]
    failures = []
    for relative in paths:
        if "/.runtime/" in relative or relative.endswith("/.env"):
            failures.append({"path": relative, "problem": "private configuration selected"})
            continue
        path = REPO / relative
        if not path.is_file():
            continue
        if path.suffix == ".ifpkg":
            import yaml
            with zipfile.ZipFile(path) as archive:
                manifest = yaml.safe_load(archive.read("manifest.yaml"))
                for item in manifest["apps"]:
                    content = archive.read(item["path"])
                    if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
                        failures.append({"path": relative, "problem": "invalid package manifest"})
                text = "\n".join(archive.read(item.filename).decode("utf-8") for item in archive.infolist())
        else:
            text = path.read_text(encoding="utf-8")
        for name, value in secrets.items():
            if value in text:
                failures.append({"path": relative, "problem": "credential value found", "kind": name})
        if path.suffix == ".py":
            compile(text, relative, "exec")
    result = {"files_checked": len(paths), "credential_values_checked": len(secrets), "failures": failures}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
