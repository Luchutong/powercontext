#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
db_mode="${1:-docker}"
case "$db_mode" in
  docker|--existing-db|--native-db) ;;
  --help) printf 'Usage: bash scripts/start.sh [--existing-db|--native-db]\nConfiguration: stepfun/.env (see .env.example)\n'; exit 0 ;;
  *) printf 'Unknown option: %s\n' "$db_mode" >&2; exit 2 ;;
esac
node -e 'const [major,minor]=process.versions.node.split(".").map(Number); if(major<22 || major===22&&minor<12) {console.error("Node.js 22.12+ is required"); process.exit(1);}'
export ONNXRUNTIME_NODE_INSTALL_CUDA=skip
npm ci --no-audit --no-fund
npm run models -- --runtime-only
if [[ "$db_mode" == "docker" ]]; then
  npm run db:up
elif [[ "$db_mode" == "--native-db" ]]; then
  bash scripts/db-native.sh
fi
node scripts/wait-db.mjs
npm run build
exec npm start
