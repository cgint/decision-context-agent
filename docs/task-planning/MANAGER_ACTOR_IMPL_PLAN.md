You’re right to push back: based on the history, we keep getting pulled into “tool mechanics” (file read/write, command repair, tmux vs subprocess) instead of optimizing the manager-layer delegation quality described in MANAGER_ACTOR_INTERFACE.md.

Status for this track is maintained in `MANAGER_ACTOR_STATUS.md`.

  Evidence from our history (what actually mattered)

- The big failures weren’t “missing file tools”; they were loss of task state + bad detours (e.g. swe-bench-langcodes run 2026-01-10__14-32-26 cloned unnecessarily, then deleted/recloned, then got path
  confusion).
- The successful run (2026-01-10__15-09-39) succeeded because it quickly built environment orientation (where is langcodes/__init__.py, which tools exist like rg), then did a minimal patch + verification. That’s
  a manager competence: “what’s the next subtask?” + “what context is essential?” + “what evidence do we need next?”

  So: file tools are “plumbing”; the thing we must test/optimize is the manager loop (subtask choice + context selection + autonomy knob), using run-level oracles.

  ———

## Proposal (aligned with MANAGER_ACTOR_*.md, based on the above)

### 1) Reframe the system as a Manager policy optimized on run-level outcomes

  Keep the actor free and capable (OpenCode), but optimize the manager’s policy with DSPy:

  Manager modules (from MANAGER_ACTOR_SIGNATURE_MAP_V2.md):

- StateUpdater: maintain durable shared state + deltas.
- NextSubtaskPlanner: choose the next task-level subtask (not commands).
- ContextSelector: pick the minimum sufficient evidence anchors for that subtask.
- AssignmentWriter: render the Assignment (using the interface fields).
- ResultInterpreter + HistoryBuilder: turn messy outputs into a minimal event stream + pinned findings.

  Actor:

- OpenCode executes however it wants; manager only constrains via DEGREES_OF_FREEDOM when needed (default “Outcome-only freedom”).

### 2) Evaluate at the run level (and only use step-level waypoints to make learning cheaper)

  Primary oracle: benchmark pass/fail (Terminal-Bench is currently the best thing we have for this).
  Secondary metrics (generic, not overfit):

- steps-to-pass
- total context bytes sent in Assignments (manager→actor)
- repetition/looping indicators (same failure without changing approach)
- “detour cost” indicators (large unrelated exploration before first relevant evidence)

  Waypoints (optional, derived from traces to reduce variance):

- found target file path (evidence includes a path)
- identified failing test name
- produced a patch that touches the expected file
  These are not hard rules; they’re intermediate scoring hooks.

### 3) “Tight collaboration” means: turn-based control with evidence, not tool obsession

  A tight loop is:

1. Manager emits Assignment (task-level + success criteria + context budget).
2. Actor executes and returns Result (evidence + deltas + next options).
3. Manager updates state/history and chooses the next subtask.

  This matches your “manager takes my role” goal: the manager is optimizing delegation, not micromanaging commands.

### 4) How we use the existing history to learn (without overfitting to one task)

  Use the existing TB traces + session_learning.json as eval generators:

- “Given state/history at step k, can the manager produce an Assignment that leads the actor to reach the next waypoint (k+1) or pass the oracle?”
  That directly tests “minimum sufficient context per step” instead of guessing.

  ———

## One concrete example (task-level, not command-level)

  For swe-bench-langcodes, the manager’s subtasks should look like:

1. “Orient: locate Language.__hash__ implementation and confirm current behavior” (success = file+line evidence).
2. “Apply minimal fix consistent with __eq__” (success = patch evidence).
3. “Verify with the benchmark’s test oracle (or minimal reproduction)” (success = test evidence).

  Notice: no file-tool talk; it’s all delegation + evidence.

  ———

## Decision (resolved): “file tools are no longer our concern”

Meaning:
- The manager implementation does **not** optimize for tool mechanics (fs read/write, terminal plumbing, tmux vs subprocess, etc.).
- We use an existing coding agent as the actor (currently **OpenCode via ACP**); the actor owns execution details and any file/terminal tooling it uses.
- The manager stays task-level: next-subtask choice, minimum sufficient context selection, autonomy knob (`DEGREES_OF_FREEDOM`), and evidence+deltas on handoff.
