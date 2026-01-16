# SWE-bench Lite Setup Complete

The SWE-bench Lite evaluation has been set up for the online replay loop agent.

## Files Created

1. **run_swebench_eval.py** - Main evaluation script
2. **SWEBENCH_EVAL_README.md** - Comprehensive documentation
3. **quick_start_swebench.sh** - Quick start script
4. **test_swebench_script.py** - Test script to verify setup

## Quick Start

```bash
# Set your API key
export GEMINI_API_KEY="your-key-here"

# Run evaluation
python3 run_swebench_eval.py --num-instances 10
```

## What It Does

For each of the 10 SWE-bench Lite instances:
1. Clone the GitHub repository
2. Checkout the specific commit
3. Run online_replay_loop.py with the problem statement
4. Apply test patches and run pytest
5. Report pass/fail results

See SWEBENCH_EVAL_README.md for full documentation.
