#!/usr/bin/env bash
# One-shot acceptance run covering all six mandatory items, from an empty state.
#   SDD_EDIT_MODE=replay (default) re-applies the recorded MiniMax session;
#   SDD_EDIT_MODE=live calls the MiniMax API (needs MINIMAX_API_KEY);
#   SDD_EDIT_MODE=hf-space calls MiniMax's official Hugging Face Space (needs SDD_WITH_HF=1 install).
set -uo pipefail
cd "$(dirname "$0")/.."

EV=evidence
LOG="${EV}/demo_accept.log"
STEPS="${EV}/demo_steps.tsv"
EDIT_MODE="${SDD_EDIT_MODE:-replay}"
FAILED=0

if [[ ! -x .venv/bin/python ]]; then
  echo "未找到 .venv，请先运行 bash scripts/install.sh" >&2
  exit 2
fi
mkdir -p "${EV}/state" "${EV}/tests"
: > "${LOG}"
printf 'step\tcommand\texpected_exit\texit_code\tduration_s\n' > "${STEPS}"
{
  echo "# demo_accept.sh started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# git_commit=$(git rev-parse HEAD 2>/dev/null || echo unknown) edit_mode=${EDIT_MODE}"
  echo "# host=$(uname -srm) python=$(.venv/bin/python -c 'import platform; print(platform.python_version())')"
} | tee -a "${LOG}"

run() {
  local name="$1" expected="$2"
  shift 2
  local started=${SECONDS}
  printf '\n==> [%s] %s\n' "${name}" "$*" | tee -a "${LOG}"
  "$@" 2>&1 | tee -a "${LOG}"
  local code=${PIPESTATUS[0]}
  printf '%s\t%s\t%s\t%s\t%s\n' "${name}" "$*" "${expected}" "${code}" "$((SECONDS - started))" >> "${STEPS}"
  printf '<== [%s] exit=%s (expected %s)\n' "${name}" "${code}" "${expected}" | tee -a "${LOG}"
  if [[ "${code}" != "${expected}" ]]; then
    FAILED=1
  fi
}

# 0. 干净状态：停止旧 Server，删除 .runtime（seekdb 数据、PowerContext 数据、工作副本）
run reset 0 ./sdd reset
# 1. 初始化：复制样例工程到工作副本、建 seekdb 库表、启动空的 PowerContext Server
run init 0 ./sdd init
run resume-empty 0 ./sdd resume --out "${EV}/state/resume_empty.json"
# 2. 导入规格/代码/测试到 seekdb，并按任务生成上下文
run ingest-v1 0 ./sdd ingest
run context-v1 0 ./sdd context --out "${EV}/context_v1.json" --md "${EV}/context_v1.md"
# 3. 修改前：目标测试必须因功能未实现而失败，回归测试必须通过
run test-before-target 0 ./sdd test --suite target --label before --expect fail
run test-before-regression 0 ./sdd test --suite regression --label before --expect pass
run save-baseline 0 ./sdd save-state --phase baseline
run context-baseline 0 ./sdd context --out "${EV}/context_baseline.json"
# 4. 把上下文交给 MiniMax 修改代码（默认回放录制的真实会话）
run edit 0 ./sdd edit --mode "${EDIT_MODE}"
# 5. 修改后：目标、回归、全量测试全部通过，并写入 PowerContext
run test-after-target 0 ./sdd test --suite target --label after --expect pass
run test-after-regression 0 ./sdd test --suite regression --label after --expect pass
run test-after-full 0 ./sdd test --suite full --label after --expect pass
run save-verified 0 ./sdd save-state --phase verified
# 6. 停止并重启 PowerContext Server，用新进程读回状态并用于后续上下文与任务处理
run server-status-before-restart 0 ./sdd --save "${EV}/state/server_before_restart.json" server status
run server-stop 0 ./sdd --save "${EV}/state/server_stop.json" server stop
run server-start 0 ./sdd --save "${EV}/state/server_start.json" server start
run resume-after-restart 0 ./sdd resume --out "${EV}/state/resume_after_restart.json"
run context-resume 0 ./sdd context --out "${EV}/context_resume.json" --md "${EV}/context_resume.md"
run edit-after-restart 0 ./sdd --save "${EV}/state/edit_after_restart.json" edit --mode "${EDIT_MODE}"
# 7. 更新规格到 v2 并重新导入，上下文必须使用新内容
run spec-update-v2 0 ./sdd spec-update --version v2
run ingest-v2 0 ./sdd ingest
run context-v2 0 ./sdd context --out "${EV}/context_v2.json" --md "${EV}/context_v2.md"
# 8. 自动核对六项验收
run accept 0 ./sdd accept

echo "# demo_accept.sh finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) failed_steps=${FAILED}" | tee -a "${LOG}"
echo "# PowerContext Server 仍在运行，可用 ./sdd resume 或 http://127.0.0.1:${SDD_PC_PORT:-17439}/dashboard/home 查看状态；结束后运行 ./sdd server stop 或 bash scripts/reset.sh" | tee -a "${LOG}"
exit "${FAILED}"
