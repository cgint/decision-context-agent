from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PiRpcRunResult:
    ok: bool
    error: str | None
    agent_end_event: dict[str, Any] | None
    exit_code: int | None


def _write_jsonl_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run_pi_rpc_prompt(
    *,
    message: str,
    cwd: Path,
    transcript_path: Path,
    pi_executable: str = "pi",
    pi_args: list[str] | None = None,
    rpc_args: list[str] | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: int = 1800,
) -> PiRpcRunResult:
    """
    Run `pi` in RPC mode, send a single prompt, and stop when `agent_end` is observed.

    Writes `transcript_path` as JSONL:
      {"at": "...", "stream": "stdout|stderr|stdin", "data": <json or string>}
    """
    pi_args = list(pi_args or [])
    resolved_env = os.environ.copy()
    if env:
        resolved_env.update(env)

    base_rpc_args = ["--mode", "rpc", "--no-session"] if rpc_args is None else list(rpc_args)
    cmd = [pi_executable, *base_rpc_args, *pi_args]
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=resolved_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stderr_lines: list[str] = []
    stderr_done = threading.Event()

    def stderr_reader() -> None:
        try:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_lines.append(line)
                _write_jsonl_line(transcript_path, {"at": _utc_now_iso(), "stream": "stderr", "data": line.rstrip("\n")})
        finally:
            stderr_done.set()

    threading.Thread(target=stderr_reader, daemon=True).start()

    started = time.time()
    agent_end: dict[str, Any] | None = None

    try:
        assert proc.stdin is not None
        assert proc.stdout is not None

        req = {"id": "req-1", "type": "prompt", "message": message}
        proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        _write_jsonl_line(transcript_path, {"at": _utc_now_iso(), "stream": "stdin", "data": req})

        while True:
            if time.time() - started > timeout_seconds:
                return PiRpcRunResult(ok=False, error="timeout waiting for agent_end", agent_end_event=agent_end, exit_code=None)

            line = proc.stdout.readline()
            if not line:
                break

            raw = line.rstrip("\n")
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                _write_jsonl_line(transcript_path, {"at": _utc_now_iso(), "stream": "stdout", "data": raw})
                continue

            _write_jsonl_line(transcript_path, {"at": _utc_now_iso(), "stream": "stdout", "data": event})

            if isinstance(event, dict) and event.get("type") == "agent_end":
                agent_end = event
                break

        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

        stderr_done.wait(timeout=2)

        exit_code = proc.returncode
        return PiRpcRunResult(ok=agent_end is not None, error=None if agent_end else "no agent_end event", agent_end_event=agent_end, exit_code=exit_code)
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass
        try:
            if proc.stderr:
                proc.stderr.close()
        except Exception:
            pass
