# SWEBench: Pi RPC (Vehicle) vs ACP (Manager→Actor)

This repo now supports running SWE-bench Lite in two modes:

1) **ACP baseline (existing)**: this repo’s Manager loop (`online_replay_loop.py`) drives an Actor via ACP (`--actor opencode-acp`).
2) **Pi vehicle (new)**: Pi Mono runs the loop in **RPC mode**, with this repo’s **manager-bridge extension** attached (`--agent-impl pi_rpc`).

The goal is to compare throughput/latency and outcomes with minimal harness differences.

## Smoke test (no LLM required)

Verifies Pi RPC mode + extension loading (no prompts, no tools):

`python3 scripts/pi_rpc_smoke_test.py`

## Run A: ACP baseline

Example: 1 instance, Docker harness:

`python3 run_swebench_eval.py --num-instances 1 --evaluation-mode docker --agent-impl online_replay --actor opencode-acp`

Outputs:
- `data/swebench_eval/<run>/instances/<id>/agent_run/steps/...`
- ACP transcripts under `.../opencode_result/...` (existing)

## Run B: Pi vehicle via RPC (+ manager-bridge)

Example: 1 instance, Docker harness:

`python3 run_swebench_eval.py --num-instances 1 --evaluation-mode docker --agent-impl pi_rpc`

Optional model selection for Pi (overrides your local `pi` config):

`python3 run_swebench_eval.py --num-instances 1 --evaluation-mode docker --agent-impl pi_rpc --pi-provider google --pi-model gemini-2.5-flash --pi-thinking-level minimal`

Outputs:
- Pi RPC transcript: `data/swebench_eval/<run>/instances/<id>/agent_run/pi_rpc_transcript.jsonl`
- Pi agent end event: `data/swebench_eval/<run>/instances/<id>/agent_run/pi_agent_end.json`
- Manager-bridge logs (stdio-spawned, no sockets): `data/swebench_eval/<run>/instances/<id>/agent_run/pi_mono_bridge/<session>/events.jsonl`

## What to compare

- **Wall clock per instance**: `results.json` (`elapsed_seconds`) for both runs.
- **In-loop time breakdown**:
  - ACP: `.../opencode_result/acp_transcript.json` + backend stderr timestamps (existing)
  - Pi: `.../pi_rpc_transcript.jsonl` (events include `tool_execution_*`, `turn_*`, `agent_end`)
- **Manager overhead** (Pi mode): extension UI RTT isn’t available headless; use manager-bridge `timing.duration_ms` inside `events.jsonl` responses (recorded by the manager sidecar).

## Notes

- `--agent-impl pi_rpc` uses `--manager-url stdio:` internally, so it does **not** require binding TCP ports or Unix sockets.
- Default behavior in Pi mode is **trace-only** (`--manager-steer-policy off`) to avoid blocking on Manager “steering”.

