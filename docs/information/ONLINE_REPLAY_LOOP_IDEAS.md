# Goal (short)

Make `online_replay_loop.py` reliably converge (blind, step-by-step) while minimizing per-step context by improving (a) initial orientation, (b) persistent state/memory, (c) action safety + determinism, and (d) measurable compression/evaluation.

# Status (short)

- Loop exists: DSPy Reasoner → bash action → DSPy Evaluator; persists per-step I/O; can run inside latest `elix-live-chat` snapshot workspace.
- Pattern corpus exists: `data/gemini/*/session_learning.json` contains reusable “priors” (principles + anti-patterns) but is not yet used as an explicit steering input in the loop.
- Compression experiments exist elsewhere in the repo, but the online loop doesn’t yet make “compression strategy comparison” a first-class thing per step.

# Ideas (prioritized, opinionated)

## Now (make Step 1 work better)

- Add a “Step 0 / Preflight” concept (even if it’s just a documented convention): freeze constraints + phase, confirm workspace/snapshot, capture a tiny repo fingerprint (git SHA, language markers, top-level dirs). This prevents Step 1 from spending context on “where am I / what is this repo”.
- Make the Reasoner emit a strict `Unknowns` list for Step 1, and force the *next action* to reduce one Unknown at a time (otherwise the loop drifts into vague exploration).
- Persist an explicit per-step “Action rationale” in one line (“why this command now”), and treat it as part of the compression target: if the rationale can’t be written in one line, the step is probably too big.
- Tighten the evaluator output contract into: `Success?`, `Evidence pointers`, `New facts`, `Remaining unknowns`, `Recommended next action`. This makes knowledge-state updates stable and comparable.

## Next (make the loop safer + more general)

- Introduce an explicit action taxonomy (conceptually, even before implementation): `read_shell`, `write_shell`, `ask_user`, `stop`. Then make “write” require phase switch + approval; this reduces heuristic “looks_like_write_command” brittleness.
- Add “detours” as first-class state: record 1–3 candidate next actions considered but not taken (this is crucial to answer: “could we have solved this in fewer steps?”).
- Add a context budget per step (chars/tokens) and log it; treat budget overruns as a failure mode that triggers a compression attempt or a task split.
- Make “evidence pointers” mandatory in knowledge updates (paths + small anchors like function names/line numbers) so minimal packs can cite *where* the facts came from without copying content.

## Later (turn it into a real benchmark harness)

- Multi-run replay harness: run the same initial request N times with different compression strategies and report (a) success rate, (b) steps-to-converge, (c) re-reading ratio, (d) token/context cost, (e) number of rule violations.
- Add “adversarial minimality” checks: try removing each context item one-by-one (or ask an LLM to propose removals) and re-run the step to estimate which tokens were actually necessary.

# Compression strategy experiments (what to compare)

- **Encodings** (same facts, different surface forms):
  - Triples: `SUBJ | REL | OBJ` (optionally grouped by entity).
  - Triples as short strings: “X has Y”, “Spinner depends on key presence”.
  - Full sentences / narrative.
  - Hybrid: triples for stable facts + sentences for open questions / rationale.
- **Granularity**:
  - Task-level only vs task-level + tool-level microsteps.
  - Keep only last step vs rolling window vs retrieval-only (bring back only what’s needed).
- **History inclusion**:
  - “Evidence-only” history (paths + anchors) vs “verbatim excerpts” history.
  - Include “detours considered” vs omit them.

# Using `session_learning.json` as priors (without bloating context)

Idea: treat the corpus as *retrieval-only priors*.

- For each step, select top-k patterns as short “priors” based on current phase + observation keywords (e.g. constraints-first, baseline verification, signal-to-noise filtering, tool/schema non-compliance).
- Persist which `pattern_id`s were active per step (so we can later correlate priors ↔ outcome).
- Never allow priors to override explicit user constraints (priors are advisory, constraints are binding).

# External references (web) that map cleanly to our goal

- ReAct: Synergizing Reasoning and Acting in Language Models (arXiv:2210.03629) — think/act/observe loop as the base mental model. `https://arxiv.org/abs/2210.03629`
- Reflexion: Language Agents with Verbal Reinforcement Learning (arXiv:2303.11366) — store “reflection text” as episodic memory after failures; aligns with step-wise post-mortems. `https://arxiv.org/abs/2303.11366`
- MemGPT: Towards LLMs as Operating Systems (arXiv:2310.08560) — explicit memory tiers; aligns with separating stable rules vs working memory vs retrieval. `https://arxiv.org/abs/2310.08560`
- LLMLingua (arXiv:2310.05736) and LLMLingua-2 (arXiv:2403.12968) — prompt compression baselines we can compare our “minimal pack” encodings against. `https://arxiv.org/abs/2310.05736` and `https://arxiv.org/abs/2403.12968`
- Tree of Thoughts (arXiv:2305.10601) / Graph of Thoughts (arXiv:2308.09687) — formalize “detours” and backtracking as deliberate exploration rather than noise. `https://arxiv.org/abs/2305.10601` and `https://arxiv.org/abs/2308.09687`
- Toolformer (arXiv:2302.04761) — highlights the importance of correct tool argument selection and call timing; maps to stricter action schemas and evaluator checks. `https://arxiv.org/abs/2302.04761`
- Generative Agents (arXiv:2304.03442) — observation→reflection→planning memory pattern; mirrors what we’re trying to persist per step. `https://arxiv.org/abs/2304.03442`

