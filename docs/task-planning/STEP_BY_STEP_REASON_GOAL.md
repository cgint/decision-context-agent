# Goal (short)

Create a step-by-step reasoning trace for `session-ui-debug` that, after every event, captures (1) learned info, (2) current knowledge state, (3a) next actions, and (3b) the minimal context pack needed to succeed at the next action. The point is to measure how far we can compress context while still enabling correct execution.

# What we will produce

- A stepwise trace derived from `data/gemini/session-ui-debug/events.json`.
- A small ruleset that defines global invariants and phase-specific constraints for stepwise operation.
- Per event, the trace must include:
  1) Learned (delta only, including user input)
  2) Knowledge now (bounded summary of accumulated state)
  3a) Next actions (task-level or tool-level)
  3b) Minimal context pack for the next action (TASK, CONTEXT, CONSTRAINTS, SUCCESS_CRITERIA)
- Optional: JSONL for automation + Markdown for human review.

# Scope and constraints

- Start with `session-ui-debug` as the primary example.
- Prefer higher-level tasks when possible (e.g., "find where UI strings live") and fall back to tool-level steps when necessary.
- Each step is atomic: observe -> consolidate -> decide -> pack -> execute.
- Packs should be minimal but sufficient for a fresh, independent agent to act.

# Success criteria

- The minimal pack is replayable for the next action without additional context.
- The "learned" delta and "knowledge now" are traceable and consistent.
- Compression can be measured step-by-step (size ratio vs full context).

# Example (single entry)

- Learned: "User reports UI shows 'Custom labels not processed yet'."
- Knowledge now: "Repo has UI components; unknown where label strings are defined."
- Next action: "Search UI code for the two strings."
- Minimal context pack: "TASK: Locate exact file paths for those UI strings; use repo search; capture component/file names only."
