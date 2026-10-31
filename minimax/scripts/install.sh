#!/usr/bin/env bash
# Create minimax/.venv with the pinned dependencies (Python >= 3.11).
#   uv (recommended) provisions Python 3.12 automatically; otherwise python3.11+/venv/pip is used.
#   SDD_WITH_HF=1 also installs gradio_client for `./sdd edit --mode hf-space`.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_VERSION="${SDD_PYTHON_VERSION:-3.12}"
if command -v uv >/dev/null 2>&1; then
  echo "==> uv $(uv --version | awk '{print $2}')：创建 .venv（Python ${PYTHON_VERSION}）"
  uv venv --allow-existing --python "${PYTHON_VERSION}" .venv
  uv pip install --python .venv/bin/python -r requirements.txt
  if [[ "${SDD_WITH_HF:-0}" == "1" ]]; then
    uv pip install --python .venv/bin/python -r requirements-hf.txt
  fi
else
  PY="$(command -v python3.12 || command -v python3.11 || command -v python3 || true)"
  if [[ -z "${PY}" ]]; then
    echo "需要 Python >= 3.11 或 uv（curl -LsSf https://astral.sh/uv/install.sh | sh）" >&2
    exit 1
  fi
  "${PY}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
    echo "${PY} 版本低于 3.11；请安装 uv 后重试（uv 会自动下载 Python ${PYTHON_VERSION}）" >&2
    exit 1
  }
  echo "==> ${PY}：创建 .venv"
  "${PY}" -m venv .venv || {
    echo "python -m venv 不可用（Debian/Ubuntu 需要 python3-venv）；推荐安装 uv 后重试" >&2
    exit 1
  }
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  if [[ "${SDD_WITH_HF:-0}" == "1" ]]; then
    .venv/bin/python -m pip install -r requirements-hf.txt
  fi
fi

chmod +x sdd scripts/*.sh
.venv/bin/python - <<'EOF'
import platform
from importlib import metadata

print("python", platform.python_version())
for name in ("powercontext", "pylibseekdb", "PyMySQL", "pytest", "httpx"):
    print(f"{name}=={metadata.version(name)}")
EOF
echo "==> 安装完成。下一步：bash scripts/demo_accept.sh"
