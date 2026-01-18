#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    extension_path = (repo_root / "integrations" / "pi_mono" / "extensions" / "manager_bridge" / "index.ts").resolve()

    proc = subprocess.Popen(
        ["pi", "--mode", "rpc", "--no-session", "-e", str(extension_path)],
        cwd=str(repo_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None

        cmd = {"id": "smoke-1", "type": "get_state"}
        proc.stdin.write(json.dumps(cmd) + "\n")
        proc.stdin.flush()

        while True:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "response" and msg.get("id") == "smoke-1":
                if not msg.get("success"):
                    print("get_state failed:", msg)
                    return 2
                print("ok")
                return 0
        print("no response received")
        return 3
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())

