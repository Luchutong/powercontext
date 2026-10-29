#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
export ONNXRUNTIME_NODE_INSTALL_CUDA=skip
npm ci
npm run models
if [[ "${1:-}" != "--existing-db" ]]; then
  npm run db:up
fi
npm run build
exec npm start
