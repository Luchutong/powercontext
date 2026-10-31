#!/usr/bin/env bash
# Return to a clean state: stop the PowerContext Server and delete .runtime
# (embedded seekdb data, PowerContext SQLite data, workspace copy of sample_app).
#   --evidence  also restore evidence/ and snapshots/ to the committed versions.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -x .venv/bin/python ]]; then
  ./sdd reset
else
  rm -rf .runtime
fi
if [[ "${1:-}" == "--evidence" ]]; then
  git checkout -- evidence snapshots
  git clean -fdq -- evidence snapshots
  echo "evidence/ 与 snapshots/ 已恢复为提交版本"
fi
echo "已恢复干净状态；重新运行：bash scripts/demo_accept.sh"
