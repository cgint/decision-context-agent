#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "dspy",
#     "google-cloud-aiplatform",
# ]
# ///

"""
Derive minimal context packs per step using DSPy.

Input: events.json
Output: minimal_context_packs.md (raw text packs + comparison stats)
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import List

import dspy

from config import DSPyGeminiConfig
import derive_action_packs as dap
from replay_state_timeline import State, apply_pack


PACK_CONTEXT_RULES_COMPRESSED = """Return only CONTEXT bullet lines.
Rules:
- Use "- " to start each bullet line.
- Focus on the current step only; do not describe next or previous steps.
- Do not restate the action/tool/args; those are provided separately in ACTION_TYPE/TOOL_NAME/TOOL_ARGS.
- Do not include tool results; only include what is needed to execute.
- Keep context minimal; omit the goal unless it is required to execute the step.
"""

PACK_CONTEXT_RULES_SUFFICIENT = """Return only CONTEXT bullet lines.
Rules:
- Use "- " to start each bullet line.
- Focus on the current step only; do not describe next or previous steps.
- Do not mention tool names (e.g. "read_file", "replace", "google_web_search").
- Include the minimum concrete anchors required to execute WITHOUT TOOL_ARGS (e.g. file paths, URLs, search patterns, shell commands).
- Do not include tool results; only include what is needed to execute.
- Keep context minimal; omit the goal unless it is required to execute the step.
"""


class MinimalContextSignature(dspy.Signature):
    """Derive minimal context needed to execute the current step."""

    goal: str = dspy.InputField(desc="Overall goal for the session.")
    step: str = dspy.InputField(desc="Current step details (tool or non-tool).")
    state: str = dspy.InputField(
        desc="Current state snapshot (facts, evidence, artifacts, actions, questions, notes)."
    )
    format_rules: str = dspy.InputField(
        desc="Formatting and behavior rules for the context bullets."
    )
    context_lines: str = dspy.OutputField(
        desc="Minimal context lines as bullets only (no headers)."
    )


class MinimalContextDeriver(dspy.Module):
    def __init__(self, *, format_rules: str):
        super().__init__()
        self.format_rules = format_rules
        self.predictor = dspy.Predict(MinimalContextSignature)

    def forward(self, goal: str, step: str, state: str) -> str:
        prediction = self.predictor(
            goal=goal,
            step=step,
            state=state,
            format_rules=self.format_rules,
        )
        return prediction.context_lines


@dataclass
class MinimalPack:
    step_id: int
    minimal_text: str
    minimal_tokens: int
    full_tokens: int


def format_state(state: State, max_items: int) -> str:
    def take(items: List[str]) -> List[str]:
        return items[-max_items:] if max_items > 0 else items

    lines = [
        "FACTS:",
        *[f"- {x}" for x in take(state.facts)],
        "",
        "EVIDENCE:",
        *[f"- {x}" for x in take(state.evidence)],
        "",
        "ARTIFACTS:",
        *[f"- {x}" for x in take(state.artifacts)],
        "",
        "ACTIONS:",
        *[f"- {x}" for x in take(state.actions)],
        "",
        "QUESTIONS:",
        *[f"- {x}" for x in take(state.questions)],
        "",
        "NOTES:",
        *[f"- {x}" for x in take(state.notes)],
    ]
    return "\n".join(lines).strip()


def format_step(pack: dap.ActionPack) -> str:
    if pack.step_type == "tool":
        return "\n".join(
            [
                "type: tool",
                f"tool: {pack.tool_name}",
                f"status: {pack.status}",
                f"trigger_user: {pack.trigger_user}",
                "args:",
                pack.args_block or "(none)",
            ]
        )
    return "\n".join(
        [
            f"type: {pack.subtype}",
            f"trigger_user: {pack.trigger_user}",
            "content:",
            pack.content or "(none)",
        ]
    )


def estimate_tokens(text: str) -> int:
    return len(dap.tokenize(text))


def parse_args_block(args_block: str) -> dict:
    args_block = args_block.strip()
    if not args_block:
        return {}
    try:
        return json.loads(args_block)
    except Exception:
        return {}


def truncate_chars(text: str, limit: int) -> str:
    text = text.strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + " ..."


def minimal_tool_args(pack: dap.ActionPack) -> dict:
    args = parse_args_block(pack.args_block)
    tool = pack.tool_name or ""
    if tool == "google_web_search":
        return {"query": args.get("query", "")}
    if tool == "web_fetch":
        if "prompt" in args:
            return {"prompt": args.get("prompt", "")}
        if "url" in args:
            return {"url": args.get("url", "")}
        return {}
    if tool == "read_file":
        if "file_path" in args:
            return {"file_path": args.get("file_path", "")}
        if "absolute_path" in args:
            return {"absolute_path": args.get("absolute_path", "")}
        return {}
    if tool == "write_file":
        return {"file_path": args.get("file_path", "")}
    if tool == "search_file_content":
        return {"pattern": args.get("pattern", "")}
    if tool == "run_shell_command":
        return {"command": args.get("command", "")}
    if tool == "replace":
        file_path = args.get("file_path", "")
        instruction = args.get("instruction", "")
        old_string = args.get("old_string", "")
        new_string = args.get("new_string", "")

        out: dict = {}
        if file_path:
            out["file_path"] = file_path
        if instruction:
            out["instruction"] = truncate_chars(str(instruction), 240)
            return out
        if old_string:
            out["old_string"] = truncate_chars(str(old_string), 200)
        if new_string:
            out["new_string"] = truncate_chars(str(new_string), 200)
        return out
    return args if isinstance(args, dict) else {}


def normalize_context_lines(context_text: str) -> List[str]:
    lines = [line.strip() for line in context_text.splitlines() if line.strip()]
    normalized = []
    for line in lines:
        if line.startswith("- "):
            normalized.append(line)
        else:
            normalized.append(f"- {line}")
    return normalized


def _string_values(obj) -> List[str]:
    values: List[str] = []
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        for v in obj.values():
            values.extend(_string_values(v))
    if isinstance(obj, list):
        for v in obj:
            values.extend(_string_values(v))
    return values


def _looks_like_path_or_url(value: str) -> bool:
    if not value:
        return False
    if "://" in value:
        return True
    return "/" in value


def _contains_url(text: str) -> bool:
    lowered = text.lower()
    return "http://" in lowered or "https://" in lowered


def _extract_urls(text: str) -> List[str]:
    return re.findall(r"https?://[^\s)>,]+", text)


def prune_context_lines(
    pack: dap.ActionPack,
    context_lines: List[str],
    *,
    prune_arg_values: bool,
    prune_urls: bool,
) -> List[str]:
    pruned: List[str] = []
    tool_name = (pack.tool_name or "").strip()
    tool_args = minimal_tool_args(pack) if pack.step_type == "tool" else {}
    arg_strings = [
        s for s in _string_values(tool_args) if isinstance(s, str) and _looks_like_path_or_url(s)
    ]

    narrative_markers = (
        "previous step",
        "last step",
        "earlier",
        "already",
        "has been performed",
    )

    for line in context_lines:
        raw = line.strip()
        lowered = raw.lower()
        if any(m in lowered for m in narrative_markers):
            continue
        if tool_name and tool_name.lower() in lowered:
            continue
        if prune_arg_values and arg_strings and any(s in raw for s in arg_strings):
            continue
        pruned.append(raw)

    # De-dup while preserving order.
    seen = set()
    deduped: List[str] = []
    for line in pruned:
        if line in seen:
            continue
        seen.add(line)
        deduped.append(line)

    # Avoid misleading anchors: keep only the anchors needed for the current tool.
    if prune_urls and pack.step_type == "tool":
        tool = (pack.tool_name or "").strip()
        if tool == "web_fetch":
            expected_urls: List[str] = []
            args = minimal_tool_args(pack)
            if isinstance(args, dict):
                expected_urls.extend(_extract_urls(str(args.get("url", "") or "")))
                expected_urls.extend(_extract_urls(str(args.get("prompt", "") or "")))
            if expected_urls:
                deduped = [
                    line
                    for line in deduped
                    if (not _contains_url(line)) or any(u in line for u in expected_urls)
                ]
        elif tool in (
            "google_web_search",
            "read_file",
            "write_file",
            "replace",
            "search_file_content",
            "run_shell_command",
        ):
            deduped = [line for line in deduped if not _contains_url(line)]
    return deduped


def render_minimal_pack(
    pack: dap.ActionPack,
    context_text: str,
    *,
    prune_arg_values: bool,
    prune_urls: bool,
) -> str:
    if pack.step_type == "tool":
        action_type = "tool"
        tool_name = pack.tool_name or ""
        tool_args = json.dumps(minimal_tool_args(pack), ensure_ascii=True)
        non_tool_type = ""
    else:
        action_type = "non_tool"
        tool_name = ""
        tool_args = "{}"
        non_tool_type = pack.subtype or ""

    context_lines = normalize_context_lines(context_text)
    context_lines = prune_context_lines(
        pack,
        context_lines,
        prune_arg_values=prune_arg_values,
        prune_urls=prune_urls,
    )
    if pack.step_type == "tool" and (pack.tool_name or "") == "google_web_search" and not prune_arg_values:
        args = minimal_tool_args(pack)
        query = args.get("query", "") if isinstance(args, dict) else ""
        if query:
            lowered_query = query.lower()
            context_lines = [line for line in context_lines if lowered_query not in line.lower()]
            context_lines.insert(0, f"- Search query: {query}")
    if pack.step_type == "tool" and (pack.tool_name or "") == "write_file" and not prune_arg_values:
        args = minimal_tool_args(pack)
        file_path = args.get("file_path", "") if isinstance(args, dict) else ""
        if file_path:
            content_lines = [line for line in context_lines if file_path not in line]
            rewritten: List[str] = [f"- Write to: {file_path}"]
            for line in content_lines:
                body = line[2:] if line.startswith("- ") else line
                lowered = body.lower()
                if lowered.startswith("write content:") or lowered.startswith("content:"):
                    rewritten.append(f"- {body}")
                else:
                    rewritten.append(f"- Write content: {body}")
            context_lines = rewritten
    if pack.step_type == "tool" and (pack.tool_name or "") == "write_todos" and not prune_arg_values:
        args = minimal_tool_args(pack)
        todos = args.get("todos") if isinstance(args, dict) else None
        if isinstance(todos, list) and todos:
            rewritten: List[str] = []
            for item in todos:
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status", "") or "").strip()
                description = str(item.get("description", "") or "").strip()
                if not description:
                    continue
                if status:
                    rewritten.append(f"- TODO ({status}): {description}")
                else:
                    rewritten.append(f"- TODO: {description}")
            if rewritten:
                context_lines = rewritten
    if pack.step_type == "tool" and (pack.tool_name or "") == "run_shell_command" and not prune_arg_values:
        args = minimal_tool_args(pack)
        command = args.get("command", "") if isinstance(args, dict) else ""
        if command:
            context_lines = [f"- Command: {command}"]
    if pack.step_type == "tool" and (pack.tool_name or "") == "web_fetch" and not prune_arg_values:
        args = minimal_tool_args(pack)
        url = ""
        if isinstance(args, dict):
            if isinstance(args.get("url"), str):
                url = args.get("url", "") or ""
            elif isinstance(args.get("prompt"), str):
                prompt = args.get("prompt", "") or ""
                match = re.search(r"https?://[^\s)>,]+", prompt)
                if match:
                    url = match.group(0)
        url = url.strip()
        if url:
            context_lines = [f"- URL: {url}"]
    if pack.step_type == "tool" and (pack.tool_name or "") == "replace" and not prune_arg_values:
        args = minimal_tool_args(pack)
        file_path = args.get("file_path", "") if isinstance(args, dict) else ""
        instruction = args.get("instruction", "") if isinstance(args, dict) else ""
        if file_path:
            rewritten = [f"- Replace in: {file_path}"]
            if instruction:
                flat = " ".join(str(instruction).split())
                if len(flat) > 240:
                    flat = f"{flat[:240].rstrip()}..."
                rewritten.append(f"- Replace instruction: {flat}")
            context_lines = rewritten
    if not context_lines:
        context_lines = ["- (none)"]

    lines = [
        f"ACTION_TYPE: {action_type}",
        f"TOOL_NAME: {tool_name}",
        f"TOOL_ARGS: {tool_args}",
        f"NON_TOOL_TYPE: {non_tool_type}",
        "CONTEXT:",
        *context_lines,
    ]
    return "\n".join(lines).strip()


def full_context_text(goal: str, pack: dap.ActionPack) -> str:
    return "\n".join(
        [
            goal,
            pack.trigger_user,
            pack.context_preface,
            pack.content,
            pack.args_block,
        ]
    ).strip()


def render_output(
    session_name: str,
    source_path: Path,
    packs: List[MinimalPack],
    *,
    model_name: str,
    max_items: int,
    max_steps: int,
    context_mode: str,
    prune_urls: bool,
) -> str:
    ratios = [
        (p.minimal_tokens / p.full_tokens)
        for p in packs
        if p.full_tokens > 0
    ]
    lines = [
        "# Minimal Context Packs (DSPy)",
        "",
        f"Session: {session_name}",
        f"Source: {source_path}",
        f"Model: {model_name}",
        f"Max items per state bucket: {max_items}",
        f"Max steps: {max_steps if max_steps > 0 else 'all'}",
        f"Context mode: {context_mode}",
        f"Prune URLs: {prune_urls}",
        "",
        "Notes:",
        "- Minimal packs are raw text and may include optional metadata.",
        "- Token counts are approximate (word-based).",
        "- Context bullets are generated using PACK_CONTEXT_RULES_* selected by --context-mode.",
        "",
    ]

    for pack in packs:
        ratio = (
            pack.minimal_tokens / pack.full_tokens
            if pack.full_tokens > 0
            else 0.0
        )
        lines.extend(
            [
                f"## Step {pack.step_id:02d}",
                "",
                f"- full_tokens: {pack.full_tokens}",
                f"- minimal_tokens: {pack.minimal_tokens}",
                f"- ratio: {ratio:.2f}",
                "",
                "Minimal context pack:",
                "```",
                pack.minimal_text.strip() or "(none)",
                "```",
                "",
            ]
        )

    lines.extend(
        [
            "## Summary",
            "",
            f"- steps: {len(packs)}",
            f"- avg_ratio: {mean(ratios):.2f}" if ratios else "- avg_ratio: 0.00",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Derive minimal context packs per step using DSPy."
    )
    parser.add_argument("--input", "-i", required=True, help="Path to events.json")
    parser.add_argument(
        "--output",
        "-o",
        help="Path to write minimal context markdown (default: alongside events.json).",
    )
    parser.add_argument(
        "--model",
        default="gemini-3-flash-preview",
        help="Model name to use with DSPy.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=12,
        help="Max items per state bucket to include in context.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=0,
        help="If > 0, only derive the first N steps (useful for iteration).",
    )
    parser.add_argument(
        "--context-mode",
        choices=["compressed", "sufficient"],
        default="compressed",
        help="How to generate CONTEXT lines (compressed forbids arg anchors; sufficient allows minimal anchors like paths/URLs).",
    )
    parser.add_argument(
        "--prune-urls",
        action="store_true",
        help="If set, drop likely-misleading URL bullets for non-fetch tools (and keep only the expected URL for web_fetch).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input not found: {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        events = json.load(f)

    DSPyGeminiConfig.configure(model_name=args.model)
    format_rules = (
        PACK_CONTEXT_RULES_SUFFICIENT
        if args.context_mode == "sufficient"
        else PACK_CONTEXT_RULES_COMPRESSED
    )
    deriver = MinimalContextDeriver(format_rules=format_rules)

    packs = dap.build_action_packs(events)
    if args.max_steps and args.max_steps > 0:
        packs = packs[: args.max_steps]
    state = State()
    goal = next(
        (e.get("message", "").strip() for e in events if e.get("author") == "user"),
        "",
    )

    minimal_packs: List[MinimalPack] = []
    for pack in packs:
        state_snapshot = format_state(state, args.max_items)
        step_text = format_step(pack)
        context_text = deriver(goal=goal, step=step_text, state=state_snapshot)
        minimal_text = render_minimal_pack(
            pack,
            context_text,
            prune_arg_values=(args.context_mode == "compressed"),
            prune_urls=args.prune_urls,
        )
        full_tokens = estimate_tokens(full_context_text(goal, pack))
        minimal_tokens = estimate_tokens(minimal_text)
        minimal_packs.append(
            MinimalPack(
                step_id=pack.step_id,
                minimal_text=minimal_text,
                minimal_tokens=minimal_tokens,
                full_tokens=full_tokens,
            )
        )
        apply_pack(state, pack)

    output_path = (
        Path(args.output)
        if args.output
        else input_path.parent / "minimal_context_packs.md"
    )
    content = render_output(
        input_path.parent.name,
        input_path,
        minimal_packs,
        model_name=args.model,
        max_items=args.max_items,
        max_steps=args.max_steps,
        context_mode=args.context_mode,
        prune_urls=args.prune_urls,
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Wrote minimal context packs to {output_path}")


if __name__ == "__main__":
    main()
