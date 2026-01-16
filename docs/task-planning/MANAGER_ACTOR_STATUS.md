# Goal (short)

Build a long-horizon **Manager** that delegates atomic subtasks to an executing **Actor** using `MANAGER_ACTOR_INTERFACE.md`, and optimize manager delegation quality (subtask choice, minimum context selection, autonomy knob) against run-level oracles (e.g., Terminal-Bench).

# Status (short)

- Interface contract is defined in `MANAGER_ACTOR_INTERFACE.md`.
- DSPy signature decomposition is defined in `MANAGER_ACTOR_SIGNATURE_MAP_V2.md`.
- Learning + eval ideas are captured in `MANAGER_ACTOR_DSPy_IDEAS.md`.
- Decision recorded: tool/file mechanics are not a central manager concern; actor is an existing coding agent (currently **OpenCode via ACP**) and owns execution details.
- `online_replay_loop.py` now supports an `--actor opencode-acp` mode that emits Assignments and parses Actor Results via OpenCode ACP (with partial ACP logs persisted in `steps/*/opencode_result` during execution).
- ACP stdio read limit is raised to avoid `LimitOverrunError` when OpenCode emits long stdout lines.

# Decisions

| Date | Decision | Notes |
|------|----------|-------|
| 2026-01-10 | Tool mechanics are not a manager concern | Manager stays task-level; actor (OpenCode via ACP) handles file/terminal tooling details |

# Open questions

- Manager persona / “minimum per step”: what is the required output beyond `TASK` + `SUCCESS_CRITERIA`?
- Actor selection: keep actor fixed (OpenCode) for v0, or add explicit multi-actor selection later?
- Oracles: for non-benchmark tasks, default to waypoints or end-to-end pass/fail?
- Initial eval suite: which smallest set of tasks should we optimize against first?
- Should the Terminal-Bench agent adopt the ACP actor path, or remain bash-only for benchmarking?

# Migration learnings

- Tool-plumbing discussions are a distraction: evaluate the manager on delegation (subtasks, context selection, degrees of freedom) and actor handoff quality (evidence + deltas).
- Treat “impact” as reporting + scoring signal (not gating) to avoid hardcoded brittle rules.
- Separate “context selection” from “assignment wording” so “minimum sufficient context” becomes optimizable.
- Keep bash execution paths for benchmarking; use ACP only for manager↔actor delegation.
- OpenCode ACP can emit long stdout lines; raise stdio limit to keep ACP connections stable.
