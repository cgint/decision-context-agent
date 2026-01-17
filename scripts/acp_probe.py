#!/usr/bin/env python3
from __future__ import annotations

# ruff: noqa: E402

import argparse
import asyncio
import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_ASSIGNMENT = """ASSIGNMENT_ID: ACP-PROBE-001
TASK: Confirm ACP connectivity by reading the first 5 lines of README.md.
SUCCESS_CRITERIA: Return the file path and the first 5 lines with line numbers.
OVERALL_GOAL: ACP connectivity probe.
CONSTRAINTS: Use a read-only command.
DEGREES_OF_FREEDOM: Action-specified
DELIVERABLES: RESULT/EVIDENCE/STATE_DELTA with a tool-backed citation.
OUTPUT_FORMAT: Respond ONLY with these headers (exact):
RESULT: <success|partial|fail>
EVIDENCE: <file path with line numbers or command output>
STATE_DELTA: <one bullet>
"""


def _resolve_repo_import(module_name: str):
    sys.path.insert(0, str(REPO_ROOT))
    return importlib.import_module(module_name)


async def _run(args: argparse.Namespace) -> int:
    acp_module = _resolve_repo_import("acp_opencode_backend")
    protocol_module = _resolve_repo_import("manager_actor_protocol")

    workspace_dir = Path(args.workspace_dir).resolve()
    backend = acp_module.OpencodeACPBackend(
        workspace_dir=workspace_dir,
        opencode_bin=args.opencode_bin,
        log_level=args.log_level,
        prompt_timeout_s=args.timeout_s,
    )
    assignment_text = (args.assignment_text or DEFAULT_ASSIGNMENT).strip()
    result = await backend.run_assignment(
        assignment_text=assignment_text,
        assignment_id=args.assignment_id,
        progress_dir=Path(args.out_dir).resolve() if args.out_dir else None,
    )
    print(result.agent_message)

    parsed = protocol_module.parse_result(
        result.agent_message, fallback_assignment_id=args.assignment_id
    )
    if parsed.errors:
        print("\nACP_PROBE_PARSE_ERRORS:")
        for err in parsed.errors:
            print(f"- {err}")
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ACP connectivity probe using OpenCode."
    )
    parser.add_argument(
        "--workspace-dir", default=".", help="Workspace directory for the agent."
    )
    parser.add_argument(
        "--assignment-id", default="ACP-PROBE-001", help="Assignment ID to use."
    )
    parser.add_argument(
        "--assignment-text", default="", help="Optional assignment text override."
    )
    parser.add_argument(
        "--opencode-bin", default="opencode", help="Path to opencode executable."
    )
    parser.add_argument("--log-level", default="INFO", help="OpenCode log level.")
    parser.add_argument(
        "--timeout-s", type=float, default=120.0, help="Prompt timeout in seconds."
    )
    parser.add_argument(
        "--out-dir", default="", help="Optional directory for progress artifacts."
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
