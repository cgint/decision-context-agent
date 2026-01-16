# Goal (short)

Produce a step-by-step reasoning trace for `session-ui-debug` with learned deltas, knowledge state, next actions, and minimal context packs per event.

# Status (short)

- `session-ui-debug` pipeline artifacts already exist (action packs, minimal packs, replay evaluation).
- Pilot step-by-step trace created for events 1-38 at `data/gemini/session-ui-debug/step_by_step_reason_trace.md` (task-level only, markdown-only).
- Global rules and phase definitions captured in `STEP_BY_STEP_REASON_RULES.md`.
- Each event includes explicit "Rule violations" and "SUCCESS_CRITERIA" fields for machine-detectable conflicts and replay checks.
- Rule violations now distinguish `external_repo_edit` when changes happen outside this repo.
- Online (blind) replay trace started at `data/gemini/session-ui-debug/online_replay_trace.md` with Step 1 complete.
- DSPy-driven online replay loop script added at `online_replay_loop.py` with bash executor `scripts/execute_task.sh`.
- History is now maintained **deterministically** as a compact, bounded event stream (`history_events.json`) that is fed back into the LMs; verbose per-step payloads are persisted separately (`new_step.json`) for offline analysis. This avoids “history-builder overload” and schema-confusion failures.
- Added a generic command policy layer: force a safe environment probe as the first step for repo-like tasks, and block destructive resets (`rm -rf`, etc) + `git clone` unless explicitly requested (prevents “lose task state” detours).
- History events preserve short failure anchors (`evidence_summary`, `error_type`, `error_excerpt`, `returncode`) plus a short command head/ID and a small list of touched files. `STATE`/`ACTIVE_RULES` are hard-capped to prevent chatty feedback loops.
- Pinned `pyproject.toml` to Python `>=3.13,<3.14` to avoid Python 3.14 runtime issues with `terminal-bench`/`typer`, and rebuilt `uv` env (`uv sync`).
- Fixed DSPy multi-thread usage: avoid calling `dspy.settings.configure()` inside concurrent agent runs; instead use per-call `dspy.context(lm=..., adapter=JSONAdapter)` in the threaded planner/selector calls.
- Latest Terminal-Bench suite run: `data/terminal_bench_runs/2026-01-10__13-47-57/results.json` shows 2/2 resolved, but `swe-bench-langcodes` is marked `failure_mode=agent_timeout` while still `is_resolved=true` (needs interpretation / possibly adjust timeout policy).
- Captured `elix-live-chat` git state in `data/gemini/session-ui-debug/elix-live-chat-git-state.md` (patch files alongside).
- Snapshot tarball + extracted workspace saved under `data/snapshots/elix-live-chat/<timestamp>/`.
- Online replay loop now auto-detects the latest snapshot workspace (or accepts `--workspace-dir`) and runs bash steps inside that workspace.
- Online loop improvement ideas + web references captured in `docs/ONLINE_REPLAY_LOOP_IDEAS.md`.
- Added guardrails to reduce wasted steps: python_edit preflight/retry, sanitize `cat`/`less`, and block complex `sed -i`.
- Added deterministic resilience guardrails to prevent edit loops: if surgical edits repeatedly fail with `old_text not found`, the command policy forces a read/locate step (prints exact matching lines with context) before allowing further edits. Also sanitize misleading `sed -n '/def X/,/return/p'` reads into a safer “read until next def” form.
- Allowed `inspect` in the python_edit sandbox to avoid unnecessary ImportErrors.
- Next deliverable: a per-event stepwise trace aligned to the new schema in `STEP_BY_STEP_REASON_GOAL.md`.
- Open decisions: output format (Markdown only vs Markdown + JSONL), scope (pilot 10-20 steps vs full 95), granularity (task-level only vs task-level + tool-level).

# Current plan

1) Define the trace entry schema and output file path(s).
2) Generate a pilot trace for the first 10-20 events and review fidelity.
3) Expand to all events, measure compression, and iterate on pack rules.

# Notes / Risks

- If minimal packs include explicit action fields, replay becomes trivial; we should evaluate a context-only mode as well.
- Higher-level tasks should reduce context, but may lose tool-specific accuracy.
- Stepwise entries must be compact to avoid re-inflating context.
- Deferred: add automated controlled parameter sweeps (e.g., multiple history/pinning limits, rulesets, models) for benchmark comparisons; for now we run a single configuration at a time.
- Latest Terminal-Bench suite run after command policy changes: `data/terminal_bench_runs/2026-01-10__15-09-39/results.json` shows 2/2 resolved (including `swe-bench-langcodes`).
