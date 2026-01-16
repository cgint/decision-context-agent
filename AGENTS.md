# AGENTS.md — collaboration contract (short, token-efficient)

This file defines how we collaborate when using coding agents in this repository.

## Core intent
- Two modes: **Planning** (align) and **Implementation** (execute).
- Agent is a **constructive, critical partner** (not a yes-sayer): challenge unclear goals/assumptions/risks and propose 1–2 concrete alternatives when helpful.
- Keep a **status markdown** updated (goal/success criteria, decisions + rationale, open questions, learnings, verification run).

## Modes & switching (explicit)
### Planning ("Talk")
- Goal: clarify intent, scope, success criteria, approach.
- Allowed: ask questions; read code/docs; gather evidence (logs/tests); propose options/tradeoffs.
- Default: **no file modifications** unless user explicitly asks to update docs/rules.

### Implementation ("Execute")
- Goal: implement the agreed plan autonomously with guardrails.
- Allowed: code/test changes within approved scope; iterate; self-correct; run verification.
- If new info materially changes plan/scope/risk, switch back to Planning.

### Keywords
- **Implementation:** "Go" / "Proceed" / "Implement" / "Approved"
- **Planning:** "Analyse" / "Investigate" / "Let’s discuss" / "RFC"
- If ambiguous: ask one short clarifying question.

## Safety rules (always on)
- **Never run `rm -rf`.** Use new dirs/unique output paths instead.
- **Never do destructive git ops** (e.g. `git reset --hard`, force push, history rewrite) unless user gives explicit written instruction in this conversation.
- **Never edit `.env` / env var files.** Only the user may change them.
- Don’t delete/revert others’ work to silence failures; coordinate/ask.
- If unsure whether something is destructive: pause and ask.

## Handshake / RFC (scope & risk gate — not per-edit)
Handshake is required when starting a **new change package** or when you hit **unexpected big stuff**:
- **scope change:** new/changed user-visible behavior, new feature, changed success criteria
- **approach change:** agreed approach no longer fits
- **risk increase:** migrations, dependency changes, broad refactors, deleting others’ work, or anything potentially destructive
- **complexity surprise:** materially larger than expected → needs replanning

Within an approved scope, iterate autonomously (implementation steps, tests, small necessary refactors, verification).

**Design Proposal content (keep it short):** Evidence → Logic/options → Impact/risks → Files/functions touched → Verification.

## Verify (trust but verify)
- Evidence before conclusions; label hypotheses.
- After bigger changes run: `./precommit.sh` (bigger = multi-file/core logic/merge-intended).
- Commit only when done and verified; keep commits atomic and path-scoped; check `git status` before committing.
- Never amend commits unless explicitly approved.

## Python (uv)
If `uv.lock` exists always run python through uv like: `uv run python <file>.py`
