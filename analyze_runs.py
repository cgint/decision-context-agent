#!/usr/bin/env python3
"""
Analyze SWE-bench evaluation runs.

Collects statistics on action types, success rates, and failure patterns.

Usage:
    uv run python analyze_runs.py                           # Analyze latest run
    uv run python analyze_runs.py --latest                  # Same as above
    uv run python analyze_runs.py data/swebench_eval/20260113_165722  # Specific run
    uv run python analyze_runs.py data/swebench_eval/       # All runs in directory
    uv run python analyze_runs.py --failures-only           # Show only failures
    uv run python analyze_runs.py --json                    # Output as JSON
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class StepResult:
    """Result of a single step."""
    step: int
    action_type: str
    command: str
    command_intent: str
    success: bool
    issues: str
    needs_verification: bool
    instance_id: str
    run_id: str


@dataclass
class TokenUsage:
    """Token usage summary for an instance or run (best-effort)."""

    recorded: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0

    def normalized_total(self) -> int:
        if self.total_tokens:
            return self.total_tokens
        # NOTE: cached_tokens is usually a *breakdown* of prompt/input tokens
        # (e.g. Gemini prompt_tokens_details.cached_tokens), so it should not be
        # added to the total (would double-count).
        return self.input_tokens + self.output_tokens


@dataclass
class InstanceStats:
    """Statistics for a single instance."""
    instance_id: str
    total_steps: int = 0
    successful_steps: int = 0
    action_types: Counter = field(default_factory=Counter)
    failed_steps: list = field(default_factory=list)
    token_usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass
class RunStats:
    """Statistics for a single run."""
    run_id: str
    run_path: Path
    instances: dict = field(default_factory=dict)  # instance_id -> InstanceStats
    all_steps: list = field(default_factory=list)  # List[StepResult]
    
    @property
    def total_steps(self) -> int:
        return len(self.all_steps)
    
    @property
    def successful_steps(self) -> int:
        return sum(1 for s in self.all_steps if s.success)
    
    @property
    def action_type_counts(self) -> Counter:
        return Counter(s.action_type for s in self.all_steps)
    
    @property
    def command_intent_counts(self) -> Counter:
        return Counter(s.command_intent for s in self.all_steps if s.command_intent)
    
    def success_rate_by_action_type(self) -> dict:
        """Returns {action_type: (success, total, rate)}"""
        by_type = defaultdict(lambda: {"success": 0, "total": 0})
        for step in self.all_steps:
            by_type[step.action_type]["total"] += 1
            if step.success:
                by_type[step.action_type]["success"] += 1
        
        result = {}
        for action_type, counts in by_type.items():
            total = counts["total"]
            success = counts["success"]
            rate = success / total if total > 0 else 0
            result[action_type] = (success, total, rate)
        return result
    
    def success_rate_by_intent(self) -> dict:
        """Returns {intent: (success, total, rate)}"""
        by_intent = defaultdict(lambda: {"success": 0, "total": 0})
        for step in self.all_steps:
            if step.command_intent:
                by_intent[step.command_intent]["total"] += 1
                if step.success:
                    by_intent[step.command_intent]["success"] += 1
        
        result = {}
        for intent, counts in by_intent.items():
            total = counts["total"]
            success = counts["success"]
            rate = success / total if total > 0 else 0
            result[intent] = (success, total, rate)
        return result
    
    @property
    def failed_steps(self) -> list:
        return [s for s in self.all_steps if not s.success]

    @property
    def token_usage(self) -> TokenUsage:
        """Aggregate token usage across instances (if recorded)."""
        usage = TokenUsage()
        any_recorded = False
        for inst in self.instances.values():
            if getattr(inst, "token_usage", None) and inst.token_usage.recorded:
                any_recorded = True
                usage.input_tokens += inst.token_usage.input_tokens
                usage.output_tokens += inst.token_usage.output_tokens
                usage.cached_tokens += inst.token_usage.cached_tokens
                usage.reasoning_tokens += inst.token_usage.reasoning_tokens
                usage.total_tokens += inst.token_usage.normalized_total()
        usage.recorded = any_recorded
        return usage


def find_latest_run(base_dir: Path) -> Optional[Path]:
    """Find the most recent run directory."""
    if not base_dir.exists():
        return None
    
    # Look for directories that match the timestamp pattern (YYYYMMDD_HHMMSS)
    run_dirs = []
    for d in base_dir.iterdir():
        if d.is_dir() and len(d.name) == 15 and d.name[8] == "_":
            try:
                # Validate it's a timestamp-like name
                int(d.name[:8])
                int(d.name[9:])
                run_dirs.append(d)
            except ValueError:
                continue
    
    if not run_dirs:
        return None
    
    # Sort by name (which is timestamp) and return latest
    return sorted(run_dirs, key=lambda x: x.name, reverse=True)[0]


def parse_step(step_dir: Path, instance_id: str, run_id: str) -> Optional[StepResult]:
    """Parse a single step directory."""
    action_file = step_dir / "action_selector_output.json"
    outcome_file = step_dir / "outcome_output.json"
    
    if not action_file.exists() or not outcome_file.exists():
        return None
    
    try:
        with open(action_file) as f:
            action_data = json.load(f)
        with open(outcome_file) as f:
            outcome_data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None
    
    # Extract step number from directory name
    step_num = int(step_dir.name.replace("step_", ""))
    
    return StepResult(
        step=step_num,
        action_type=action_data.get("action_type", "unknown"),
        command=action_data.get("command", ""),
        command_intent=action_data.get("command_intent", ""),
        success=str(outcome_data.get("success", "")).lower() == "true",
        issues=outcome_data.get("issues", ""),
        needs_verification=str(outcome_data.get("needs_verification", "")).lower() == "true",
        instance_id=instance_id,
        run_id=run_id,
    )


def _safe_load_json(path: Path) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _coerce_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return 0
    return 0


def _parse_token_usage_from_obj(obj: Any) -> Optional[TokenUsage]:
    """
    Best-effort token usage parsing.

    Supports multiple schemas:
    - {"tokens": {"input": .., "output": .., "cached": .., "total": ..}}
    - {"total_input_tokens": .., "total_output_tokens": ..}
    - {"prompt_tokens": .., "completion_tokens": .., "total_tokens": ..}
    """
    if not isinstance(obj, dict):
        return None

    if isinstance(obj.get("tokens"), dict):
        tokens = obj["tokens"]
        usage = TokenUsage(
            recorded=True,
            input_tokens=_coerce_int(tokens.get("input") or tokens.get("prompt") or tokens.get("prompt_tokens")),
            output_tokens=_coerce_int(tokens.get("output") or tokens.get("completion") or tokens.get("completion_tokens")),
            cached_tokens=_coerce_int(tokens.get("cached") or tokens.get("cache")),
            total_tokens=_coerce_int(tokens.get("total") or tokens.get("total_tokens")),
        )
        if not usage.total_tokens:
            usage.total_tokens = usage.normalized_total()
        return usage

    if "total_input_tokens" in obj or "total_output_tokens" in obj:
        usage = TokenUsage(
            recorded=True,
            input_tokens=_coerce_int(obj.get("total_input_tokens")),
            output_tokens=_coerce_int(obj.get("total_output_tokens")),
        )
        usage.total_tokens = usage.normalized_total()
        return usage

    if "prompt_tokens" in obj or "completion_tokens" in obj or "total_tokens" in obj:
        usage = TokenUsage(
            recorded=True,
            input_tokens=_coerce_int(obj.get("prompt_tokens")),
            output_tokens=_coerce_int(obj.get("completion_tokens")),
            total_tokens=_coerce_int(obj.get("total_tokens")),
        )
        if not usage.total_tokens:
            usage.total_tokens = usage.normalized_total()
        return usage

    return None


def load_token_usage_by_instance(run_dir: Path) -> dict[str, TokenUsage]:
    """
    Load token usage persisted by upstream runners, if present.

    Current producers in this repo:
    - Terminal-Bench `results.json` format (dict with "results": [...]) includes
      total_input_tokens / total_output_tokens per trial.
    - Some session logs include {"tokens": {...}} per event (not linked to SWE-bench eval runs yet).

    For SWE-bench eval runs, this returns {} unless token fields are added to `results.json`.
    """
    results_path = run_dir / "results.json"
    if not results_path.exists():
        return {}

    data = _safe_load_json(results_path)
    if data is None:
        return {}

    items = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("results"), list):
        items = data["results"]
    else:
        return {}

    out: dict[str, TokenUsage] = {}
    for item in items:
        if not isinstance(item, dict):
            continue

        instance_id = item.get("instance_id") or item.get("task_id") or item.get("trial_name") or item.get("id")
        if not instance_id:
            continue
        instance_id = str(instance_id)

        usage = _parse_token_usage_from_obj(item)
        if usage and usage.recorded:
            out[instance_id] = usage

    return out


def _load_token_usage_from_lm_usage_events(agent_run_dir: Path) -> Optional[TokenUsage]:
    """
    Load best-effort token usage from agent_run/lm_usage_events.jsonl.

    This file is append-only and stores the *full* raw usage dict per LM call.
    We sum a few stable numeric fields for summary reporting (input/output/total,
    plus breakdowns like cached_tokens / reasoning_tokens when present).
    """
    path = agent_run_dir / "lm_usage_events.jsonl"
    if not path.exists():
        return None

    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    cached_tokens = 0
    reasoning_tokens = 0
    any_found = False

    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                usage_obj = event.get("usage")
                if not isinstance(usage_obj, dict) or not usage_obj:
                    continue

                for model_usage in usage_obj.values():
                    if not isinstance(model_usage, dict):
                        continue
                    any_found = True
                    input_tokens += _coerce_int(model_usage.get("prompt_tokens"))
                    output_tokens += _coerce_int(model_usage.get("completion_tokens"))
                    total_tokens += _coerce_int(model_usage.get("total_tokens"))

                    prompt_details = model_usage.get("prompt_tokens_details")
                    if isinstance(prompt_details, dict):
                        cached_tokens += _coerce_int(prompt_details.get("cached_tokens"))

                    completion_details = model_usage.get("completion_tokens_details")
                    if isinstance(completion_details, dict):
                        reasoning_tokens += _coerce_int(completion_details.get("reasoning_tokens"))
    except OSError:
        return None

    if not any_found:
        return None

    usage = TokenUsage(
        recorded=True,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
    )
    if not usage.total_tokens:
        usage.total_tokens = usage.normalized_total()
    return usage


def _load_token_usage_from_agent_run(agent_run_dir: Path) -> Optional[TokenUsage]:
    """
    Load token usage persisted by `online_replay_loop.py` as agent_run/token_usage.json.
    """
    usage_from_events = _load_token_usage_from_lm_usage_events(agent_run_dir)

    path = agent_run_dir / "token_usage.json"
    if path.exists():
        data = _safe_load_json(path)
        usage_from_totals = _parse_token_usage_from_obj(data) if isinstance(data, dict) else None
        if usage_from_totals and usage_from_totals.recorded:
            # Prefer totals from token_usage.json, but enrich with breakdown fields
            # computed from the raw per-call usage log.
            if usage_from_events and usage_from_events.recorded:
                usage_from_totals.cached_tokens = usage_from_events.cached_tokens
                usage_from_totals.reasoning_tokens = usage_from_events.reasoning_tokens
            return usage_from_totals

    return usage_from_events if usage_from_events and usage_from_events.recorded else None


def parse_instance(
    instance_dir: Path,
    run_id: str,
    *,
    token_usage: Optional[TokenUsage] = None,
) -> Optional[tuple[InstanceStats, list[StepResult]]]:
    """Parse a single instance directory."""
    agent_run_dir = instance_dir / "agent_run"
    steps_dir = agent_run_dir / "steps"
    
    if not steps_dir.exists():
        return None
    
    instance_id = instance_dir.name
    stats = InstanceStats(instance_id=instance_id)
    # Prefer token usage persisted alongside the agent run, if present.
    usage_from_agent_run = _load_token_usage_from_agent_run(agent_run_dir)
    if usage_from_agent_run:
        stats.token_usage = usage_from_agent_run
    elif token_usage and token_usage.recorded:
        stats.token_usage = token_usage
    steps = []
    
    for step_dir in sorted(steps_dir.iterdir()):
        if step_dir.is_dir() and step_dir.name.startswith("step_"):
            step_result = parse_step(step_dir, instance_id, run_id)
            if step_result:
                steps.append(step_result)
                stats.total_steps += 1
                stats.action_types[step_result.action_type] += 1
                if step_result.success:
                    stats.successful_steps += 1
                else:
                    stats.failed_steps.append(step_result)
    
    return stats, steps


def parse_run(run_dir: Path) -> Optional[RunStats]:
    """Parse a complete run directory."""
    instances_dir = run_dir / "instances"
    
    if not instances_dir.exists():
        return None
    
    run_id = run_dir.name
    stats = RunStats(run_id=run_id, run_path=run_dir)
    token_usage_by_instance = load_token_usage_by_instance(run_dir)
    
    for instance_dir in sorted(instances_dir.iterdir()):
        if instance_dir.is_dir():
            instance_id = instance_dir.name
            result = parse_instance(
                instance_dir,
                run_id,
                token_usage=token_usage_by_instance.get(instance_id),
            )
            if result:
                instance_stats, steps = result
                stats.instances[instance_stats.instance_id] = instance_stats
                stats.all_steps.extend(steps)
    
    return stats


def format_percentage(value: float) -> str:
    """Format a percentage value."""
    return f"{value * 100:.1f}%"


def print_run_stats(stats: RunStats, failures_only: bool = False) -> None:
    """Print statistics for a run in human-readable format."""
    print(f"\n{'=' * 60}")
    print(f"Run: {stats.run_id}")
    print(f"Path: {stats.run_path}")
    print(f"{'=' * 60}")
    
    if not stats.all_steps:
        print("No steps found.")
        return
    
    if not failures_only:
        # Action type distribution
        print(f"\n📊 Action Types ({stats.total_steps} total steps):")
        action_counts = stats.action_type_counts
        for action_type, count in sorted(action_counts.items(), key=lambda x: -x[1]):
            pct = count / stats.total_steps
            print(f"  {action_type:20s}: {count:4d} ({format_percentage(pct)})")
        
        # Success rate by action type
        print("\n✅ Success Rate by Action Type:")
        for action_type, (success, total, rate) in sorted(
            stats.success_rate_by_action_type().items(), key=lambda x: -x[1][2]
        ):
            print(f"  {action_type:20s}: {success:3d}/{total:3d} ({format_percentage(rate)})")
        
        # Command intent breakdown
        print("\n🎯 Command Intent Breakdown:")
        intent_rates = stats.success_rate_by_intent()
        for intent, (success, total, rate) in sorted(
            intent_rates.items(), key=lambda x: -x[1][1]
        ):
            failures = total - success
            fail_note = f" ← {failures} failures" if failures > 0 else ""
            print(f"  {intent:12s}: {total:3d} ({format_percentage(total / stats.total_steps)}){fail_note}")
        
        # Per-instance summary
        print(f"\n📦 Instances ({len(stats.instances)} total):")
        for instance_id, inst_stats in sorted(stats.instances.items()):
            python_edit_count = inst_stats.action_types.get("python_edit", 0) + inst_stats.action_types.get("eval_python", 0)
            eval_note = f" (python_edit: {python_edit_count}x)" if python_edit_count > 0 else ""
            success_rate = inst_stats.successful_steps / inst_stats.total_steps if inst_stats.total_steps > 0 else 0
            status = "✓" if success_rate == 1.0 else "⚠" if success_rate >= 0.8 else "✗"
            print(f"  {status} {instance_id}: {inst_stats.total_steps} steps, {inst_stats.successful_steps} success{eval_note}")

    # Token usage summary (if available)
    tu = stats.token_usage
    print("\n🔢 Token Usage:")
    if tu.recorded:
        print(f"  input:  {tu.input_tokens:,}")
        print(f"  output: {tu.output_tokens:,}")
        if tu.cached_tokens:
            print(f"  cached: {tu.cached_tokens:,}")
        if tu.reasoning_tokens:
            print(f"  reasoning: {tu.reasoning_tokens:,}")
        print(f"  total:  {tu.normalized_total():,}")
        if stats.total_steps > 0:
            avg = tu.normalized_total() / stats.total_steps
            print(f"  avg/step: {avg:,.1f}")
    else:
        print("  (no token usage found in run artifacts)")
    
    # Failed steps
    failed = stats.failed_steps
    if failed:
        print(f"\n❌ Failed Steps ({len(failed)} total):")
        for step in failed:
            cmd_preview = step.command[:50] + "..." if len(step.command) > 50 else step.command
            cmd_preview = cmd_preview.replace("\n", "\\n")
            issues_preview = step.issues[:60] if step.issues else "no issues recorded"
            print(f"  [{step.instance_id}] step_{step.step:03d}: {step.command_intent or 'unknown'} ({step.action_type})")
            print(f"      cmd: {cmd_preview}")
            print(f"      issue: {issues_preview}")
    elif failures_only:
        print("\n✅ No failed steps!")


def stats_to_dict(stats: RunStats) -> dict:
    """Convert stats to a dictionary for JSON output."""
    tu = stats.token_usage
    return {
        "run_id": stats.run_id,
        "run_path": str(stats.run_path),
        "total_steps": stats.total_steps,
        "successful_steps": stats.successful_steps,
        "success_rate": stats.successful_steps / stats.total_steps if stats.total_steps > 0 else 0,
        "token_usage": {
            "recorded": tu.recorded,
            "input_tokens": tu.input_tokens,
            "output_tokens": tu.output_tokens,
            "cached_tokens": tu.cached_tokens,
            "reasoning_tokens": tu.reasoning_tokens,
            "total_tokens": tu.normalized_total(),
        },
        "action_types": dict(stats.action_type_counts),
        "command_intents": dict(stats.command_intent_counts),
        "success_by_action_type": {
            k: {"success": v[0], "total": v[1], "rate": v[2]}
            for k, v in stats.success_rate_by_action_type().items()
        },
        "success_by_intent": {
            k: {"success": v[0], "total": v[1], "rate": v[2]}
            for k, v in stats.success_rate_by_intent().items()
        },
        "instances": {
            inst_id: {
                "total_steps": inst.total_steps,
                "successful_steps": inst.successful_steps,
                "action_types": dict(inst.action_types),
                "token_usage": {
                    "recorded": inst.token_usage.recorded,
                    "input_tokens": inst.token_usage.input_tokens,
                    "output_tokens": inst.token_usage.output_tokens,
                    "cached_tokens": inst.token_usage.cached_tokens,
                    "reasoning_tokens": inst.token_usage.reasoning_tokens,
                    "total_tokens": inst.token_usage.normalized_total(),
                },
            }
            for inst_id, inst in stats.instances.items()
        },
        "failed_steps": [
            {
                "instance_id": s.instance_id,
                "step": s.step,
                "action_type": s.action_type,
                "command_intent": s.command_intent,
                "command": s.command[:200],
                "issues": s.issues,
            }
            for s in stats.failed_steps
        ],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Analyze SWE-bench evaluation runs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Path to run directory, or parent directory containing multiple runs. "
             "If not specified, analyzes the latest run.",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Analyze the latest run (default if no path specified).",
    )
    parser.add_argument(
        "--failures-only",
        action="store_true",
        help="Only show failed steps.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON instead of human-readable format.",
    )
    parser.add_argument(
        "--base-dir",
        default="data/swebench_eval",
        help="Base directory for runs (default: data/swebench_eval).",
    )
    
    args = parser.parse_args()
    
    # Determine which path to analyze
    if args.path:
        target_path = Path(args.path)
    else:
        # Default to latest
        base_dir = Path(args.base_dir)
        target_path = find_latest_run(base_dir)
        if target_path is None:
            print(f"No runs found in {base_dir}", file=sys.stderr)
            sys.exit(1)
        print(f"Analyzing latest run: {target_path.name}")
    
    if not target_path.exists():
        print(f"Path does not exist: {target_path}", file=sys.stderr)
        sys.exit(1)
    
    # Check if this is a single run or a directory of runs
    all_stats = []
    
    if (target_path / "instances").exists():
        # Single run
        stats = parse_run(target_path)
        if stats:
            all_stats.append(stats)
    else:
        # Directory of runs - find all run subdirectories
        for subdir in sorted(target_path.iterdir()):
            if subdir.is_dir() and (subdir / "instances").exists():
                stats = parse_run(subdir)
                if stats:
                    all_stats.append(stats)
    
    if not all_stats:
        print(f"No valid runs found at {target_path}", file=sys.stderr)
        sys.exit(1)
    
    # Output results
    if args.json:
        output = [stats_to_dict(s) for s in all_stats]
        if len(output) == 1:
            output = output[0]
        print(json.dumps(output, indent=2))
    else:
        for stats in all_stats:
            print_run_stats(stats, failures_only=args.failures_only)
        
        # Summary if multiple runs
        if len(all_stats) > 1:
            print(f"\n{'=' * 60}")
            print(f"SUMMARY: {len(all_stats)} runs analyzed")
            print(f"{'=' * 60}")
            
            total_steps = sum(s.total_steps for s in all_stats)
            total_success = sum(s.successful_steps for s in all_stats)
            total_python_edit = sum(
                s.action_type_counts.get("python_edit", 0) + s.action_type_counts.get("eval_python", 0)
                for s in all_stats
            )
            total_shell = sum(s.action_type_counts.get("shell_command", 0) for s in all_stats)
            total_tokens_recorded = any(s.token_usage.recorded for s in all_stats)
            total_input_tokens = sum(s.token_usage.input_tokens for s in all_stats)
            total_output_tokens = sum(s.token_usage.output_tokens for s in all_stats)
            total_tokens = sum(s.token_usage.normalized_total() for s in all_stats)
            
            print(f"Total steps: {total_steps}")
            print(f"Overall success rate: {format_percentage(total_success / total_steps if total_steps > 0 else 0)}")
            print(f"python_edit usage: {total_python_edit} ({format_percentage(total_python_edit / total_steps if total_steps > 0 else 0)})")
            print(f"shell_command usage: {total_shell} ({format_percentage(total_shell / total_steps if total_steps > 0 else 0)})")
            print("\n🔢 Token Usage:")
            if total_tokens_recorded:
                print(f"  input:  {total_input_tokens:,}")
                print(f"  output: {total_output_tokens:,}")
                print(f"  total:  {total_tokens:,}")
                if total_steps > 0:
                    print(f"  avg/step: {(total_tokens / total_steps):,.1f}")
            else:
                print("  (no token usage found in run artifacts)")


if __name__ == "__main__":
    main()
