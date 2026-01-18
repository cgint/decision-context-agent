#!/usr/bin/env python3

from __future__ import annotations

# ruff: noqa: E402

import sys
import argparse
import contextlib
import json
import os
import socketserver
import stat
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# Ensure repo-root imports work when invoked as `python scripts/...`.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pi_mono_manager_bridge import BridgeConfig, PiMonoManagerBridge


def _read_body_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length_raw = handler.headers.get("Content-Length", "")
    try:
        length = int(length_raw)
    except ValueError:
        length = 0
    body = handler.rfile.read(length) if length > 0 else b""
    if not body:
        return {}
    try:
        value = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(handler: BaseHTTPRequestHandler, code: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Decision Context Agent Manager-Bridge server for Pi Mono (Variant B).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--unix-socket", default="", help="If set, listen on a Unix domain socket path instead of TCP.")
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Read JSON lines from stdin and write JSON responses to stdout (persistent, no bind).",
    )
    parser.add_argument(
        "--oneshot",
        action="store_true",
        help="Read one JSON payload from stdin and write JSON response to stdout (no bind).",
    )
    parser.add_argument("--log-dir", default="data/pi_mono_bridge")
    parser.add_argument("--enable-lm", action="store_true", help="Enable DSPy LM calls for steering (requires API keys).")
    parser.add_argument("--model", default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--reasoning-effort", default=None, choices=["low", "medium", "high", "disable"])
    parser.add_argument("--steer-policy", default="off", choices=["off", "on_error", "always"])
    args = parser.parse_args()

    defaults = BridgeConfig()
    config = BridgeConfig(
        log_dir=Path(args.log_dir),
        enable_lm=bool(args.enable_lm),
        model=args.model or defaults.model,
        temperature=args.temperature if args.temperature is not None else defaults.temperature,
        reasoning_effort=args.reasoning_effort if args.reasoning_effort is not None else defaults.reasoning_effort,
        steer_policy=args.steer_policy,  # type: ignore[arg-type]
    )
    bridge = PiMonoManagerBridge(config)

    if args.stdio and args.oneshot:
        raise SystemExit("--stdio and --oneshot are mutually exclusive")

    if args.oneshot:
        raw = sys.stdin.read()
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {}

        session_id = str(payload.get("session_id", "") or "").strip() if isinstance(payload, dict) else ""
        event = payload.get("event") if isinstance(payload, dict) else None

        if not session_id or not isinstance(event, dict):
            sys.stdout.write(
                json.dumps(
                    {"ok": False, "error": "expected stdin JSON: {session_id: str, event: object}"},
                    ensure_ascii=False,
                )
                + "\n"
            )
            return

        try:
            response = bridge.handle_event(session_id=session_id, event=event)
        except Exception as exc:
            sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False) + "\n")
            return

        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        return

    if args.stdio:
        for line in sys.stdin:
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                sys.stdout.write(json.dumps({"ok": False, "error": "invalid json"}, ensure_ascii=False) + "\n")
                sys.stdout.flush()
                continue

            if not isinstance(payload, dict):
                sys.stdout.write(
                    json.dumps({"ok": False, "error": "expected object payload"}, ensure_ascii=False) + "\n"
                )
                sys.stdout.flush()
                continue

            session_id = str(payload.get("session_id", "") or "").strip()
            event = payload.get("event")
            if not session_id or not isinstance(event, dict):
                sys.stdout.write(
                    json.dumps({"ok": False, "error": "expected {session_id: str, event: object}"}, ensure_ascii=False)
                    + "\n"
                )
                sys.stdout.flush()
                continue

            try:
                response = bridge.handle_event(session_id=session_id, event=event)
            except Exception as exc:
                sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False) + "\n")
                sys.stdout.flush()
                continue

            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        return

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/healthz"):
                _write_json(self, 200, {"ok": True})
                return
            _write_json(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/event":
                _write_json(self, 404, {"ok": False, "error": "not found"})
                return

            payload = _read_body_json(self)
            session_id = str(payload.get("session_id", "") or "").strip()
            event = payload.get("event")
            if not session_id or not isinstance(event, dict):
                _write_json(self, 400, {"ok": False, "error": "expected {session_id: str, event: object}"})
                return

            try:
                response = bridge.handle_event(session_id=session_id, event=event)
            except Exception as exc:
                _write_json(self, 500, {"ok": False, "error": str(exc)})
                return
            _write_json(self, 200, response if isinstance(response, dict) else {"ok": True})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    unix_socket = (args.unix_socket or "").strip()
    if unix_socket:
        socket_path = Path(unix_socket).expanduser().resolve()
        if socket_path.exists():
            mode = os.stat(socket_path).st_mode
            if stat.S_ISSOCK(mode):
                socket_path.unlink()
            else:
                raise SystemExit(f"--unix-socket path exists and is not a socket: {socket_path}")

        class ThreadingUnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        server = ThreadingUnixHTTPServer(str(socket_path), Handler)
        try:
            print(f"pi-mono manager-bridge listening on unix:{socket_path}")
            server.serve_forever()
        finally:
            server.server_close()
            with contextlib.suppress(FileNotFoundError):
                socket_path.unlink()
        return

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"pi-mono manager-bridge listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
