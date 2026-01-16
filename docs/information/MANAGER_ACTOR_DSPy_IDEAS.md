# Manager↔Actor + DSPy: Ideas for a “Learning Manager” (Protocol-Agnostic)

This document explains how to use the text-first `MANAGER_ACTOR_INTERFACE.md` contract to evolve the current repo from an “actor loop” toward a **long-horizon manager** that orchestrates one or more external coding agents (via ACP or any other transport), and how to make that manager **learn** using DSPy + evaluation.

Key principle: **don’t hardcode “don’t do X” guardrails.** Keep actor freedom, but make the manager (and actor) responsible via evidence, deltas, and measurable outcomes.

---

## 1) What changes when we adopt the interface?

### Today (mostly actor-centric)
We already have:
- stepwise execution loops (reason → command → evaluate)
- per-step state/history artifacts
- benchmark-driven run-level oracles (Terminal-Bench tasks)

What’s missing for the “human lead” experience:
- a **manager loop** that chooses *subtasks* (not commands),
- can keep a narrative “goal/status/options” view,
- can delegate execution to actors (OpenCode, Gemini CLI, Codex, …),
- can regain control after each bounded delegation.

### With the Manager↔Actor interface
The interface makes a clean separation:
- **Manager output = Assignment** (task-level pack + bounds)
- **Actor output = Result** (evidence + deltas + impact summary)

Transport is orthogonal:
- ACP `session/prompt_turn` is just a wire format.
- A local CLI actor is just another wire format.
- A human assistant can even “role-play” the actor.

This matters because it lets us optimize the same manager behavior against different actors and transports.

### Important framing (recursive roles)
The manager/actor split is **about responsibility**, not about “who runs shell commands”.
- A manager can delegate to an actor that is itself a manager of lower-level tools/agents.
- The same interface works at multiple abstraction levels; only the task granularity changes.

---

## 2) Minimal “Manager Loop” (conceptual)

The manager loop can be the same abstract pattern we already use, but at a higher level:

1) Observe: user request + latest actor result + environment info
2) Consolidate: update shared state (facts, decisions, constraints, progress)
3) Decide: pick the next subtask + which actor to delegate to
4) Assign: create a bounded Assignment message (Outcome-only by default)
5) Execute: actor runs; manager streams progress and bridges tool calls if needed
6) Evaluate: did it meet success criteria? update state; choose next

Pseudo:
```
while not done:
  state = update_state(state, observation)
  assignment = plan_next_assignment(state)
  actor_result = run_actor(assignment)
  observation = interpret(actor_result)
```

Important: the manager should log a human-readable “what we’re doing and why” *and* a machine-readable event stream.

---

## 3) Using the interface to “change the agent” without overfitting

### The interface is the stable contract; behavior is learned
Instead of encoding special-case fixes (e.g., “never clone”), we:
- keep the manager free to delegate anything,
- require evidence and deltas back from the actor,
- score outcomes with general metrics (success, detours, repeated failures, context cost).

### The manager can vary autonomy without forbidding tools
Use the “degrees of freedom ladder” as a knob:
- Default: **Outcome-only freedom**
- When risk is high or replayability matters: use **Action-envelope**
- For deterministic replays: **Action-specified**

Crucially: choosing a tighter rung is a *decision*, not a permanent rule.

### Context selection is a first-class learnable behavior
“Minimum context per step” should not be hidden inside “write a good assignment”.
Treat **context selection** as its own object:
- what to include vs omit,
- how to anchor it to evidence (paths, step ids, error excerpts),
- how to fit a context budget without losing critical details.

---

## 4) Where DSPy fits (keep it small and testable)

We want “learning” without building a huge RL system.

Practical idea: treat the manager as a few DSPy modules (signatures) and optimize them against a measurable oracle (benchmark tests, or replay checks).

### Candidate manager modules (DSPy signatures)

#### A) `StateUpdater`
Input: `overall_goal`, `last_observation`, `prior_state`, `recent_history`
Output: `state_delta`, `state_updated`, `open_questions`, `risks`, `progress_summary`

Why: keeps the manager’s “world model” coherent and delta-friendly.

#### B) `NextSubtaskPlanner`
Input: `overall_goal`, `state`, `history`
Output: `next_task_candidates` (2–3), `decision_rationale`

Why: picks *meaningful* subtasks rather than commands.

#### C) `ContextSelector` (recommended)
Input: `overall_goal`, `chosen_subtask`, `state`, `history`
Output: `context_payload` (small), `context_budget_used`, `omissions_note` (optional)

Why: makes “minimum context” optimizable without over-constraining the assignment format.

#### D) `AssignmentWriter`
Input: chosen subtask + `context_payload` + bounds
Output: an **Assignment** in the text contract format (or structured fields rendered to text).

Why: learns clarity and delegation quality, while `ContextSelector` owns “what to include”.

#### E) `ResultInterpreter`
Input: raw actor output (streamed chunks or final result)
Output: parsed `RESULT/EVIDENCE/STATE_DELTA/CHANGES/RISKS/NEXT_OPTIONS`

Why: keeps the manager oriented even if the actor is verbose or messy.

#### F) `DelegationCritic` (optional but powerful)
Input: `assignment`, `actor_result`, `oracle_status`, `history`
Output: `what_worked`, `what_failed`, `how_to_adjust_next_assignment`

Why: focuses learning on “how to delegate better”, not “how to do commands”.

Keep the count small: start with **C + D** (ContextSelector + AssignmentWriter), then add A and B; add F only once you can run small eval suites reliably.

---

## 5) Learning objective: run-level success + generic penalties

To avoid overfitting to one task and avoid brittle rule patches, optimize on **run outcomes**.

### What exactly gets optimized?
Be explicit about the optimized object(s), otherwise learning drifts:
- Assignment text quality (clarity, sufficiency, ambiguity reduction)
- Context selection policy (what gets included/omitted and how it is anchored)
- Autonomy choice (`DEGREES_OF_FREEDOM`) as a tunable decision, not a fixed rule

### Run-level metrics (generic)
- `oracle_pass` (primary): tests pass / benchmark says resolved / replay criteria met
- `total_steps` (secondary): fewer steps is better *if success holds*
- `total_context_cost` (secondary): token/char cost of manager→actor assignments across run
- `repetition_penalty`: repeated same failure pattern without changing approach
- `confusion_penalty`: signs of environment misunderstanding (e.g., “file not found” after resets)
- `impact_penalty`: large workspace changes with weak evidence (not forbidden; just scored)

This is “freedom + responsibility” in evaluation form: the manager is free to do anything, but it learns that some patterns are expensive and correlate with failure.

---

## 6) “Evals from learnings”: how to build training/evaluation without hand-labeling everything

We already have two sources of signal in this repo:
- **run traces** (what happened, step-by-step)
- **session learnings** (what correlated with success/failure in hindsight)

The goal is to convert them into lightweight, automatable evals.

### 6.1 Replay-based evals from traces (cheap, objective)
From a known-good run/trace:
- take step `k` state/history,
- generate a manager assignment for step `k+1`,
- run an actor in a snapshot workspace,
- check whether we reach the known success oracle (or at least the next expected waypoint).

This yields objective signal: “does this delegation work with this context?”

### 6.2 Waypoint evals (reduce variance vs full-run only)
Full end-to-end oracles are great but noisy/slow. Add intermediate waypoints:
- “located file path containing X”
- “ran tests and got failing test name”
- “produced patch touching file Y”

Waypoints can be derived from past traces automatically (e.g., artifacts/paths in history events).

### 6.3 Pattern-to-scenario evals from `session_learning.json` (generalization)
Use learned patterns as scenario generators:
- Convert a learning (“tool not available; fallback needed”) into a scenario where the env probe says tool missing.
- Score whether the manager’s next assignment accounts for that fact (not by forbidding, but by reducing repeats/detours).

This is *not* hardcoding “always do grep”; it’s training the manager to:
- notice constraints from evidence,
- avoid repeating known-bad actions under the same evidence.

### 6.4 Pairwise “delegation quality” evals (fast sanity checks)
Given two candidate assignments for the same subtask:
- prefer the one that is smaller *and* still sufficient to succeed.

We can approximate sufficiency by:
- trying both against the actor and oracle,
- or using waypoint checks.

This enables DSPy to learn “how much context is enough” without us hand-authoring the answer.

---

## 7) A simple DSPy learning loop that won’t take forever

Start with a fixed actor + tiny benchmark suite:
- actor: existing Terminal-Bench agent backend or a local bash actor
- tasks: `hello-world` + `swe-bench-langcodes` (fast, already wired)

Then optimize only one thing first:
- the **AssignmentWriter** prompt/signature

Run N=3–5 optimization trials:
- evaluate with oracle pass/fail + small penalties
- keep caching and snapshot workspaces to reduce variance

Only after that stabilizes:
- add the `StateUpdater` and then `NextSubtaskPlanner`

This gives a quick “does learning move the needle?” checkpoint.

---

## 8) How the interface supports “human bridge” behavior

The manager’s product is the shared table (mob-programming feel):
- what we’re doing,
- why,
- what changed,
- what options are next,
- what we need from the human (if any).

The interface encourages this by forcing:
- `SUCCESS_CRITERIA` (what “done” means)
- `EVIDENCE` (why we believe it)
- `STATE_DELTA` (what is now true)
- `NEXT_OPTIONS` (handoff-ready choices)

If we later build a UI, it can render these fields directly. Until then, Markdown traces are enough.

---

## 9) Open questions to resolve before building a big “manager”

1) Manager persona: “mentor/lead” implies richer outputs. What’s the minimum per step?
2) Multi-actor choice: do we want an explicit “actor selection” module or keep actor fixed initially?
3) Oracles: for non-benchmark tasks, what’s the default success oracle (waypoints vs end-to-end)?
4) Human interaction mode: when blocked, do we always return control, or allow bounded assumptions?

My suggestion: keep actor fixed and optimize delegation quality first; add multi-actor selection only after the manager reliably improves outcomes on a small suite.

---

## 10) Design guardrail: impact is reporting + scoring, not gating

Avoid replacing “hardcoded do/don’t rules” with “hardcoded risk gates”.
Instead:
- require actors to report evidence + deltas + impact summary,
- let the manager learn (via evals) when impact-heavy detours correlate with failure,
- use **uncertainty-based handoff** (ask/return control when uncertain) rather than “risk-based forbiddance”.
