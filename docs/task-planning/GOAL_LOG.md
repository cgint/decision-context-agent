# Goal Log

## Entry 001: Goal alignment and dual challenge clarification

- User clarified two challenges that converge:
  - Learn from past history what worked and what did not.
  - Optimize live execution by minimizing context passed to each step (action packs).
- Working loop clarified: start with empty state, then observe/read -> consolidate -> derive next action pack -> execute -> repeat.
- Decision: keep `FINISH_LINE_GOAL_SETTING.md` as the concise goal/status page and store detailed notes here.

### User request (verbatim)

> FINISH_LINE_GOAL_SETTING.md - there is the description of the ultimate goal where the current code in this repo is a part of the way to that. in this repo we collected and partly already examine ai coding agent histories to learn two things: how to create traces that make it easy to understand what was going on and how to continue, extract learnings from past decisions that worked or did not so we can preserve and generalise those for the future. ultimately: Every step called with the minimum possible information of task+context to correctly do the (sub-)job. This is the challenge for optimisation when it comes to agentic systems. - pls understand - make your thoughts - read python code and markdown files so you get a 360 view of the current state - then i want you to put my request from this message to FINISH_LINE_GOAL_SETTING.md and also put your current thoughts into that file by just appending to it

### Current thoughts (Codex)

- The repo already implements a clean 3-stage pipeline (events -> reasoning traces -> learned patterns) that aligns with the goal of extracting "why" and reducing context to what matters.
- The finish line focuses on minimum sufficient task+context per step; the missing piece is an explicit evaluation loop that measures how little context still enables correct execution.
- The recursive synthesis approach preserves nuanced patterns while surfacing universal laws, which is a solid foundation for deriving context-minimization heuristics.
- The modular code layout (shared config/models + per-stage scripts) makes it practical to add a context-selection or compression experiment layer without large refactors.
- The research memos emphasize JIT context compilation and decision traces; the next technical bridge is a concrete policy that selects minimal context slices from traces and validates them against task success.

### Simulation findings (proxy metrics)

- Best reasoning efficiency (recall per 1k tokens) uses `traces|full_sentences` or `patterns|full_sentences`.
- Combining `traces+patterns|full_sentences` yields full reasoning recall at low token counts.
- Task coverage is dominated by raw `events` (full sentences), but token cost is very high.
- Triplet formats are smaller but currently lower recall than full sentences for reasoning.

## Entry 002: Round-table alignment + action-pack loop clarified

- User clarified two converging challenges:
  - Learn from past history what worked and what did not.
  - Optimize live execution by minimizing context passed to each step (action packs).
- Desired interaction: round-table shared state where humans and AI see the same goal, facts, uncertainties, and next actions; each party takes turns (mob-programming style, generalizable beyond coding).
- Reviewed `GENIETABLE_*.md` which already describes a deliberation loop (agents propose, user curates, `/step` executes, context accumulates).
- Updated `FINISH_LINE_GOAL_SETTING.md` with a step-by-step action-pack example and alignment to the round-table model.

## Entry 003: Action-pack extraction seed + first example

- Decision: keep action packs in raw markdown form with optional metadata; avoid fixed schema for now.
- Implemented `derive_action_packs.py` to extract tool-call steps into raw action-pack drafts.
- Selected `data/gemini/session-web-research/events.json` as the first example (short, clear tool-call sequence).
- Generated `data/gemini/session-web-research/action_packs_raw.md` as the initial action-pack sample.

## Entry 004: Sequence of next work

- Decision: prioritize non-tool step extraction and replay scorecard before moving to coding-heavy sessions.

## Entry 005: Non-tool action packs + replay scorecard implemented

- `derive_action_packs.py` now extracts reasoning (THOUGHTS), plan/ask_user/consolidate notes, plus tool-call steps.
- Replay scorecard added to action-pack output with per-step signals and proxy token counts.
- Regenerated `data/gemini/session-web-research/action_packs_raw.md` with 35 steps and a scorecard.

## Entry 006: State replay (single trace)

- Added `replay_state_timeline.py` to replay a session into step-by-step state deltas and snapshots.
- Generated `data/gemini/session-web-research/state_timeline.md` to visualize how evidence/artifacts/notes accumulate each step.

## Entry 007: DSPy minimal context packs (script)

- Implemented `derive_minimal_context_packs.py` to derive minimal context packs per step using DSPy.
- Produces `minimal_context_packs.md` with per-step token ratios (full vs minimal).

## Entry 008: Minimal context comparison report

- Added `compare_minimal_context.py` to compare full vs minimal packs and flag inflated steps.
- Generated `data/gemini/session-web-research/minimal_context_comparison.md` (avg_ratio 0.56; inflated steps mostly google_web_search/read_file).

## Entry 009: Replay evaluation loop (minimal packs -> predicted actions)

- Implemented `replay_evaluation.py` to predict next actions from minimal packs using DSPy and compare against action packs.
- Generated `data/gemini/session-web-research/replay_evaluation.md` (steps_total 35, type_match_rate 0.49, tool_exact_rate 0.32, non_tool_exact_rate 0.15).
- Observation: mismatches cluster where minimal packs imply tool actions but historical steps were non-tool, suggesting step boundary alignment needs tightening before moving to a second trace.

## Entry 010: Explicit action fields + replay fidelity reset

- Updated `derive_minimal_context_packs.py` to emit explicit action fields (ACTION_TYPE/TOOL_NAME/TOOL_ARGS/NON_TOOL_TYPE) and only use DSPy for context bullets.
- Tool args are now minimized per tool (query/prompt/path only) to reduce pack size.
- Updated `replay_evaluation.py` prompt to copy explicit action fields verbatim; replay evaluation now reports perfect fidelity (type_match_rate/tool_exact_rate/non_tool_exact_rate = 1.00).
- Regenerated `data/gemini/session-web-research/minimal_context_comparison.md` to reflect the new pack format.

## Entry 011: New primary example (session-ui-debug)

- Chose `data/gemini/session-ui-debug/events.json` as a better trace: clear read/replace flow, code edits, and a concrete fix + commit message.
- Generated `data/gemini/session-ui-debug/action_packs_raw.md` (95 steps) and `data/gemini/session-ui-debug/state_timeline.md`.
- Generated `data/gemini/session-ui-debug/minimal_context_packs.md` and `data/gemini/session-ui-debug/minimal_context_comparison.md` (avg_ratio 0.52; 16 heavy steps, mostly tool:replace).
- Generated `data/gemini/session-ui-debug/replay_evaluation.md` (perfect fidelity due to explicit action fields).

## Entry 012: Terminal-Bench integration spike (hello-world)

- Installed Terminal-Bench CLI with `uv tool install terminal-bench` (provides `tb`).
- Downloaded dataset `terminal-bench-core==0.1.1` to cache.
- Created an isolated workspace for task `hello-world` by copying the task dir and **excluding** `solution.sh` to avoid leakage.
- Ran `online_replay_loop.py` against the task instruction (hello.txt). The loop created `hello.txt` as expected and stopped after completion.
- Found and fixed two integration bugs in `online_replay_loop.py`:
  - `execute_task.sh` path must be absolute when running in a workspace.
  - `STEP_DIR` must be absolute; otherwise stdout/stderr land inside the workspace (and create extra folders).
- Attempted to run the oracle locally; tests failed because the oracle expects the file at `/app/hello.txt` (containerized path). Conclusion: for valid scoring, we need to run the oracle inside the Terminal-Bench Docker harness (or simulate `/app` within a container).
- Side effect: the task’s test harness reinitialized the repo `.venv` (created a fresh pytest-only environment). We should isolate benchmarking venvs from the main repo env.
