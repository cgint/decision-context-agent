#!/usr/bin/env python3
"""
Compare full vs minimal context packs for a session.

Reads minimal_context_packs.md and action_packs_raw.md to produce a
comparison report with ratio flags and type-level summaries.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Dict, List


@dataclass
class MinimalStep:
    step_id: int
    full_tokens: int
    minimal_tokens: int
    ratio: float


@dataclass
class ActionStep:
    step_id: int
    step_type: str
    subtype: str


def parse_minimal(md_path: Path) -> Dict[int, MinimalStep]:
    text = md_path.read_text(encoding="utf-8")
    steps: Dict[int, MinimalStep] = {}
    step_blocks = re.split(r"^## Step ", text, flags=re.M)
    for block in step_blocks[1:]:
        header, *rest = block.splitlines()
        step_id = int(header.strip().split()[0])
        full_match = re.search(r"full_tokens:\s*(\d+)", block)
        minimal_match = re.search(r"minimal_tokens:\s*(\d+)", block)
        ratio_match = re.search(r"ratio:\s*([0-9.]+)", block)
        if not (full_match and minimal_match and ratio_match):
            continue
        steps[step_id] = MinimalStep(
            step_id=step_id,
            full_tokens=int(full_match.group(1)),
            minimal_tokens=int(minimal_match.group(1)),
            ratio=float(ratio_match.group(1)),
        )
    return steps


def parse_actions(md_path: Path) -> Dict[int, ActionStep]:
    text = md_path.read_text(encoding="utf-8")
    steps: Dict[int, ActionStep] = {}
    step_blocks = re.split(r"^## Step ", text, flags=re.M)
    for block in step_blocks[1:]:
        header, *rest = block.splitlines()
        step_id = int(header.strip().split()[0])
        step_type = "unknown"
        subtype = "unknown"
        if "(event" in header and "non-tool" in header:
            step_type = "non_tool"
        elif "(event" in header and "tool" in header:
            step_type = "tool"
        type_match = re.search(r"- type:\s*([a-z_]+)", block)
        if type_match:
            subtype = type_match.group(1)
        tool_match = re.search(r"- tool:\s*([a-z_]+)", block)
        if step_type == "tool" and tool_match:
            subtype = f"tool:{tool_match.group(1)}"
        steps[step_id] = ActionStep(step_id=step_id, step_type=step_type, subtype=subtype)
    return steps


def summarize_by_type(min_steps: Dict[int, MinimalStep], action_steps: Dict[int, ActionStep]):
    buckets: Dict[str, List[MinimalStep]] = {}
    for step_id, min_step in min_steps.items():
        action = action_steps.get(step_id)
        key = action.subtype if action else "unknown"
        buckets.setdefault(key, []).append(min_step)

    rows = []
    for key, items in sorted(buckets.items(), key=lambda x: x[0]):
        ratios = [s.ratio for s in items]
        rows.append(
            {
                "type": key,
                "steps": len(items),
                "avg_ratio": mean(ratios),
                "min_ratio": min(ratios),
                "max_ratio": max(ratios),
            }
        )
    return rows


def render_report(
    session_name: str,
    minimal_path: Path,
    action_path: Path,
    min_steps: Dict[int, MinimalStep],
    action_steps: Dict[int, ActionStep],
) -> str:
    inflated = [s for s in min_steps.values() if s.ratio > 1.0]
    heavy = [s for s in min_steps.values() if s.ratio >= 0.75]
    ratios = [s.ratio for s in min_steps.values()]
    avg_ratio = mean(ratios) if ratios else 0.0
    by_type = summarize_by_type(min_steps, action_steps)

    lines = [
        "# Minimal Context Comparison Report",
        "",
        f"Session: {session_name}",
        f"Minimal packs: {minimal_path}",
        f"Action packs: {action_path}",
        "",
        "## Summary",
        "",
        f"- steps_compared: {len(min_steps)}",
        f"- avg_ratio: {avg_ratio:.2f}",
        f"- inflated_steps (ratio > 1.0): {len(inflated)}",
        f"- heavy_steps (ratio >= 0.75): {len(heavy)}",
        "",
        "## Inflated Steps (ratio > 1.0)",
        "",
    ]

    if inflated:
        for step in sorted(inflated, key=lambda s: s.step_id):
            action = action_steps.get(step.step_id)
            subtype = action.subtype if action else "unknown"
            lines.append(
                f"- step {step.step_id:02d}: ratio={step.ratio:.2f} "
                f"full={step.full_tokens} minimal={step.minimal_tokens} type={subtype}"
            )
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## By Step Type",
            "",
            "| type | steps | avg_ratio | min_ratio | max_ratio |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in by_type:
        lines.append(
            f"| {row['type']} | {row['steps']} | {row['avg_ratio']:.2f} | "
            f"{row['min_ratio']:.2f} | {row['max_ratio']:.2f} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation (draft)",
            "",
            "- Ratios << 1.0 indicate strong compression; these steps likely only need goal + immediate target.",
            "- Ratios > 1.0 indicate overstuffing in minimal packs; tighten prompt or add hard caps.",
            "- Tool-heavy steps benefit most from minimal context; reasoning steps often need more.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare minimal vs full context packs for a session."
    )
    parser.add_argument("--session-dir", required=True, help="Path to session dir")
    parser.add_argument(
        "--minimal",
        help="Path to minimal_context_packs.md (default: [session-dir]/minimal_context_packs.md).",
    )
    parser.add_argument(
        "--actions",
        help="Path to action_packs_raw.md (default: [session-dir]/action_packs_raw.md).",
    )
    parser.add_argument(
        "--output",
        help="Path to write comparison report (default: session dir).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    session_dir = Path(args.session_dir)
    minimal_path = Path(args.minimal) if args.minimal else (session_dir / "minimal_context_packs.md")
    action_path = Path(args.actions) if args.actions else (session_dir / "action_packs_raw.md")
    if not minimal_path.exists():
        raise SystemExit(f"Missing minimal packs: {minimal_path}")
    if not action_path.exists():
        raise SystemExit(f"Missing action packs: {action_path}")

    min_steps = parse_minimal(minimal_path)
    action_steps = parse_actions(action_path)
    report = render_report(
        session_dir.name, minimal_path, action_path, min_steps, action_steps
    )

    output_path = (
        Path(args.output)
        if args.output
        else session_dir / "minimal_context_comparison.md"
    )
    output_path.write_text(report, encoding="utf-8")
    print(f"Wrote comparison report to {output_path}")


if __name__ == "__main__":
    main()
