# SWE-bench Lite Evaluation for Online Replay Loop

This document describes how to evaluate the online replay loop agent on SWE-bench Lite benchmark.

## Overview

The `run_swebench_eval.py` script evaluates the online replay loop agent on SWE-bench Lite instances. For each instance, it:

1. **Sets up the workspace**: Clones the repository and checks out the base commit
2. **Runs the agent**: Executes `online_replay_loop.py` with the problem statement
3. **Applies test patch**: Applies the test patch from SWE-bench
4. **Runs tests**: Executes pytest to check if the fix is correct
5. **Reports results**: Saves detailed results and prints a summary

## Requirements

- Python 3.12+
- Git
- Internet connection (to download dataset and clone repos)
- API key for Gemini (GEMINI_API_KEY environment variable)

## Installation

Install dependencies:

```bash
pip install -e .
```

This will install the `datasets` package along with other dependencies.

## Usage

### Basic usage (10 instances):

```bash
python3 run_swebench_eval.py
```

### Custom number of instances:

```bash
python3 run_swebench_eval.py --num-instances 5
```

### With different model:

```bash
python3 run_swebench_eval.py --model gemini-2.5-flash --max-steps 20
```

### Full options:

```bash
python3 run_swebench_eval.py \
  --num-instances 10 \
  --model gemini-2.5-flash \
  --max-steps 15 \
  --phase autopilot \
  --interaction-mode none \
  --output-dir data/swebench_eval \
  --cache-dir data/swebench_cache
```

## Output Structure

Results are saved in `data/swebench_eval/<timestamp>/`:

```
data/swebench_eval/20260112_120000/
├── config.json                    # Evaluation configuration
├── results.json                   # Summary results for all instances
├── instances/                     # Per-instance results
│   └── <instance_id>/
│       ├── instance.json          # Instance data
│       ├── agent_stdout.txt       # Agent stdout
│       ├── agent_stderr.txt       # Agent stderr
│       ├── agent_run/             # Agent execution artifacts
│       ├── test.patch             # Test patch applied
│       ├── test_stdout.txt        # Test output
│       └── test_stderr.txt        # Test errors
└── workspaces/                    # Cloned repositories
    └── <instance_id>/
        └── <repo_name>/
```

## Results

The script prints a summary at the end:

```
================================================================================
EVALUATION SUMMARY
================================================================================

Total instances: 10
Agent completed: 8/10 (80.0%)
Tests passed: 5/10 (50.0%)
Total time: 3600.0s (60.0m)
Average time per instance: 360.0s

Detailed results:
--------------------------------------------------------------------------------
PASS   astropy__astropy-12345                      (250.5s)
FAIL   django__django-13456                        (420.2s)
       Error: Tests failed with exit code 1
...
```

## Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `--num-instances` | 10 | Number of SWE-bench instances to evaluate |
| `--model` | `gemini-2.5-flash` | LLM model to use |
| `--max-steps` | 15 | Maximum reasoning steps for the agent |
| `--phase` | `autopilot` | Agent phase |
| `--interaction-mode` | `none` | Interaction mode (none/human) |
| `--agent-impl` | `online_replay` | Agent loop: `online_replay` (Manager→Actor) or `pi_rpc` (Pi vehicle via RPC) |
| `--pi-provider` | unset | (pi_rpc) Pi provider override; otherwise Pi uses local defaults |
| `--pi-model` | unset | (pi_rpc) Pi model override; otherwise Pi uses local defaults |
| `--pi-thinking-level` | unset | (pi_rpc) Pi thinking level override |
| `--pi-timeout-seconds` | 1800 | (pi_rpc) Timeout per instance |
| `--pi-disable-manager-bridge` | false | (pi_rpc) Run Pi without the Decision Context manager-bridge extension |
| `--output-dir` | `data/swebench_eval` | Output directory for results |
| `--cache-dir` | `data/swebench_cache` | Cache directory for dataset |

## Output Artifacts (Docker Mode)

For `--evaluation-mode docker`, the official SWE-bench harness produces most of the authoritative outcome info.
This repository persists the key harness outputs under:

- `data/swebench_eval/<eval_id>/swebench_reports/harness_stdout.txt`
- `data/swebench_eval/<eval_id>/swebench_reports/harness_stderr.txt`
- `data/swebench_eval/<eval_id>/swebench_reports/harness_report.json` (copy of the parsed harness report)

Per-instance agent artifacts are stored under:

- `data/swebench_eval/<eval_id>/instances/<instance_id>/model_patch.diff`
- `data/swebench_eval/<eval_id>/instances/<instance_id>/agent_run/` (agent transcript/logs; contents depend on `--agent-impl`)

## Troubleshooting

### Dataset download fails

If the dataset download fails, try manually downloading:

```bash
python3 -c "from datasets import load_dataset; load_dataset('princeton-nlp/SWE-bench_Lite', split='test')"
```

### Git clone fails

- Check internet connection
- Some repos may be private or archived
- The script has fallback logic for main/master branches

### Tests fail to run

- Ensure pytest is installed in the repo's environment
- Some repos may require additional setup (virtual env, dependencies)
- In `--evaluation-mode local`, check `test_stderr.txt` for details
- In `--evaluation-mode docker`, check:
  - `data/swebench_eval/<eval_id>/swebench_reports/harness_stdout.txt`
  - `data/swebench_eval/<eval_id>/swebench_reports/harness_stderr.txt`
  - `data/swebench_eval/<eval_id>/swebench_reports/harness_report.json`

### Harness reports “ERROR” (Docker mode)

If the summary shows `ERROR` for an instance, it usually means the official SWE-bench harness failed to complete that instance run (e.g., environment/build failure, crash while applying patch, crash during test invocation).
The quickest way to diagnose is to open the harness logs in `data/swebench_eval/<eval_id>/swebench_reports/`.

### Harness reports “EMPTY” (Docker mode)

If the summary shows `EMPTY`, the agent produced an empty patch (no code changes). The harness treats this as an “empty patch” outcome.
Check `data/swebench_eval/<eval_id>/instances/<instance_id>/model_patch.diff` and the agent logs in `data/swebench_eval/<eval_id>/instances/<instance_id>/agent_run/` to understand why no change was made.

### Agent timeout

- Default timeout is 30 minutes per instance
- Increase `max_steps` if the agent needs more iterations
- Check `agent_stderr.txt` for errors

## Notes

- The first run will download the SWE-bench Lite dataset (~300 instances)
- Each instance requires cloning a repository (can be slow)
- Evaluation of 10 instances takes approximately 1-2 hours
- Results are incremental - check `instances/` directory during run
- The agent runs without tests initially, tests are only run for evaluation

## Example Run

```bash
# Set up API key
export GEMINI_API_KEY="your-api-key"

# Run evaluation on 10 instances
python3 run_swebench_eval.py --num-instances 10

# Check results
cat data/swebench_eval/$(ls -t data/swebench_eval/ | head -1)/results.json
```

## Integration with CI/CD

Add to your CI pipeline:

```yaml
- name: Run SWE-bench evaluation
  run: |
    python3 run_swebench_eval.py --num-instances 5
  env:
    GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
```

## Performance Metrics

The evaluation tracks:
- **Agent success rate**: Did the agent complete without errors?
- **Test pass rate**: Did the agent's fix pass the tests?
- **Elapsed time**: Time per instance and total
- **Error messages**: Detailed failure reasons

## Next Steps

After running the evaluation:
1. Review `results.json` for success rates
2. Analyze failed instances in `instances/*/` directories
3. Check agent reasoning traces in `agent_run/trace.md`
4. Iterate on agent prompts/configuration based on failure patterns
5. Compare results across different models/configurations
