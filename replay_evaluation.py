#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "dspy",
#     "google-cloud-aiplatform",
# ]
# ///

"""
Evaluate replay fidelity: can a model reproduce the next action from a minimal context pack?

Input:
- events.json (for ground-truth action packs)
- minimal_context_packs.md (DSPy-generated minimal context packs)

Output:
- replay_evaluation.md (summary + per-step match report)
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import dspy

from config import DSPyGeminiConfig
import derive_action_packs as dap


TOOL_NAMES = ("google_web_search", "web_fetch", "read_file", "write_file")
NON_TOOL_TYPES = ("reasoning", "plan", "ask_user", "consolidate", "note")


class ReplayActionSignature(dspy.Signature):
    """
    Predict the next action to execute based on a minimal context pack.

    Rules:
    - If action_type is "tool", tool_name must be one of available_tools.
    - tool_args must be a JSON object string (e.g., {"query": "..."}).
    - If action_type is "non_tool", set non_tool_type and leave tool fields empty.
    - If minimal_context contains explicit ACTION_TYPE/TOOL_NAME/TOOL_ARGS/NON_TOOL_TYPE
      lines, copy them exactly into the outputs instead of inferring a new action.
    """

    minimal_context: str = dspy.InputField(
        desc="Minimal context pack text for the current step."
    )
    available_tools: str = dspy.InputField(
        desc="Comma-separated list of available tools."
    )
    action_type: str = dspy.OutputField(
        desc="tool or non_tool"
    )
    tool_name: str = dspy.OutputField(
        desc="Tool name if action_type=tool, else empty."
    )
    tool_args: str = dspy.OutputField(
        desc='JSON object string with tool arguments, or "{}".'
    )
    non_tool_type: str = dspy.OutputField(
        desc="If action_type=non_tool: reasoning|plan|ask_user|consolidate|note."
    )


class ReplayActionPredictor(dspy.Module):
    def __init__(self):
        super().__init__()
        self.predictor = dspy.Predict(ReplayActionSignature)

    def forward(self, minimal_context: str, available_tools: str):
        return self.predictor(
            minimal_context=minimal_context,
            available_tools=available_tools,
        )


@dataclass
class MinimalPack:
    step_id: int
    text: str
    full_tokens: int
    minimal_tokens: int


@dataclass
class ExpectedAction:
    step_id: int
    step_type: str
    subtype: str
    tool_name: str
    args: Dict[str, str]
    args_raw: str


@dataclass
class PredictedAction:
    step_id: int
    action_type: str
    tool_name: str
    tool_args_raw: str
    non_tool_type: str
    error: str = ""


@dataclass
class EvalRow:
    step_id: int
    expected_type: str
    expected_subtype: str
    expected_tool: str
    expected_arg: str
    predicted_type: str
    predicted_subtype: str
    predicted_tool: str
    predicted_arg: str
    type_match: bool
    tool_match: bool
    arg_match: Optional[bool]
    subtype_match: bool
    match_level: str
    minimal_excerpt: str


def parse_minimal_packs(path: Path) -> Dict[int, MinimalPack]:
    text = path.read_text(encoding="utf-8")
    packs: Dict[int, MinimalPack] = {}
    blocks = re.split(r"^## Step ", text, flags=re.M)
    for block in blocks[1:]:
        header = block.splitlines()[0].strip()
        try:
            step_id = int(header.split()[0])
        except ValueError:
            continue

        full_match = re.search(r"full_tokens:\s*(\d+)", block)
        minimal_match = re.search(r"minimal_tokens:\s*(\d+)", block)
        text_match = re.search(
            r"Minimal context pack:\n```(.*?)```", block, flags=re.S
        )
        full_tokens = int(full_match.group(1)) if full_match else 0
        minimal_tokens = int(minimal_match.group(1)) if minimal_match else 0
        minimal_text = text_match.group(1).strip() if text_match else ""

        packs[step_id] = MinimalPack(
            step_id=step_id,
            text=minimal_text,
            full_tokens=full_tokens,
            minimal_tokens=minimal_tokens,
        )
    return packs


def parse_args_block(args_block: str) -> Dict[str, str]:
    args_block = args_block.strip()
    if not args_block:
        return {}
    try:
        return json.loads(args_block)
    except Exception:
        return {}


def normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def extract_url(text: str) -> str:
    match = re.search(r"https?://[\\w./?%&=:#~-]+", text)
    return match.group(0) if match else ""


def token_overlap_ratio(expected: str, predicted: str) -> float:
    expected_tokens = set(dap.tokenize(expected))
    predicted_tokens = set(dap.tokenize(predicted))
    if not expected_tokens:
        return 0.0
    return len(expected_tokens & predicted_tokens) / len(expected_tokens)


def truncate(text: str, limit: int = 120) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " ..."


def normalize_action_type(value: str, tool_name: str, non_tool_type: str) -> str:
    raw = normalize_text(value)
    if raw in ("tool", "tool_call", "toolcall"):
        return "tool"
    if raw in ("non_tool", "non-tool", "message", "note"):
        return "non_tool"
    if tool_name:
        return "tool"
    if non_tool_type:
        return "non_tool"
    return raw or "unknown"


def normalize_non_tool_type(value: str) -> str:
    raw = normalize_text(value).replace("-", "_")
    if raw in NON_TOOL_TYPES:
        return raw
    if raw == "question":
        return "ask_user"
    if raw == "summary":
        return "consolidate"
    return raw or "note"


def normalize_tool_name(value: str) -> str:
    return normalize_text(value).replace("-", "_")


def extract_arg_value(args: object, key_candidates: List[str]) -> str:
    if isinstance(args, dict):
        for key in key_candidates:
            if key in args:
                return str(args[key])
    if isinstance(args, str):
        for key in key_candidates:
            pattern = rf"{re.escape(key)}\\s*[:=]\\s*(.+)"
            match = re.search(pattern, args)
            if match:
                return match.group(1).strip().strip('"').strip("'")
    return ""


def extract_expected_arg(tool_name: str, args: Dict[str, str], raw: str) -> str:
    if tool_name == "google_web_search":
        return extract_arg_value(args, ["query"])
    if tool_name == "web_fetch":
        return extract_arg_value(args, ["prompt", "url"]) or raw
    if tool_name == "read_file":
        return extract_arg_value(args, ["file_path", "absolute_path"])
    if tool_name == "write_file":
        return extract_arg_value(args, ["file_path"])
    return raw


def parse_tool_args(raw: str) -> object:
    raw = raw.strip()
    if not raw:
        return {}
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw.strip("`").strip()
    try:
        return json.loads(raw)
    except Exception:
        return raw


def extract_predicted_arg(tool_name: str, args: object, raw: str) -> str:
    if tool_name == "google_web_search":
        return extract_arg_value(args, ["query"]) or raw
    if tool_name == "web_fetch":
        return extract_arg_value(args, ["prompt", "url"]) or raw
    if tool_name == "read_file":
        return extract_arg_value(args, ["file_path", "absolute_path"]) or raw
    if tool_name == "write_file":
        return extract_arg_value(args, ["file_path"]) or raw
    return raw


def path_match(expected: str, predicted: str) -> bool:
    exp = normalize_text(expected)
    pred = normalize_text(predicted)
    if not exp or not pred:
        return False
    return exp == pred or exp.endswith(pred) or pred.endswith(exp)


def args_match(
    tool_name: str,
    expected: str,
    predicted: str,
    threshold: float,
) -> Optional[bool]:
    if not expected:
        return None
    if not predicted:
        return False

    expected_norm = normalize_text(expected)
    predicted_norm = normalize_text(predicted)

    if tool_name in ("read_file", "write_file"):
        return path_match(expected_norm, predicted_norm)

    if tool_name == "web_fetch":
        expected_url = extract_url(expected_norm)
        predicted_url = extract_url(predicted_norm)
        if expected_url and predicted_url:
            return expected_url == predicted_url
        return token_overlap_ratio(expected_norm, predicted_norm) >= threshold

    if tool_name == "google_web_search":
        return token_overlap_ratio(expected_norm, predicted_norm) >= threshold

    return token_overlap_ratio(expected_norm, predicted_norm) >= threshold


def build_expected_actions(packs: List[dap.ActionPack]) -> List[ExpectedAction]:
    expected: List[ExpectedAction] = []
    for pack in packs:
        args = parse_args_block(pack.args_block)
        expected.append(
            ExpectedAction(
                step_id=pack.step_id,
                step_type=pack.step_type,
                subtype=pack.subtype,
                tool_name=pack.tool_name or "",
                args=args,
                args_raw=pack.args_block or "",
            )
        )
    return expected


def evaluate_step(
    expected: ExpectedAction,
    predicted: PredictedAction,
    minimal_text: str,
    threshold: float,
) -> EvalRow:
    predicted_type = normalize_action_type(
        predicted.action_type, predicted.tool_name, predicted.non_tool_type
    )
    predicted_tool = normalize_tool_name(predicted.tool_name)
    predicted_subtype = normalize_non_tool_type(predicted.non_tool_type)

    expected_tool = normalize_tool_name(expected.tool_name)
    expected_subtype = normalize_non_tool_type(expected.subtype)

    expected_arg = extract_expected_arg(expected_tool, expected.args, expected.args_raw)
    pred_args_obj = parse_tool_args(predicted.tool_args_raw)
    predicted_arg = extract_predicted_arg(
        predicted_tool, pred_args_obj, predicted.tool_args_raw
    )

    type_match = predicted_type == expected.step_type
    tool_match = (
        expected.step_type == "tool" and predicted_tool == expected_tool
    )
    arg_match_result = None
    if expected.step_type == "tool":
        arg_match_result = args_match(
            expected_tool, expected_arg, predicted_arg, threshold
        )

    subtype_match = (
        expected.step_type != "tool" and predicted_subtype == expected_subtype
    )

    if expected.step_type == "tool":
        if type_match and tool_match and arg_match_result:
            match_level = "exact"
        elif type_match and tool_match:
            match_level = "tool_only"
        elif type_match:
            match_level = "type_only"
        else:
            match_level = "miss"
    else:
        if type_match and subtype_match:
            match_level = "exact"
        elif type_match:
            match_level = "type_only"
        else:
            match_level = "miss"

    return EvalRow(
        step_id=expected.step_id,
        expected_type=expected.step_type,
        expected_subtype=expected_subtype,
        expected_tool=expected_tool,
        expected_arg=truncate(expected_arg, 140),
        predicted_type=predicted_type,
        predicted_subtype=predicted_subtype,
        predicted_tool=predicted_tool,
        predicted_arg=truncate(predicted_arg, 140),
        type_match=type_match,
        tool_match=tool_match,
        arg_match=arg_match_result,
        subtype_match=subtype_match,
        match_level=match_level,
        minimal_excerpt=truncate(minimal_text, 140),
    )


def render_report(
    session_name: str,
    events_path: Path,
    minimal_path: Path,
    model_name: str,
    threshold: float,
    rows: List[EvalRow],
    missing_steps: List[int],
) -> str:
    total_steps = len(rows) + len(missing_steps)
    tool_rows = [r for r in rows if r.expected_type == "tool"]
    non_tool_rows = [r for r in rows if r.expected_type != "tool"]

    def rate(count: int, total: int) -> float:
        return count / total if total else 0.0

    type_matches = [r for r in rows if r.type_match]
    exact_matches = [r for r in rows if r.match_level == "exact"]
    tool_name_matches = [r for r in tool_rows if r.tool_match]
    tool_arg_matches = [
        r for r in tool_rows if r.arg_match is True
    ]
    tool_exact_matches = [
        r for r in tool_rows if r.match_level == "exact"
    ]
    non_tool_exact_matches = [
        r for r in non_tool_rows if r.match_level == "exact"
    ]

    lines = [
        "# Replay Evaluation Report (DSPy)",
        "",
        f"Session: {session_name}",
        f"Events: {events_path}",
        f"Minimal packs: {minimal_path}",
        f"Model: {model_name}",
        f"Args match threshold: {threshold:.2f}",
        "",
        "## Summary",
        "",
        f"- steps_total: {total_steps}",
        f"- steps_scored: {len(rows)}",
        f"- missing_minimal: {len(missing_steps)}",
        f"- tool_steps: {len(tool_rows)}",
        f"- non_tool_steps: {len(non_tool_rows)}",
        f"- type_match_rate: {rate(len(type_matches), len(rows)):.2f}",
        f"- exact_match_rate: {rate(len(exact_matches), len(rows)):.2f}",
        f"- tool_name_match_rate: {rate(len(tool_name_matches), len(tool_rows)):.2f}",
        f"- tool_args_match_rate: {rate(len(tool_arg_matches), len(tool_rows)):.2f}",
        f"- tool_exact_rate: {rate(len(tool_exact_matches), len(tool_rows)):.2f}",
        f"- non_tool_exact_rate: {rate(len(non_tool_exact_matches), len(non_tool_rows)):.2f}",
        "",
    ]

    lines.extend(
        [
            "## By Step Type",
            "",
            "| type | steps | type_match | exact_match | tool_match | arg_match |",
            "| --- | --- | --- | --- | --- | --- |",
            f"| tool | {len(tool_rows)} | "
            f"{rate(len([r for r in tool_rows if r.type_match]), len(tool_rows)):.2f} | "
            f"{rate(len(tool_exact_matches), len(tool_rows)):.2f} | "
            f"{rate(len(tool_name_matches), len(tool_rows)):.2f} | "
            f"{rate(len(tool_arg_matches), len(tool_rows)):.2f} |",
            f"| non_tool | {len(non_tool_rows)} | "
            f"{rate(len([r for r in non_tool_rows if r.type_match]), len(non_tool_rows)):.2f} | "
            f"{rate(len(non_tool_exact_matches), len(non_tool_rows)):.2f} | "
            f"{'-' if not non_tool_rows else 'n/a'} | "
            f"{'-' if not non_tool_rows else 'n/a'} |",
            "",
        ]
    )

    if missing_steps:
        missing_list = ", ".join(f"{s:02d}" for s in missing_steps)
        lines.extend(
            [
                "## Missing Minimal Packs",
                "",
                f"- steps: {missing_list}",
                "",
            ]
        )

    mismatches = [r for r in rows if r.match_level != "exact"]
    lines.extend(
        [
            "## Mismatches",
            "",
            "| step | expected | predicted | match | minimal_excerpt |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    if mismatches:
        for row in mismatches:
            expected_text = (
                f"tool:{row.expected_tool} {row.expected_arg}"
                if row.expected_type == "tool"
                else f"non_tool:{row.expected_subtype}"
            )
            predicted_text = (
                f"tool:{row.predicted_tool} {row.predicted_arg}"
                if row.predicted_type == "tool"
                else f"non_tool:{row.predicted_subtype}"
            )
            lines.append(
                f"| {row.step_id:02d} | {truncate(expected_text, 60)} | "
                f"{truncate(predicted_text, 60)} | {row.match_level} | "
                f"{truncate(row.minimal_excerpt, 60)} |"
            )
    else:
        lines.append("| - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Per-step Results",
            "",
            "| step | expected | predicted | match |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        expected_text = (
            f"tool:{row.expected_tool} {row.expected_arg}"
            if row.expected_type == "tool"
            else f"non_tool:{row.expected_subtype}"
        )
        predicted_text = (
            f"tool:{row.predicted_tool} {row.predicted_arg}"
            if row.predicted_type == "tool"
            else f"non_tool:{row.predicted_subtype}"
        )
        lines.append(
            f"| {row.step_id:02d} | {truncate(expected_text, 60)} | "
            f"{truncate(predicted_text, 60)} | {row.match_level} |"
        )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This is a proxy replay: it checks whether a model can predict the next action from the minimal pack.",
            "- Tool arg matching uses token overlap or URL/path equivalence; it is not semantic equivalence.",
            "- Non-tool steps are evaluated on subtype match only (reasoning/plan/ask_user/consolidate/note).",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay evaluation: predict actions from minimal context packs."
    )
    parser.add_argument("--input", "-i", required=True, help="Path to events.json")
    parser.add_argument(
        "--minimal",
        help="Path to minimal_context_packs.md (default: alongside events.json).",
    )
    parser.add_argument(
        "--output",
        help="Path to write replay evaluation markdown (default: alongside events.json).",
    )
    parser.add_argument(
        "--model",
        default="gemini-3-flash-preview",
        help="Model name to use with DSPy.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="Model temperature for replay prediction.",
    )
    parser.add_argument(
        "--arg-threshold",
        type=float,
        default=0.6,
        help="Token overlap threshold for argument matching.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input not found: {input_path}")

    minimal_path = (
        Path(args.minimal)
        if args.minimal
        else input_path.parent / "minimal_context_packs.md"
    )
    if not minimal_path.exists():
        raise SystemExit(f"Missing minimal packs: {minimal_path}")

    output_path = (
        Path(args.output)
        if args.output
        else input_path.parent / "replay_evaluation.md"
    )

    with open(input_path, "r", encoding="utf-8") as f:
        events = json.load(f)

    packs = dap.build_action_packs(events)
    expected = build_expected_actions(packs)
    minimal = parse_minimal_packs(minimal_path)

    DSPyGeminiConfig.configure(model_name=args.model, temperature=args.temperature)
    predictor = ReplayActionPredictor()

    rows: List[EvalRow] = []
    missing_steps: List[int] = []
    for exp in expected:
        minimal_pack = minimal.get(exp.step_id)
        if not minimal_pack:
            missing_steps.append(exp.step_id)
            continue

        try:
            prediction = predictor(
                minimal_context=minimal_pack.text,
                available_tools=", ".join(TOOL_NAMES),
            )
            predicted = PredictedAction(
                step_id=exp.step_id,
                action_type=str(prediction.action_type or ""),
                tool_name=str(prediction.tool_name or ""),
                tool_args_raw=str(prediction.tool_args or ""),
                non_tool_type=str(prediction.non_tool_type or ""),
            )
        except Exception as exc:
            predicted = PredictedAction(
                step_id=exp.step_id,
                action_type="",
                tool_name="",
                tool_args_raw="",
                non_tool_type="",
                error=str(exc),
            )

        rows.append(
            evaluate_step(
                exp,
                predicted,
                minimal_pack.text,
                threshold=args.arg_threshold,
            )
        )

    report = render_report(
        session_name=input_path.parent.name,
        events_path=input_path,
        minimal_path=minimal_path,
        model_name=args.model,
        threshold=args.arg_threshold,
        rows=rows,
        missing_steps=missing_steps,
    )

    output_path.write_text(report, encoding="utf-8")
    print(f"Wrote replay evaluation to {output_path}")


if __name__ == "__main__":
    main()
