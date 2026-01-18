"""
Targeted regression tests for Pi Mono manager-bridge (Variant B).

Run with:
  uv run python test_pi_mono_manager_bridge.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from pi_mono_manager_bridge import BridgeConfig, PiMonoManagerBridge


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_creates_session_and_logs_events():
    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp) / "bridge"
        bridge = PiMonoManagerBridge(
            BridgeConfig(log_dir=log_dir, enable_lm=False, steer_policy="off")
        )

        session_id = "my session/with weird chars"
        out1 = bridge.handle_event(
            session_id=session_id,
            event={"type": "before_agent_start", "prompt": "Fix bug X"},
        )
        out2 = bridge.handle_event(
            session_id=session_id,
            event={"type": "tool_result", "tool_name": "read", "is_error": False},
        )
        assert "timing" in out1 and isinstance(out1["timing"], dict)
        assert "duration_ms" in out1["timing"]
        assert "timing" in out2 and isinstance(out2["timing"], dict)
        assert "duration_ms" in out2["timing"]

        session_dirs = [p for p in log_dir.iterdir() if p.is_dir()]
        assert len(session_dirs) == 1

        session_dir = session_dirs[0]
        assert (session_dir / "session.json").exists()
        assert (session_dir / "events.jsonl").exists()

        session_json = _read_json(session_dir / "session.json")
        assert session_json["session_id"] == session_id
        assert session_json["goal"] == "Fix bug X"

        lines = (session_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) >= 2


def test_blocks_obviously_dangerous_bash():
    with tempfile.TemporaryDirectory() as tmp:
        bridge = PiMonoManagerBridge(
            BridgeConfig(log_dir=Path(tmp), enable_lm=False, steer_policy="off")
        )

        out = bridge.handle_event(
            session_id="s1",
            event={
                "type": "tool_call",
                "tool_name": "bash",
                "input": {"command": "rm -rf /tmp/should_not_run"},
            },
        )
        assert out["ok"] is True
        assert "timing" in out and isinstance(out["timing"], dict)
        assert out["actions"]["block"] is True
        assert "rm -rf" in out["actions"]["reason"]


def test_allows_safe_bash():
    with tempfile.TemporaryDirectory() as tmp:
        bridge = PiMonoManagerBridge(
            BridgeConfig(log_dir=Path(tmp), enable_lm=False, steer_policy="off")
        )

        out = bridge.handle_event(
            session_id="s1",
            event={
                "type": "tool_call",
                "tool_name": "bash",
                "input": {"command": "echo hi"},
            },
        )
        assert out["ok"] is True
        assert "timing" in out and isinstance(out["timing"], dict)
        assert out["actions"] == {}


def test_server_oneshot_blocks_dangerous_bash():
    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp) / "bridge"
        payload = {
            "session_id": "s1",
            "event": {
                "type": "tool_call",
                "tool_name": "bash",
                "input": {"command": "rm -rf /tmp/should_not_run"},
            },
        }
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/pi_mono_manager_bridge_server.py",
                "--oneshot",
                "--log-dir",
                str(log_dir),
            ],
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        out = json.loads(proc.stdout.decode("utf-8"))
        assert out["ok"] is True
        assert "timing" in out and isinstance(out["timing"], dict)
        assert out["actions"]["block"] is True
        assert "rm -rf" in out["actions"]["reason"]

        session_dirs = [p for p in log_dir.iterdir() if p.is_dir()]
        assert len(session_dirs) == 1
        assert (session_dirs[0] / "events.jsonl").exists()


def test_server_stdio_handles_multiple_events():
    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp) / "bridge"
        payload1 = {
            "session_id": "s1",
            "event": {"type": "before_agent_start", "prompt": "Fix bug X"},
        }
        payload2 = {
            "session_id": "s1",
            "event": {
                "type": "tool_call",
                "tool_name": "bash",
                "input": {"command": "rm -rf /tmp/should_not_run"},
            },
        }
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/pi_mono_manager_bridge_server.py",
                "--stdio",
                "--log-dir",
                str(log_dir),
            ],
            input=(json.dumps(payload1) + "\n" + json.dumps(payload2) + "\n").encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        lines = proc.stdout.decode("utf-8").splitlines()
        assert len(lines) == 2
        out1 = json.loads(lines[0])
        out2 = json.loads(lines[1])
        assert out1["ok"] is True
        assert "timing" in out1 and isinstance(out1["timing"], dict)
        assert out2["ok"] is True
        assert out2["actions"]["block"] is True
        assert "timing" in out2 and isinstance(out2["timing"], dict)

        session_dirs = [p for p in log_dir.iterdir() if p.is_dir()]
        assert len(session_dirs) == 1
        assert (session_dirs[0] / "events.jsonl").exists()


def main() -> None:
    test_creates_session_and_logs_events()
    print("✓ test_creates_session_and_logs_events passed")
    test_blocks_obviously_dangerous_bash()
    print("✓ test_blocks_obviously_dangerous_bash passed")
    test_allows_safe_bash()
    print("✓ test_allows_safe_bash passed")
    test_server_oneshot_blocks_dangerous_bash()
    print("✓ test_server_oneshot_blocks_dangerous_bash passed")
    test_server_stdio_handles_multiple_events()
    print("✓ test_server_stdio_handles_multiple_events passed")


if __name__ == "__main__":
    main()
