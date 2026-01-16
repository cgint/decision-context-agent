# Manager ↔ Actor Interface (Protocol-Agnostic, Text-First)

Goal: define a **human-like delegation contract** between a long-horizon **Manager** and an executing **Actor**, independent of transport (ACP, CLI, chat, etc.). The same payload should work:

- as plain text between humans,
- inside `session/prompt_turn` over ACP,
- or as the input/output contract of a local “actor” process.

This is intentionally **lightly structured**: small required headers for automation + large freeform space for reasoning, nuance, and explanation.

---

## Core ideas

- The **Manager owns the long-horizon problem**: goal, priorities, tradeoffs, and “what’s next”.
- The **Actor owns bounded execution**: do a scoped subtask and return evidence + deltas.
- The interface is **task-level**, not command-level: the actor may choose tools/commands unless the manager intentionally narrows degrees of freedom.
- “Freedom with responsibility” means: the actor can act, but must keep the manager oriented via **evidence**, **deltas**, and **impact awareness** (what changed, why, and how to revert if needed).
- This interface is **recursive across abstraction levels**: a “Manager” can delegate to an “Actor” that is itself a manager of lower-level tools/agents. The contract stays the same; only the task granularity changes.

---

## Message types

### 1) Assignment (Manager → Actor)

Required headers (minimal, parseable):

```
ASSIGNMENT_ID: <string>
TASK: <one sentence; atomic subtask>
SUCCESS_CRITERIA: <how we know this subtask succeeded>
```

Optional headers (add only if they materially help):

```
OVERALL_GOAL: <the manager’s broader objective for this run/thread>
CONTEXT: <facts + evidence pointers; keep small>
CONTEXT_BUDGET: <optional budget hint; e.g. “<= 15 lines”, “<= 1k chars”, “only anchors”>
STATE: <current shared state / key facts / decisions (delta-friendly)>
HISTORY: <recent event stream or summary; keep small; links/pointers ok>
CONSTRAINTS: <hard boundaries; scope; “do not”>
DEGREES_OF_FREEDOM: <what the actor may decide vs must follow>
BUDGET: <steps/time/tool limits; optional>
DELIVERABLES: <what to return: patch, file paths, explanation, etc.>
```

Notes:
- These headers are **optional conveniences**, not required structure. If you prefer, you can inline the same content in the freeform body under headings like “Overall goal / State / History”.
- Actors must **not** assume these fields are present; treat missing fields as empty/unknown and proceed (or return control via a context request).

Freeform body (recommended):
- “Why this task matters now”
- “Known pitfalls / prior attempts”
- “What must not be broken”
- “What context was intentionally omitted” (optional; helps with replayability)

### 2) Progress update (Actor → Manager)

Used for streaming or checkpoints; fully optional.

```
ASSIGNMENT_ID: <string>
STATUS: <started|investigating|implementing|verifying|blocked>
NOTE: <1–3 lines; observation-only>
```

### 3) Context request (Actor → Manager)

When blocked, the actor asks for **specific missing info**.

```
ASSIGNMENT_ID: <string>
REQUEST_CONTEXT: <what exact info is missing>
WHY: <why it blocks progress>
IF_NO_RESPONSE: <default: return control to manager (do not proceed); include suggested assumptions/options>
```

### 4) Result (Actor → Manager)

Required headers:

```
ASSIGNMENT_ID: <string>
RESULT: <success|partial|fail>
EVIDENCE: <the smallest anchors that justify RESULT>
STATE_DELTA: <new durable facts learned; concise>
```

Optional but often useful:

```
CHANGES: <files changed / commands run / external effects; concise>
ARTIFACTS: <paths created/modified; logs; outputs>
RISKS: <what might be wrong / what to double-check>
OPEN_QUESTIONS: <what remains uncertain>
NEXT_OPTIONS: <2–3 possible next subtasks (not commands), with tradeoffs>
```

Freeform body (recommended):
- “Narrative of what happened” (short, human-readable)
- “If I had more time, I’d …”

---

## Responsibility primitives (non-restrictive)

These are not “forbidden actions”. They are **reporting requirements** that keep the manager (and human) oriented.

### Impact awareness
If the actor performs a potentially high-impact action (e.g., large deletion, workspace reset, network fetch, bulk refactor), it should:
- state the intent,
- cite the evidence that motivated it,
- note what state may be lost,
- suggest a revert/rollback path.

Crucially: “high-impact” is **self-reported** by the actor based on expected irreversibility/scope, not a fixed blacklist.

Also crucially: impact awareness should be treated as **reporting + scoring signal**, not an action gate. The system should learn “this was expensive/risky and didn’t help” from outcomes, rather than hardcoding a growing list of forbidden operations.

### Uncertainty-based handoff (not risk-based gating)
When the actor’s uncertainty is high (missing context, ambiguous instructions, unknown environment effects), it should **return control** using a context request rather than proceed on assumptions. This preserves freedom while avoiding unforced detours.

### Evidence over confidence
Prefer short evidence anchors (paths, line numbers, error excerpts, return codes, test names) over long prose.

### Deltas, not reprints
Return only what changed in understanding (`STATE_DELTA`) and what changed in the workspace (`CHANGES`), not full re-dumps of the whole state.

---

## Degrees of freedom ladder (manager chooses per assignment)

This is how the manager can tune autonomy *without* hardcoding rules:

1) **Outcome-only freedom**: Actor chooses approach; must satisfy success criteria; returns evidence.
2) **Action-envelope**: Actor may choose tools/commands but within explicit bounds (allowed paths, read-only, time/step budget).
3) **Action-specified (replay)**: Manager provides exact command/patch to execute (max determinism; minimal autonomy).

The same interface supports all three; only the `DEGREES_OF_FREEDOM` section changes.

Default (recommended): **Outcome-only freedom** unless the manager explicitly narrows it.

---

## Example (Manager → Actor)

```
ASSIGNMENT_ID: A-017
TASK: Locate the current `Language.__hash__` implementation and propose the minimal correct fix.
OVERALL_GOAL: Fix the reported `Language.__hash__` inconsistency while keeping the change minimal and well-evidenced.
SUCCESS_CRITERIA: Identify the exact file+location and confirm the fix makes equal Language objects hash-equal after pickle roundtrip.
CONTEXT: Repo workspace is already provided; prefer using existing tests if present.
CONSTRAINTS: Keep change minimal; avoid workspace resets unless you can justify with evidence.
DEGREES_OF_FREEDOM: You may choose search/read tools; you may edit code; you may run tests.
DELIVERABLES: File path + short patch summary + evidence (tests/output excerpt).
```

## Example (Actor → Manager)

```
ASSIGNMENT_ID: A-017
RESULT: success
EVIDENCE: `langcodes/__init__.py:1503` changed from `hash(id(self))` to `hash(self._str_tag)`; verifier script prints `True`; task test `test__hash__` passes.
STATE_DELTA: `__hash__` depended on object identity; now hashes stable across pickle roundtrip for equal tags.
CHANGES: Modified `langcodes/__init__.py`; created temp verifier; ran oracle test.
RISKS: If `_str_tag` can be mutated after construction, hashing could be unsafe; check invariants.
NEXT_OPTIONS: Add unit test for pickle roundtrip; confirm `_str_tag` immutability.
```
