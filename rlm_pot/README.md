# RLM-PoT: Recursive Language Model via Program of Thought

A pluggable module that enables agents to execute Python code with a persistent namespace, implementing the "Context as Variable" pattern.

## The Problem

When agents use `sed` to edit files, they often:
1. Read a file (`cat file.py`)
2. "Remember" a pattern from the output
3. Construct a `sed` command with the remembered pattern
4. **Fail** because they hallucinated/misremembered the exact pattern

Example of this failure:
```
# Agent reads: _line_type_re = re.compile(_type_re)
# Agent remembers (incorrectly): re.compile(_line_type_re)  
# Agent runs: sed 's/re.compile(_line_type_re)/.../'  ← Pattern doesn't exist!
# sed returns exit code 0 (silent failure)
```

## The Solution

Instead of relying on the LLM's "memory", use Python variables:

```python
# Step 1: Load and extract
content = open('qdp.py').read()
import re
match = re.search(r're\.compile\((\w+)\)', content)
var_name = match.group(1)  # Exact: '_type_re'

# Step 2: Construct deterministically
old = f're.compile({var_name})'
new = f're.compile({var_name}, re.IGNORECASE)'
new_content = content.replace(old, new)

# Step 3: Verify and write
assert old not in new_content  # Change was applied
with open('qdp.py', 'w') as f:
    f.write(new_content)
```

The variable `var_name` is exact - no hallucination possible.

## Quick Start

### Standalone Usage (testing/scripting)

```python
from rlm_pot import PythonExecutor

executor = PythonExecutor(working_dir=Path('/path/to/workspace'))

# Execute code
result = executor.run('''
content = open('file.py').read()
import re
match = re.search(r'def (\\w+)', content)
_result_ = match.group(1)  # Return value
''')

print(result["result"])  # The extracted function name
print(result["namespace_keys"])  # ['content', 'match', 're']
```

### Agent Loop Integration

```python
# In online_replay_loop.py

# At top of file
try:
    from rlm_pot import handle_python_action, reset_executor, is_enabled
    RLM_POT_ENABLED = is_enabled()
except ImportError:
    RLM_POT_ENABLED = False

# In action execution section
if action_type == "shell_command":
    # ... existing shell handling ...
elif action_type == "eval_python" and RLM_POT_ENABLED:
    result = handle_python_action(code, working_dir=workspace_dir)
    stdout = result["stdout"]
    stderr = result["stderr"]  
    returncode = result["returncode"]
```

## Key Features

### Persistent Namespace

Variables survive across agent steps:

```python
# Step 3
result = executor.run('file_content = open("big_file.py").read()')

# Step 4 - file_content is still available!
result = executor.run('lines = file_content.split("\\n"); _result_ = len(lines)')
```

### Safe Execution

- **Restricted imports**: Only safe modules (re, json, os.path, pathlib, etc.)
- **Path sandboxing**: File access limited to working directory
- **No dangerous builtins**: eval, exec, compile are blocked

### Result Return Convention

Set `_result_` to return structured data to the agent:

```python
_result_ = {
    "found": True,
    "line_number": 42,
    "pattern": "re.compile(_type_re)"
}
```

## Testing

Run standalone tests:

```bash
cd /path/to/decision-context-tracing
uv run python -m rlm_pot.test_executor
```

## Files

```
rlm_pot/
├── __init__.py          # Package exports
├── executor.py          # Core PythonExecutor class
├── action_handler.py    # Agent loop integration
├── test_executor.py     # Standalone tests
└── README.md            # This file
```

## Enable/Disable

The module can be enabled/disabled without code changes:

```python
from rlm_pot import set_enabled, is_enabled

set_enabled(False)  # Disable
set_enabled(True)   # Enable
```

Or simply don't import it - the integration code checks `RLM_POT_ENABLED`.

## Comparison: sed vs RLM-PoT

| Aspect | sed | RLM-PoT |
|--------|-----|---------|
| Pattern extraction | LLM "remembers" (fuzzy) | Python regex (exact) |
| String construction | Shell escaping | Python f-strings |
| Multi-line edits | Complex syntax | Native Python |
| Verification | Separate command | Built-in assertions |
| State persistence | None | Namespace variables |
