#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from acp_opencode_backend import OpencodeACPBackend  # noqa: E402


def read_assignment_text(path: str) -> str:
    if path == "-":
        return Path("/dev/stdin").read_text(encoding="utf-8")
    return Path(path).read_text(encoding="utf-8")


async def _run(args: argparse.Namespace) -> int:
    workspace_dir = Path(args.workspace_dir).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else None
    assignment_text = read_assignment_text(args.assignment_file).strip()

    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        run_dir = out_dir / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        run_dir = None

    backend = OpencodeACPBackend(
        workspace_dir=workspace_dir,
        opencode_bin=args.opencode_bin,
        log_level=args.log_level,
        prompt_timeout_s=args.timeout_s,
    )
    result = await backend.run_assignment(assignment_text=assignment_text, assignment_id=args.assignment_id)

    if run_dir:
        OpencodeACPBackend.persist_result(run_dir, result)

    print(result.agent_message)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Manager→Actor assignment via OpenCode ACP.")
    parser.add_argument(
        "--assignment-file",
        default="-",
        help="Path to a text file containing the Assignment message (use '-' for stdin).",
    )
    parser.add_argument("--assignment-id", default="", help="Optional ASSIGNMENT_ID override.")
    parser.add_argument("--workspace-dir", default=".", help="Workspace directory to pass to OpenCode.")
    parser.add_argument("--out-dir", default="", help="Directory to write transcript/artifacts (a timestamped subdir is created).")
    parser.add_argument("--opencode-bin", default="opencode", help="Path to the opencode executable.")
    parser.add_argument("--log-level", default="INFO", help="OpenCode log level (DEBUG/INFO/WARN/ERROR).")
    parser.add_argument("--timeout-s", type=float, default=600.0, help="Prompt timeout in seconds.")
    args = parser.parse_args()
    if not args.assignment_id:
        args.assignment_id = None
    if not args.out_dir:
        args.out_dir = None
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
