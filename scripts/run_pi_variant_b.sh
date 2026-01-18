#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: required command not found: $1" >&2
    exit 1
  fi
}

require_cmd pi

EXT_PATH="$REPO_ROOT/integrations/pi_mono/extensions/manager_bridge/index.ts"
if [[ ! -f "$EXT_PATH" ]]; then
  echo "error: extension not found: $EXT_PATH" >&2
  exit 1
fi

RUNS_DIR="${DCA_RUNS_DIR:-$REPO_ROOT/data/pi_mono_bridge_runs}"
mkdir -p "$RUNS_DIR"

TS="$(date -u +%Y%m%d_%H%M%S)"
LOGDIR="$RUNS_DIR/$TS"
mkdir -p "$LOGDIR"

PROMPT="${1:-what time is it}"

echo "repo_root=$REPO_ROOT"
echo "logdir=$LOGDIR"
echo "extension=$EXT_PATH"
echo "prompt=$PROMPT"
echo

echo "== Step 1/3: sanity-check pi can complete a prompt (no extensions) =="
set +e
BASELINE_OUT="$(
  pi --no-session --thinking minimal -p "Say 'ok' and nothing else." 2>&1
)"
BASELINE_RC=$?
set -e

echo "$BASELINE_OUT" | sed -n '1,120p'
echo
if [[ $BASELINE_RC -ne 0 ]] || echo "$BASELINE_OUT" | rg -q "fetch failed"; then
  echo "error: pi prompt failed (exit=$BASELINE_RC). Fix provider/API key/network first." >&2
  echo "tip: try running: pi --no-session -p \"hi\" --thinking minimal" >&2
  exit 2
fi

echo "== Step 2/3: Variant B run (manager-bridge via stdio:) =="
echo "note: this should exit on its own; if it hangs, Ctrl+C and share stderr."
echo

set +e
VARIANT_OUT="$(
  pi --no-session \
    -e "$EXT_PATH" \
    --manager-url "stdio:" \
    --manager-log-dir "$LOGDIR" \
    --manager-events "session_start,before_agent_start,tool_call,tool_result,turn_end,agent_end" \
    --manager-steer-policy "off" \
    --thinking minimal \
    -p "$PROMPT" 2>&1
)"
VARIANT_RC=$?
set -e

echo "$VARIANT_OUT" | sed -n '1,200p'
echo

if [[ $VARIANT_RC -ne 0 ]] || echo "$VARIANT_OUT" | rg -q "fetch failed"; then
  echo "error: Variant B run failed (exit=$VARIANT_RC)." >&2
  echo "logdir=$LOGDIR" >&2
  exit 3
fi

echo "== Step 3/3: Verify manager-bridge logs exist =="
FILES_FOUND="$(find "$LOGDIR" -type f -maxdepth 5 | wc -l | tr -d ' ')"
echo "files_in_logdir=$FILES_FOUND"
if [[ "$FILES_FOUND" -eq 0 ]]; then
  echo "warning: no log files found in $LOGDIR" >&2
  echo "This usually means the extension did not receive any events (or manager-url was unset)." >&2
  exit 4
fi

echo
echo "ok: Variant B completed and produced logs."
echo "next: inspect logs with:"
echo "  find \"$LOGDIR\" -type f -maxdepth 5 -print"
echo "  rg -n \"\\\"type\\\":\\\"tool_call\\\"|\\\"type\\\":\\\"turn_end\\\"\" \"$LOGDIR\" || true"

