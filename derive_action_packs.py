#!/usr/bin/env python3
"""
Derive raw action-pack candidates from events.json.

This is a minimal, schema-light extractor:
- Each tool call becomes one action pack
- Context is taken from the latest user message and the AI preface text
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class ActionPack:
    step_id: int
    event_index: int
    step_type: str
    subtype: str
    tool_index: Optional[int]
    tool_name: Optional[str]
    status: str
    trigger_user: str
    context_preface: str
    content: str
    args_block: str
    result_block: str
    approx_tokens: int


def tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_./:-]*", text.lower())


def extract_preface(message: str) -> str:
    for marker in ("THOUGHTS:", "TOOL CALL:"):
        if marker in message:
            return message.split(marker, 1)[0].strip()
    return message.strip()


def extract_thoughts(message: str) -> str:
    if "THOUGHTS:" not in message:
        return ""
    return extract_block(message, "THOUGHTS:", "TOOL CALL:")


def extract_block(text: str, start_label: str, end_label: Optional[str]) -> str:
    start_idx = text.find(start_label)
    if start_idx == -1:
        return ""
    start_idx += len(start_label)
    end_idx = text.find(end_label, start_idx) if end_label else -1
    if end_idx == -1:
        end_idx = len(text)
    return text[start_idx:end_idx].strip()


def parse_tool_calls(message: str) -> List[dict]:
    if "TOOL CALL:" not in message:
        return []
    parts = message.split("TOOL CALL:")
    tool_sections = parts[1:]
    tool_calls = []
    for section in tool_sections:
        section = section.strip()
        if not section:
            continue
        lines = section.splitlines()
        tool_name = lines[0].strip() if lines else "unknown_tool"
        status_match = re.search(r"Status:\s*([a-zA-Z_]+)", section)
        status = status_match.group(1) if status_match else "unknown"
        args_block = extract_block(section, "Args:", "Result:")
        result_block = extract_block(section, "Result:", None)
        tool_calls.append(
            {
                "tool_name": tool_name,
                "status": status,
                "args_block": args_block,
                "result_block": result_block,
            }
        )
    return tool_calls


def truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + " ..."


def classify_non_tool(message: str) -> str:
    lowered = message.lower()
    if "?" in message or re.search(
        r"\b(should i|do you want|would you like|can i|please confirm|let me know)\b",
        lowered,
    ):
        return "ask_user"
    if re.search(
        r"\b(summary|findings|conclusion|i have completed|i have finished|i have updated|done)\b",
        lowered,
    ):
        return "consolidate"
    if re.search(
        r"\b(next steps|plan|i will|i am going to|i am ready to|ready to|i can)\b",
        lowered,
    ):
        return "plan"
    return "note"


def estimate_tokens(text: str) -> int:
    return len(tokenize(text))


def compute_signal(pack: ActionPack) -> str:
    if pack.step_type == "tool":
        if pack.status == "success":
            return "success"
        if pack.status == "cancelled":
            return "cancelled"
        return "unknown"
    if pack.subtype == "ask_user":
        return "awaiting_user"
    if pack.subtype == "plan":
        return "proposal"
    if pack.subtype == "consolidate":
        return "checkpoint"
    if pack.subtype == "reasoning":
        return "analysis"
    return "info"


def build_action_packs(events: List[dict]) -> List[ActionPack]:
    packs: List[ActionPack] = []
    last_user_message = ""
    step_id = 1
    for idx, event in enumerate(events):
        author = event.get("author", "")
        message = event.get("message", "")
        if author == "user":
            last_user_message = message.strip()
            continue

        if author != "ai":
            continue

        tool_calls = parse_tool_calls(message)
        if tool_calls:
            preface = extract_preface(message)
            thoughts = extract_thoughts(message)

            if thoughts:
                approx_tokens = estimate_tokens(" ".join([last_user_message, thoughts]))
                packs.append(
                    ActionPack(
                        step_id=step_id,
                        event_index=idx,
                        step_type="non_tool",
                        subtype="reasoning",
                        tool_index=None,
                        tool_name=None,
                        status="n/a",
                        trigger_user=last_user_message,
                        context_preface="",
                        content=thoughts,
                        args_block="",
                        result_block="",
                        approx_tokens=approx_tokens,
                    )
                )
                step_id += 1

            if preface:
                subtype = classify_non_tool(preface)
                approx_tokens = estimate_tokens(" ".join([last_user_message, preface]))
                packs.append(
                    ActionPack(
                        step_id=step_id,
                        event_index=idx,
                        step_type="non_tool",
                        subtype=subtype,
                        tool_index=None,
                        tool_name=None,
                        status="n/a",
                        trigger_user=last_user_message,
                        context_preface="",
                        content=preface,
                        args_block="",
                        result_block="",
                        approx_tokens=approx_tokens,
                    )
                )
                step_id += 1

            for tool_index, tool_call in enumerate(tool_calls, start=1):
                approx_tokens = estimate_tokens(
                    " ".join(
                        [
                            last_user_message,
                            preface,
                            tool_call["tool_name"],
                            tool_call["args_block"],
                        ]
                    )
                )
                packs.append(
                    ActionPack(
                        step_id=step_id,
                        event_index=idx,
                        step_type="tool",
                        subtype="tool_call",
                        tool_index=tool_index,
                        tool_name=tool_call["tool_name"],
                        status=tool_call["status"],
                        trigger_user=last_user_message,
                        context_preface=preface,
                        content="",
                        args_block=tool_call["args_block"],
                        result_block=tool_call["result_block"],
                        approx_tokens=approx_tokens,
                    )
                )
                step_id += 1
            continue

        message_clean = message.strip()
        if not message_clean:
            continue
        subtype = classify_non_tool(message_clean)
        approx_tokens = estimate_tokens(" ".join([last_user_message, message_clean]))
        packs.append(
            ActionPack(
                step_id=step_id,
                event_index=idx,
                step_type="non_tool",
                subtype=subtype,
                tool_index=None,
                tool_name=None,
                status="n/a",
                trigger_user=last_user_message,
                context_preface="",
                content=message_clean,
                args_block="",
                result_block="",
                approx_tokens=approx_tokens,
            )
        )
        step_id += 1
    return packs


def render_packs(
    session_name: str, source_path: Path, packs: List[ActionPack], result_chars: int
) -> str:
    lines = [
        "# Action Packs (raw draft)",
        "",
        f"Session: {session_name}",
        f"Source: {source_path}",
        "",
        "Notes:",
        "- Each tool call is mapped to one action pack.",
        "- Non-tool steps are included from AI messages and THOUGHTS blocks.",
        "- Context is raw: last user message + AI preface text or message content.",
        "- Results are truncated for readability.",
        "",
    ]
    for pack in packs:
        if pack.step_type == "tool":
            lines.extend(
                [
                    f"## Step {pack.step_id:02d} (event {pack.event_index}, tool {pack.tool_index})",
                    "",
                    "Trigger (user):",
                    pack.trigger_user or "(none)",
                    "",
                    "Context (AI preface):",
                    pack.context_preface or "(none)",
                    "",
                    "Action:",
                    f"- tool: {pack.tool_name}",
                    f"- status: {pack.status}",
                    f"- approx_tokens: {pack.approx_tokens}",
                    "",
                    "Args (raw):",
                    "```",
                    pack.args_block or "(none)",
                    "```",
                    "",
                    "Result (truncated):",
                    "```",
                    truncate(pack.result_block, result_chars) or "(none)",
                    "```",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    f"## Step {pack.step_id:02d} (event {pack.event_index}, non-tool)",
                    "",
                    "Trigger (user):",
                    pack.trigger_user or "(none)",
                    "",
                    "Action:",
                    f"- type: {pack.subtype}",
                    f"- approx_tokens: {pack.approx_tokens}",
                    "",
                    "Content (raw):",
                    "```",
                    truncate(pack.content, result_chars) or "(none)",
                    "```",
                    "",
                ]
            )

    lines.extend(render_scorecard(packs))
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Derive raw action-pack candidates from events.json."
    )
    parser.add_argument("--input", "-i", required=True, help="Path to events.json")
    parser.add_argument(
        "--output",
        "-o",
        help="Path to write action packs markdown (default: alongside events.json).",
    )
    parser.add_argument(
        "--result-chars",
        type=int,
        default=500,
        help="Max characters to keep from tool results.",
    )
    return parser.parse_args()


def render_scorecard(packs: List[ActionPack]) -> List[str]:
    tool_steps = [p for p in packs if p.step_type == "tool"]
    non_tool_steps = [p for p in packs if p.step_type != "tool"]
    success_steps = [p for p in tool_steps if p.status == "success"]
    cancelled_steps = [p for p in tool_steps if p.status == "cancelled"]
    success_rate = (
        len(success_steps) / len(tool_steps) if tool_steps else 0.0
    )
    token_total = sum(p.approx_tokens for p in packs)
    token_avg = token_total / len(packs) if packs else 0.0

    lines = [
        "## Replay scorecard (draft)",
        "",
        f"- total_steps: {len(packs)}",
        f"- tool_steps: {len(tool_steps)}",
        f"- non_tool_steps: {len(non_tool_steps)}",
        f"- tool_success: {len(success_steps)}",
        f"- tool_cancelled: {len(cancelled_steps)}",
        f"- tool_success_rate: {success_rate:.2f}",
        f"- approx_tokens_total: {token_total}",
        f"- approx_tokens_avg: {token_avg:.1f}",
        "",
        "Per-step signals:",
        "",
        "| step | type | subtype | status | signal | approx_tokens |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    for pack in packs:
        signal = compute_signal(pack)
        lines.append(
            f"| {pack.step_id:02d} | {pack.step_type} | {pack.subtype} | "
            f"{pack.status} | {signal} | {pack.approx_tokens} |"
        )

    lines.append("")
    return lines


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input not found: {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        events = json.load(f)

    session_name = input_path.parent.name
    output_path = (
        Path(args.output)
        if args.output
        else input_path.parent / "action_packs_raw.md"
    )

    packs = build_action_packs(events)
    content = render_packs(session_name, input_path, packs, args.result_chars)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Wrote {len(packs)} action packs to {output_path}")


if __name__ == "__main__":
    main()
