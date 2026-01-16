# SWE-bench Local Evaluation: Findings and Solutions

This document summarizes the challenges encountered and solutions implemented while setting up local SWE-bench evaluation without Docker.

## Overview

SWE-bench is a benchmark for evaluating AI agents on real-world software engineering tasks. The official harness uses **Docker containers** to provide reproducible, isolated environments for each evaluation instance. We attempted to run evaluations locally without Docker, which revealed several environment isolation challenges.

## Problem Statement

Running `run_swebench_eval.py` with our agent on the `astropy__astropy-12907` instance resulted in test failures due to environment configuration issues, not agent performance.

---

## Issues Encountered and Solutions

### Issue 1: pytest Not Found in Virtual Environment

**Symptom:**
```
/Users/.../decision-context-tracing/.venv/bin/python3: No module named pytest
```

**Root Cause:**
When running via `uv run python run_swebench_eval.py`, subprocess calls to `python3` resolved to the project's venv Python, not the system Python where we installed pytest.

**Solution:**
Create a dedicated per-instance virtual environment for each SWE-bench instance:

```python
def setup_instance_venv(workspace_dir: Path) -> Path:
    venv_dir = (workspace_dir / ".test_venv").resolve()
    subprocess.run(["/usr/bin/python3", "-m", "venv", str(venv_dir)])
    return venv_dir
```

Key insight: Use `/usr/bin/python3` (system Python) to create the venv, avoiding inheritance from the project's uv-managed environment.

---

### Issue 2: Symlink Resolution Breaking venv

**Symptom:**
```
/Library/Developer/CommandLineTools/.../python3.9: No module named pytest
```

**Root Cause:**
Using `.resolve()` on the venv's Python path followed the symlink chain to the actual system Python binary, which doesn't have access to the venv's site-packages.

**Solution:**
Do NOT use `.resolve()` on venv executable paths:

```python
# WRONG - resolves symlinks, breaks venv isolation
venv_python = (venv_dir / "bin" / "python").resolve()

# CORRECT - keeps symlink, venv works properly
venv_python = venv_dir / "bin" / "python"
```

---

### Issue 3: numpy Version Incompatibility

**Symptom:**
```
DeprecationWarning: numpy.core is deprecated and has been renamed to numpy._core
```

**Root Cause:**
Old astropy code (v4.3 from 2022) uses `np.core.fromnumeric` which was deprecated in numpy 2.x.

**Solution:**
Pin numpy to version < 2:

```python
["pip", "install", "numpy<2", ...]
```

---

### Issue 4: setuptools.dep_util Removed

**Symptom:**
```
ModuleNotFoundError: No module named 'setuptools.dep_util'
```

**Root Cause:**
astropy 4.3's build scripts use `setuptools.dep_util` which was removed in setuptools 66+.

**Solution:**
Pin setuptools to version < 66:

```python
["pip", "install", "setuptools<66", ...]
```

---

### Issue 5: pip Build Isolation Using Wrong setuptools

**Symptom:**
Even with `setuptools<66` installed in the venv, pip's isolated build environment used newer setuptools.

**Root Cause:**
`pip install -e .` creates an isolated build environment that installs its own dependencies from `pyproject.toml`'s `[build-system]` section, ignoring what's in the venv.

**Attempted Solutions:**
1. `--no-build-isolation` flag - Failed because old setuptools doesn't support PEP 660 editable installs
2. `python setup.py develop --no-deps` - Works with proper CFLAGS

---

### Issue 6: Clang Compiler Strictness on macOS (Xcode 15+)

**Symptom:**
```
error: incompatible function pointer types initializing 'traverseproc'
```

**Root Cause:**
macOS Sonoma/Ventura with Xcode 15+ ships with a stricter clang that treats `-Wincompatible-function-pointer-types` as an error by default. Old C code patterns in astropy 4.3 trigger this.

**Important:** This is NOT ARM/Apple Silicon specific - it affects both ARM and Intel Macs running modern macOS.

**Solution:**
Set CFLAGS to downgrade these errors to warnings:

```python
env["CFLAGS"] = "-Wno-error=incompatible-function-pointer-types -Wno-error=incompatible-pointer-types"
```

---

### Issue 7: Missing astropy Dependencies

**Symptom:**
```
ModuleNotFoundError: No module named 'erfa'
ModuleNotFoundError: No module named 'yaml'
```

**Root Cause:**
astropy has runtime dependencies that aren't automatically installed when using `setup.py develop`.

**Solution:**
Explicitly install required dependencies:

```python
["pip", "install", "pyerfa", "pyyaml", ...]
```

---

### Issue 8: pytest Configuration Conflicts

**Symptom:**
```
error: unrecognized arguments: --doctest-rst
```

**Root Cause:**
astropy's `setup.cfg` contains pytest `addopts` that include plugins we don't have installed.

**Solution:**
Override pytest's addopts configuration:

```python
["pytest", "-xvs", "-p", "no:doctest", "--override-ini=addopts=", ...]
```

---

## Final Working Configuration

The complete solution requires:

1. **Per-instance virtual environment** using `/usr/bin/python3`
2. **Pinned dependencies:**
   - `numpy<2`
   - `setuptools<66`
   - `pytest`, `hypothesis`
   - `pyerfa`, `pyyaml` (for astropy)
   - `setuptools_scm`, `extension-helpers`, `cython` (build tools)

3. **Compiler flags:**
   ```
   CFLAGS="-Wno-error=incompatible-function-pointer-types -Wno-error=incompatible-pointer-types"
   ```

4. **Build command:**
   ```bash
   python setup.py develop --no-deps
   ```

5. **Test command:**
   ```bash
   python -m pytest -xvs -p no:doctest --override-ini=addopts= <test_files>
   ```

---

## Why SWE-bench Uses Docker

This debugging journey illustrates exactly why SWE-bench uses Docker:

| Challenge | Docker Solution |
|-----------|-----------------|
| Dependency version conflicts | Pre-configured images per repository version |
| Compiler incompatibilities | Consistent Linux/GCC environment |
| Build tool requirements | All tools pre-installed in images |
| Python version requirements | Specific Python versions per instance |
| OS-specific issues | Consistent Ubuntu environment |

The official SWE-bench harness uses a layered Docker architecture:
1. **Base images** - Language/tooling support
2. **Environment images** - Repository-specific dependencies
3. **Instance images** - Problem-specific configurations

---

## Recommendations

### For Local Development/Testing:
- Use the fixes documented here for quick local iteration
- Accept that some instances may not work without Docker
- Focus on instances that don't require complex C extensions

### For Production Evaluation:
- Use Docker-based SWE-bench harness for reproducible results
- Follow official SWE-bench installation guide
- Use pre-built Docker images when available

### For This Project:
- The local evaluation setup is now working for astropy
- May need additional fixes for other SWE-bench instances
- Consider adding instance-specific dependency handling

---

## Files Modified

- `run_swebench_eval.py` - Added per-instance venv, dependency pinning, CFLAGS, pytest options

## Test Verification

After all fixes, the test output shows actual test execution:

```
FAILED astropy/modeling/tests/test_separable.py::test_separable[compound_model6-result6]
 x: array([False, False, False, False])  ← Current (buggy) output
 y: array([False, False,  True,  True])  ← Expected (fixed) output
```

This confirms:
- ✅ Environment setup is correct
- ✅ Tests are running against the code
- ❌ Agent didn't apply the fix (expected, as this is what SWE-bench evaluates)

---

## Date

2026-01-13
