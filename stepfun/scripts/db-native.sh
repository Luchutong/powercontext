#!/usr/bin/env bash
# Linux alternative when Docker is unavailable. Uses the official packaged server.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
db_port="$(node --input-type=module -e 'import {loadEnvironment,connectionConfig} from "./server/config.mjs"; loadEnvironment(); process.stdout.write(String(connectionConfig().port));')"
db_base="$(pwd)/.data/native"
if [[ ! "$db_port" =~ ^[0-9]+$ ]]; then
  printf 'Invalid SEEKDB_PORT\n' >&2
  exit 1
fi
mkdir -p "$db_base"
if [[ -r "$db_base/run/seekdb.pid" ]]; then
  db_pid="$(cat "$db_base/run/seekdb.pid")"
  if [[ "$db_pid" =~ ^[0-9]+$ ]] && kill -0 "$db_pid" 2>/dev/null; then
    node scripts/wait-db.mjs
    printf 'seekdb already running: PID %s\n' "$db_pid"
    exit 0
  fi
fi
if [[ -z "${SEEKDB_BIN:-}" ]]; then
  if [[ ! -x .data/venv/bin/python ]]; then python3 -m venv .data/venv; fi
  .data/venv/bin/python -m pip install 'pylibseekdb==1.4.0.post1'
  SEEKDB_BIN="$(.data/venv/bin/python -c 'import pathlib,pylibseekdb; print(pathlib.Path(pylibseekdb.__file__).parent / "seekdb")')"
fi
"$SEEKDB_BIN" --base-dir="$db_base" --port="$db_port" \
  --parameter cpu_count=2 --parameter memory_budget=2G \
  --parameter datafile_size=1G --parameter datafile_maxsize=4G \
  --parameter log_disk_size=2G >> "$db_base/startup.log" 2>&1
printf 'seekdb startup requested on port %s. Log: %s/log/seekdb.log\n' "$db_port" "$db_base"
node scripts/wait-db.mjs
