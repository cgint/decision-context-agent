# Goal (short)

Build a long-horizon **Manager** that delegates atomic subtasks to an executing **Actor** using `MANAGER_ACTOR_INTERFACE.md`, optimizing delegation quality (subtask choice, minimum context selection, autonomy knob) while keeping tool execution passive and actor-driven (ACP-first).

# Success criteria (current scope)

- Local execution path behaves as a **Minimal Actor** (no edit micromanagement).
- ACP delegation is the primary orchestration boundary for both local and remote actors.
- SWE-bench Lite evaluation can select ACP actor execution (e.g., `--actor opencode-acp`).

# Status (short)

- Interface contract is defined in `MANAGER_ACTOR_INTERFACE.md`.
- DSPy signature decomposition is defined in `MANAGER_ACTOR_SIGNATURE_MAP_V2.md`.
- Learning + eval ideas are captured in `MANAGER_ACTOR_DSPy_IDEAS.md`.
- Decision recorded: tool/file mechanics are not a central manager concern; actor is an existing coding agent and owns execution details.
- `online_replay_loop.py` defaults to `--actor opencode-acp` (Assignments + Actor Results via ACP).
- ACP stdio read limit is raised to avoid `LimitOverrunError` when OpenCode emits long stdout lines.

# Decisions

| Date | Decision | Notes |
|------|----------|-------|
| 2026-01-10 | Tool mechanics are not a manager concern | Manager stays task-level; actor handles file/terminal tooling details |
| 2026-01-16 | ACP-first integration | Manager orchestrates; OpenCode acts as ACP Actor by default; local path kept minimal |

# Open questions

- Do we need a dedicated ACP adapter for Pi-Mono, or should we standardize on OpenCode ACP initially?
- What heuristics should the Manager use for loop detection (count, time, or evidence type)?

# Migration learnings

- Tool-plumbing discussions are a distraction: evaluate the manager on delegation quality and actor handoff evidence.
- Treat “impact” as reporting + scoring signal (not gating) to avoid brittle rules.
- Separate “context selection” from “assignment wording” so “minimum sufficient context” becomes optimizable.
- Keep bash execution paths for benchmarking; use ACP for manager↔actor delegation.
- OpenCode ACP can emit long stdout lines; raise stdio limit to keep ACP connections stable.

# Verification run

- LSP diagnostics: online_replay_loop.py (clean), manager_actor_protocol.py (clean), run_swebench_eval.py (clean), scripts/acp_probe.py (clean). Markdown LSP unavailable.
