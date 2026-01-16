# Goal (aligned)

Build a step-by-step execution loop that turns observations into minimal action packs (task + context) for each step, informed by learnings from past agent histories and presented as a round-table shared state for humans and AI.

# Short status (as of now)

- Past-history pipeline exists: events -> reasoning traces -> learned patterns with multiple real sessions in `data/gemini`.
- Compression simulation harness exists (`simulate_context_compression.py`); proxy results logged in `GOAL_LOG.md`.
- Genie round-table prototype exists in `GENIETABLE_*.md` (other repo) and aligns with the step-by-step shared context approach.
- Action-pack draft extraction exists (`derive_action_packs.py`); includes non-tool steps + replay scorecard at `data/gemini/session-web-research/action_packs_raw.md`.
- Step-by-step state replay exists (`replay_state_timeline.py`) with a first timeline at `data/gemini/session-web-research/state_timeline.md`.
- DSPy minimal context pack generator exists (`derive_minimal_context_packs.py`) and targets `minimal_context_packs.md`.
- Minimal packs now include explicit action fields (ACTION_TYPE/TOOL_NAME/TOOL_ARGS/NON_TOOL_TYPE) with context bullets.
- Minimal vs full context comparison exists (`compare_minimal_context.py`) with report at `data/gemini/session-web-research/minimal_context_comparison.md`.
- Replay evaluation loop implemented (`replay_evaluation.py`) with report at `data/gemini/session-web-research/replay_evaluation.md` (type_match_rate 1.00, tool_exact_rate 1.00, non_tool_exact_rate 1.00 after explicit action fields).
- New primary example `session-ui-debug` selected (clear read/replace flow + code edits); full artifact set generated under `data/gemini/session-ui-debug/`.
- Next critical gap: tighten action-pack step boundaries and schema so minimal packs align with actual step types.

# Two challenges that converge

1) Learn from history: extract what worked/failed into patterns (current repo focus).
2) Online execution: observe -> consolidate -> produce minimal action pack -> execute -> repeat.

These converge when learned patterns guide action-pack creation and live runs create new traces for the library.

# Working loop (single-step view)

- State S0 is empty.
- Step N:
  - Observe/read (new evidence).
  - Consolidate into state (facts + decisions + uncertainties).
  - Derive next action pack (task + minimal context).
  - Execute the action pack.
  - Append resulting events/traces back into state.

# Round-table alignment (GENIETABLE)

The GENIETABLE prototype describes a deliberation loop where agents propose, the user curates, and `/step` executes actions against a shared context. This is the interaction model we want: a common view of goal, status, facts, uncertainties, and next actions that both humans and AI can read, edit, and steer together (mob-programming style, generalized beyond coding).

# Step-by-step example (action packs)

Example task: "Fix failing test in auth module."

Step 0 (observe)

- Action pack: "Read failing test output; include only test name and error line."
- Expected result: failure signature captured.

Step 1 (context gather)

- Action pack: "Open failing test file + related auth module; include only file paths + relevant snippets."
- Expected result: minimal code context needed for diagnosis.

Step 2 (reason + propose)

- Action pack: "Describe likely cause and propose minimal change; include failure signature + snippets."
- Expected result: candidate fix with scope.

Step 3 (execute + verify)

- Action pack: "Apply fix; run specific test; include command + expected pass signal."
- Expected result: test passes or new failure captured for next loop.

This illustrates the single-step rhythm: observe -> consolidate -> minimal action pack -> execute -> update state.

# Single-trace learning gate (before any second history)

We must complete and learn from one trace end-to-end before moving to a second example.

Required artifacts for a single trace:
- action packs (tool + non-tool steps)
- state timeline (step-by-step deltas + snapshots)
- minimal context packs (raw text, not schema)
- comparison report (full vs minimal context size, step-by-step)
- replay evaluation report (does minimal context predict the correct action)

What we must learn from the first trace:
- Which step types require consolidation vs direct execution.
- What minimal context is necessary per step to be replayable.
- Where cancellations, retries, or confusion occur and why.
- Token cost per step and per successful outcome.

No UI is required. This is a data and processing exercise.

# Open clarifications

- Is scope limited to coding agents, or broader agentic workflows?
  - overall knowledge-driven work where ai and humans collaborate - so lets keep datastructures rather generic for now (text instead of json with narrow-facts)
- What is the primary success metric: task correctness, cost, latency, or a weighted mix?
  - task correctness and use of tokens to successfully complete (will reduce cost plus speed in the end - but those are secondary for now)
- Do we accept proxy metrics (token coverage), or require task replays for validation?
  - Lets start with what is already telling us if we are heading into the right direction
  - ideally known real world examples of coding tasks
  - if that is not doable lets make up an example so that we get started and make the first learnings

# Thoughts (Codex)

- Keep the two challenges explicit: history learning (offline) and action-pack execution (online). The bridge is a policy that turns learned patterns into context selection rules.
- Action packs should be typed and atomic (observe/read/plan/execute/verify) so each step can be evaluated independently.
- The round-table model (GENIETABLE) is the right UI/interaction layer for shared state, traceability, and user steering.
- We should define an action-pack schema early (task, minimal context, constraints, success criteria) and evolve it via replay-based evaluation.

# Details / Background

## What is the actual goal

Decomposition and iteration are important in longer running agentic task completion.

Every step called with the minimum possible information of task+context to correctly do the (sub-)job. This is the challenge for optimisation when it comes to agentic systems.

Retrieval, decision-traces, ... are mandatory!

*See also: https://fortune.com/2026/01/06/want-ai-agents-to-work-better-improve-the-way-they-retrieve-information-databricks-says/*

## Main challenges

Reduce context to the minimum (!) context - which is task-description and information - about the environment to successfully get the job done.

# How do we get there step by step

## Simple testability and evaluation

We want to create rather simple scenarios of medium challenges where we can test what extractions are minimum and still work to get the job done

## First tryouts should be quickly possible as we have the history

and there are way more history files also waiting to be evaluated

## Replay evaluation (session-web-research)

Results (DSPy, gemini-3-flash-preview) in `data/gemini/session-web-research/replay_evaluation.md`:
- steps_total: 35
- tool_exact_rate: 1.00
- non_tool_exact_rate: 1.00
- type_match_rate: 1.00

Interpretation: explicit action fields make replay perfect, which confirms action-pack fidelity but shifts the question to "model obedience" vs "pack correctness." If we want to measure inference from context-only packs, we should run a separate evaluation mode without explicit action fields.

## Replay evaluation (session-ui-debug)

Results (DSPy, gemini-3-flash-preview) in `data/gemini/session-ui-debug/replay_evaluation.md`:
- steps_total: 95
- tool_exact_rate: 1.00
- non_tool_exact_rate: 1.00
- type_match_rate: 1.00

Compression summary in `data/gemini/session-ui-debug/minimal_context_comparison.md`:
- avg_ratio: 0.52
- heavy_steps: 16 (mostly tool:replace)
