#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TASKS_FILE="${TASKS_FILE:-$ROOT_DIR/benchmarks/tb_tasks.txt}"
DATASET="${DATASET:-terminal-bench-core==0.1.1}"
OUTPUT_PATH="${OUTPUT_PATH:-$ROOT_DIR/data/terminal_bench_runs}"

AGENT_IMPORT_PATH="${AGENT_IMPORT_PATH:-bench_agents.online_replay_agent:OnlineReplayAgent}"
MODEL="${MODEL:-gemini-2.5-flash}"
MAX_STEPS="${MAX_STEPS:-30}"

N_CONCURRENT="${N_CONCURRENT:-2}"
N_ATTEMPTS="${N_ATTEMPTS:-1}"
GLOBAL_AGENT_TIMEOUT_SEC="${GLOBAL_AGENT_TIMEOUT_SEC:-900}"

NO_UPLOAD_RESULTS="${NO_UPLOAD_RESULTS:-1}"
NO_LIVESTREAM="${NO_LIVESTREAM:-1}"

if [[ ! -f "$TASKS_FILE" ]]; then
  echo "Missing TASKS_FILE: $TASKS_FILE" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Missing 'uv' CLI." >&2
  exit 1
fi

# Prefer running via the project's uv environment so local agent code can import repo deps (e.g. dspy).
tb_cmd=(uv run tb)

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "Missing GEMINI_API_KEY in environment." >&2
  exit 1
fi

task_args=()
while IFS= read -r line; do
  line="${line%%#*}"
  line="$(echo "$line" | xargs)"
  [[ -z "$line" ]] && continue
  task_args+=( --task-id "$line" )
done <"$TASKS_FILE"

if [[ "${#task_args[@]}" -eq 0 ]]; then
  echo "No tasks found in $TASKS_FILE" >&2
  exit 1
fi

cmd=(
  "${tb_cmd[@]}" run
  --dataset "$DATASET"
  "${task_args[@]}"
  --agent-import-path "$AGENT_IMPORT_PATH"
  --agent-kwarg model="$MODEL"
  --agent-kwarg max_steps="$MAX_STEPS"
  --output-path "$OUTPUT_PATH"
  --n-concurrent "$N_CONCURRENT"
  --n-attempts "$N_ATTEMPTS"
  --global-agent-timeout-sec "$GLOBAL_AGENT_TIMEOUT_SEC"
)

if [[ "$NO_UPLOAD_RESULTS" == "1" ]]; then
  cmd+=( --no-upload-results )
fi
if [[ "$NO_LIVESTREAM" == "1" ]]; then
  cmd+=( --no-livestream )
fi

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  printf "PYTHONPATH=%q GEMINI_API_KEY=*** %q" "$ROOT_DIR" "${cmd[0]}"
  for arg in "${cmd[@]:1}"; do
    printf " %q" "$arg"
  done
  printf "\n"
  exit 0
fi

PYTHONPATH="$ROOT_DIR" GEMINI_API_KEY="$GEMINI_API_KEY" "${cmd[@]}"
