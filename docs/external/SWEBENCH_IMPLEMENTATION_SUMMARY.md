# SWE-bench Lite Evaluation Implementation Summary

## Overview

A complete evaluation framework has been implemented to test the online replay loop agent against the SWE-bench Lite benchmark. This enables systematic evaluation of the agent's ability to fix real-world GitHub issues from popular Python repositories.

## What Was Implemented

### 1. Main Evaluation Script: `run_swebench_eval.py`

**Features:**
- Downloads SWE-bench Lite dataset (300 instances)
- Evaluates first N instances (default: 10)
- For each instance:
  - Clones repository and checks out base commit
  - Runs `online_replay_loop.py` with the problem statement
  - Applies test patches
  - Runs pytest to verify the fix
  - Records detailed results
- Generates comprehensive JSON output with all results
- Prints summary statistics

**Key Functions:**
- `setup_workspace()` - Clone repo and checkout commit
- `run_agent()` - Execute online_replay_loop.py
- `run_tests()` - Apply patches and run pytest
- `evaluate_instance()` - Complete evaluation pipeline
- `load_swebench_lite()` - Load dataset with auto-install
- `print_summary()` - Display results

**Design Decisions:**
- Uses subprocess instead of tmux (compatible with OpenCode/ACP)
- 30-minute timeout per instance (configurable)
- Incremental results saved per instance
- Comprehensive error handling and logging
- Graceful fallbacks (main/master branch, pytest discovery)

### 2. Dependencies: Updated `pyproject.toml`

Added `datasets>=3.2.0` to enable SWE-bench Lite dataset loading via HuggingFace.

### 3. Documentation: `SWEBENCH_EVAL_README.md`

**Comprehensive guide covering:**
- Overview and requirements
- Installation instructions
- Usage examples (basic and advanced)
- Output structure explanation
- Configuration options table
- Troubleshooting guide
- Integration with CI/CD
- Performance metrics
- Next steps for iteration

### 4. Quick Start Script: `quick_start_swebench.sh`

**Interactive script that:**
- Checks for GEMINI_API_KEY
- Verifies Python version
- Installs dependencies if needed
- Prompts user before starting
- Runs evaluation with sensible defaults
- Reports completion

### 5. Test Script: `test_swebench_script.py`

**Validation script that:**
- Tests imports
- Verifies all functions exist
- Checks dataclass definitions
- Tests --help output
- Reports pass/fail status

### 6. Benchmark Documentation: `benchmarks/SWEBENCH_SETUP.md`

Quick reference for the evaluation setup and integration points.

## Usage

### Basic Usage
```bash
# Set API key
export GEMINI_API_KEY="your-key"

# Run evaluation
python3 run_swebench_eval.py --num-instances 10
```

### Quick Start
```bash
./quick_start_swebench.sh
```

### Custom Configuration
```bash
python3 run_swebench_eval.py \
  --num-instances 5 \
  --model gemini-2.5-flash \
  --max-steps 20 \
  --phase autopilot
```

## Output Structure

Results saved to `data/swebench_eval/<timestamp>/`:
```
├── config.json          # Evaluation configuration
├── results.json         # Complete results for all instances
├── instances/           # Per-instance detailed results
│   └── <instance_id>/
│       ├── instance.json
│       ├── agent_stdout.txt
│       ├── agent_stderr.txt
│       ├── agent_run/   # Full agent execution trace
│       ├── test.patch
│       ├── test_stdout.txt
│       └── test_stderr.txt
└── workspaces/          # Cloned repositories
    └── <instance_id>/
```

## Key Metrics

The evaluation tracks:
- **Agent success rate**: Completion without errors
- **Test pass rate**: Fixes that pass tests
- **Elapsed time**: Per instance and total
- **Error messages**: Detailed failure analysis

## Integration with Existing Code

The script seamlessly integrates with:
- `online_replay_loop.py` - Main agent execution
- `config.py` - DSPy and Gemini configuration
- `STEP_BY_STEP_REASON_RULES.md` - Agent reasoning rules
- Git/pytest standard tooling

## Advantages Over Terminal-Bench

1. **No tmux dependency** - Direct subprocess execution
2. **OpenCode/ACP compatible** - Works with manager-actor pattern
3. **Standard format** - Easy comparison with other SWE-bench results
4. **Real-world tasks** - GitHub issues from Django, Flask, scikit-learn, etc.
5. **Clear success criteria** - Tests either pass or fail
6. **Industry standard** - SWE-bench is widely used benchmark

## Testing

All components verified:
```bash
python3 test_swebench_script.py
# All tests PASSED ✓
```

## Expected Performance

- **Duration**: 1-2 hours for 10 instances
- **Success rate baseline**: 30-50% (typical for new agents)
- **Improvement potential**: Iterative refinement of prompts/config

## Next Steps

1. **Run initial evaluation**: `./quick_start_swebench.sh`
2. **Analyze results**: Check `results.json` and agent traces
3. **Identify patterns**: Review failed instances
4. **Iterate**: Adjust prompts, max_steps, or model
5. **Compare**: Test different configurations
6. **Scale**: Increase to 50, 100, or all 300 instances

## Example Output

```
================================================================================
SWE-BENCH LITE EVALUATION
================================================================================
Evaluation ID: 20260112_120000
Model: gemini-2.5-flash
Max steps: 15
Num instances: 10
Output dir: data/swebench_eval/20260112_120000
================================================================================

[1/10] Evaluating astropy__astropy-12345
================================================================================
  Cloning astropy/astropy...
  Checking out commit abc123...
  Running agent (max 15 steps)...
  Applying test patch...
  Running tests...
  Tests PASSED! (245.3s)

...

================================================================================
EVALUATION SUMMARY
================================================================================

Total instances: 10
Agent completed: 9/10 (90.0%)
Tests passed: 6/10 (60.0%)
Total time: 3245.2s (54.1m)
Average time per instance: 324.5s

Detailed results:
--------------------------------------------------------------------------------
PASS   astropy__astropy-12345                      (245.3s)
PASS   django__django-13456                        (312.1s)
FAIL   flask__flask-3789                           (401.2s)
       Error: Tests failed with exit code 1
...

Results saved to: data/swebench_eval/20260112_120000/results.json
```

## Files Created

1. `run_swebench_eval.py` - Main evaluation script (16KB)
2. `SWEBENCH_EVAL_README.md` - Documentation (5.6KB)
3. `quick_start_swebench.sh` - Quick start (1.7KB)
4. `test_swebench_script.py` - Tests (1.9KB)
5. `benchmarks/SWEBENCH_SETUP.md` - Setup guide (811B)
6. `pyproject.toml` - Updated with datasets dependency
7. `SWEBENCH_IMPLEMENTATION_SUMMARY.md` - This file

## Validation

✓ Script syntax verified (py_compile)
✓ All imports work
✓ Help output functional
✓ All functions exist
✓ Dependencies documented
✓ Comprehensive documentation created
✓ Quick start script functional
✓ Test script passes

## Implementation Complete

The SWE-bench Lite evaluation framework is fully implemented and ready to use. 
Run `./quick_start_swebench.sh` to begin evaluation.
