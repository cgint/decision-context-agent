# Step-by-step reasoning rules

This file defines global constraints and phase-specific rules for the step-by-step trace process.

## Available Actions
- **shell_command**: Run system commands, navigate, execute tests.
- **python_edit**: Edit files using Python. Available: `re`, `open()`, `os.path`, `json`, string ops.
  Ideal for: pattern extraction, multi-line edits, building replacements from variables.
  Sandboxed to workspace. Does not execute target code.

## Global principles

- Preserve user constraints across all steps.
- Keep steps atomic: one action per step.
- Record evidence sources (files read, commands run, key outputs).
- If a step cannot proceed, capture what's blocking and what to try next.
- Do not edit code unless the user explicitly approves.

## Phases

### Analysis (default)
- **Goal:** Understand symptoms and locate relevant code.
- **Output:** Candidate areas, hypotheses, open questions.

### Root-cause analysis
- **Goal:** Identify the exact condition causing the issue.
- **Output:** Causal chain with evidence.

### Planning
- **Goal:** Propose a minimal fix and verification plan.
- **Output:** Actionable steps with clear success criteria.

### Implementing
- **Goal:** Apply the fix and verify it works.
- **Requires:** Explicit user permission.
- **Output:** Changes made + verification results.
- **Key rule:** Verify every edit was actually applied before moving on.
- **Prefer `python_edit`** for any edit involving multi-line changes, complex patterns, or content extracted in a previous step.
- **NEVER use `sed`** for anything beyond simple single-line `s/old/new/` substitutions.
- After any edit, run `git diff --stat <file>` — if it shows `0 insertions, 0 deletions`, the edit failed.

**Example `python_edit`:**
```python
content = open('file.py').read()
new_content = content.replace('old_pattern', 'new_pattern')
open('file.py', 'w').write(new_content)
```

### Ideation
- **Goal:** Generate alternative approaches or improvements.
- **Output:** Options with tradeoffs.

## Phase switching

- Only switch phases when the current goal is met or the user requests it.
- Switching to Implementing requires explicit approval.

## Minimal pack format

- **TASK:** The next action in one sentence.
- **CONTEXT:** Only the facts required for that task.
- **CONSTRAINTS:** Limits and "do not" rules.
- **SUCCESS_CRITERIA:** What a correct result looks like.
