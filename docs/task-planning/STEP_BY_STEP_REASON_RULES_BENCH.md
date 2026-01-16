# Step-by-step reasoning rules (benchmark mode)

Rules for benchmark tasks where the task description is implicit approval to act.

## Available Actions
- **shell_command**: Run system commands, navigate, execute tests.
- **python_edit**: Edit files using Python. Available: `re`, `open()`, `os.path`, `json`, string ops.
  Ideal for: pattern extraction, multi-line edits, building replacements from variables.
  Sandboxed to workspace. Does not execute target code.

## Principles

- Keep steps atomic: one action per step.
- Record evidence (paths, commands, key outputs).
- If blocked, capture what's wrong and what to try next.
- Avoid reading oracle solutions or hidden reference files.

## Editing

- **Prefer `python_edit`** for any edit involving multi-line changes, complex patterns, or content extracted in a previous step.
- **NEVER use `sed`** for anything beyond simple single-line `s/old/new/` substitutions.
- Verify every edit: run `git diff --stat <file>` — if it shows `0 insertions, 0 deletions`, the edit failed.
- If an approach fails twice, try something different.
- If unsure what the code looks like, read it again.

**Example `python_edit`:**
```python
# Read, modify, write — variables persist across steps
content = open('file.py').read()
new_content = content.replace('old_pattern', 'new_pattern')
open('file.py', 'w').write(new_content)
```

## Minimal pack format

- **TASK:** The next action in one sentence.
- **CONTEXT:** Only the facts required for that task.
- **CONSTRAINTS:** Limits and "do not" rules.
- **SUCCESS_CRITERIA:** What a correct result looks like.
