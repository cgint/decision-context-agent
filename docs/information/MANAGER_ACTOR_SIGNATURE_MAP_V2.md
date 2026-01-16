# Manager↔Actor Signature Map (v2, prose)

This is a prose “wiring diagram” for how the manager↔actor contract maps to DSPy signatures. The goal is to keep agents **free**, but make delegation **legible**, **optimizable**, and **replayable** without hardcoding brittle rules.

---

## Goal and status (short)

Goal: every step delegates an atomic subtask using the **minimum sufficient context**, and every result returns **evidence + deltas** so the next step can adapt.

Status: the interface is task-level and text-first (`MANAGER_ACTOR_INTERFACE.md`). Next is aligning the DSPy signature set to (1) separate **context selection** from **assignment wording**, and (2) make autonomy a tunable decision rather than a rule.

---

## Core decomposition (what should be learned vs executed)

### 1) Manager responsibilities (learnable)
The manager should mostly learn:
- what the next subtask is (task-level planning),
- what context is necessary for that subtask (selection + anchoring),
- what autonomy level is appropriate for that subtask (degrees of freedom),
- how to express the delegation so an actor can succeed (clarity).

### 2) Actor responsibilities (executed)
The actor should mostly execute:
- choose specific tools/commands/edits (unless autonomy is intentionally narrowed),
- verify success criteria,
- report evidence, deltas, and any irreversible changes.

This separation is recursive: an “actor” can itself be a manager that delegates to lower-level tools.

---

## Signatures (v2)

### A) `StateUpdater` (manager)
Purpose: convert new observations into a delta-friendly shared state.

Inputs (typical):
- `overall_goal`, `prior_state`, `recent_history`, `new_observation`

Outputs (typical):
- `state_delta` (durable facts learned),
- `state_updated`,
- `open_questions` (prefer non-blocking phrasing in no-human mode),
- `risks` (optional),
- `progress_summary` (optional).

### B) `NextSubtaskPlanner` (manager)
Purpose: propose 2–3 next subtasks, then select one with rationale.

Outputs should be task-level (not commands), e.g. “locate implementation of X”, “run tests to identify failing case”, “apply minimal patch to Y”.

### C) `ContextSelector` (manager) — **first-class**
Purpose: decide what to include for the actor to succeed, with evidence anchors.

Key idea: “minimum context” is not a writing skill; it is a selection policy.

Outputs:
- `context_payload` (small, evidence-heavy),
- `context_budget_used` (optional; chars/tokens/lines),
- `omissions_note` (optional; what was intentionally not included).

### D) `AssignmentWriter` (manager)
Purpose: render an Assignment message (task + success criteria + bounds), using the selected context.

Two viable forms:
1) **Structured fields** rendered into the text headers from `MANAGER_ACTOR_INTERFACE.md` (easier to score/compare).
2) **Freeform assignment text** that follows the contract (more human-like; harder to optimize).

Either way, the output should align to the interface fields:
- `TASK`, `SUCCESS_CRITERIA`, optional `CONSTRAINTS`, optional `CONTEXT`, optional `CONTEXT_BUDGET`, optional `DEGREES_OF_FREEDOM`.

### E) `ExecutionPlanner` (actor-side, optional)
Purpose: convert `TASK + SUCCESS_CRITERIA + CONTEXT` into an executable plan (command, patch plan, tool calls).

Important: this keeps the manager from becoming command-centric.

### F) `ResultInterpreter` (manager)
Purpose: parse actor output (which may be messy) into the Result fields:
- `RESULT`, `EVIDENCE`, `STATE_DELTA`, optional `CHANGES/ARTIFACTS/RISKS/NEXT_OPTIONS`.

### G) `HistoryBuilder` (manager, or infra)
Purpose: maintain a **minimal event stream** for continuity and replay.

Principle: history is observation-only (what happened + anchors), not “Next: do X”.

---

## Autonomy as a knob, not a rule

`DEGREES_OF_FREEDOM` is a decision the manager can tune per assignment:
1) Outcome-only freedom (default)
2) Action-envelope (bounded tools/paths/time)
3) Action-specified (deterministic replay)

This can itself be optimized (learned) later.

---

## Impact: reporting + scoring, not gating

We should avoid “manager step before high-risk actions” as a fixed rule.

Instead:
- actors always report **impact** (what changed, what was at risk, how to revert),
- evaluators score outcomes so impact-heavy detours get penalized when they don’t help,
- handoffs are triggered by **uncertainty**, not by a hardcoded list of risky commands.

---

## Evaluation hooks (what can be scored without overfitting)

Run-level:
- pass/fail oracle,
- steps,
- context cost (chars/tokens),
- repetition/detour indicators,
- impact-with-weak-evidence penalty,
- confusion indicators (e.g., repeated “file not found” due to wrong assumptions).

Step-level (cheap):
- whether the actor can reach the immediate success criteria (waypoint),
- whether evidence anchors exist and are consistent with deltas,
- whether the next assignment adapts after a failure (policy update, not retry).
