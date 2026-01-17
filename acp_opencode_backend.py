from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acp.client.connection import ClientSideConnection
from acp.interfaces import Client
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    AllowedOutcome,
    ClientCapabilities,
    CreateTerminalResponse,
    CurrentModeUpdate,
    DeniedOutcome,
    EnvVariable,
    FileSystemCapability,
    Implementation,
    KillTerminalCommandResponse,
    PermissionOption,
    PromptResponse,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    SessionInfoUpdate,
    TerminalExitStatus,
    TerminalOutputResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UserMessageChunk,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)
from acp.stdio import spawn_stdio_transport

ACP_STDIO_LIMIT_BYTES = 8 * 1024 * 1024


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _to_jsonable(model_dump(mode="json"))
    return {"repr": repr(value)}


@dataclass(frozen=True)
class ACPTranscriptEvent:
    at: str
    kind: str
    payload: dict[str, Any]


@dataclass
class ACPPromptResult:
    stop_reason: str
    agent_message: str
    agent_thought: str
    transcript: list[ACPTranscriptEvent]
    opencode_stderr: str


class AutoApproveToolClient(Client):
    """
    ACP Client implementation for running a local agent (OpenCode) with:
    - auto-approved permissions
    - local fs read/write
    - local terminal command execution
    - streaming transcript capture via session/update
    """

    def __init__(
        self, *, workspace_dir: Path, terminal_output_byte_limit: int = 200_000
    ):
        self._workspace_dir = workspace_dir
        self._terminal_output_byte_limit = terminal_output_byte_limit

        self._agent_message_parts: list[str] = []
        self._agent_thought_parts: list[str] = []
        self._transcript: list[ACPTranscriptEvent] = []

        self._terminal_procs: dict[str, asyncio.subprocess.Process] = {}
        self._terminal_buffers: dict[str, bytearray] = {}
        self._terminal_limits: dict[str, int] = {}

    @property
    def transcript(self) -> list[ACPTranscriptEvent]:
        return list(self._transcript)

    @property
    def agent_message(self) -> str:
        return "".join(self._agent_message_parts).strip()

    @property
    def agent_thought(self) -> str:
        return "".join(self._agent_thought_parts).strip()

    def _stamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _record(self, kind: str, payload: Any) -> None:
        payload_dict = _to_jsonable(payload)
        if not isinstance(payload_dict, dict):
            payload_dict = {"value": payload_dict}
        self._transcript.append(
            ACPTranscriptEvent(at=self._stamp(), kind=kind, payload=payload_dict)
        )

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: ToolCallUpdate,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        self._record(
            "session/request_permission",
            {
                "session_id": session_id,
                "options": [o.model_dump(mode="json") for o in options],
                "tool_call": tool_call.model_dump(mode="json"),
            },
        )
        if options:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(
                    outcome="selected", option_id=options[0].option_id
                )
            )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self._record("session/update", {"session_id": session_id, "update": update})

        if (
            isinstance(update, AgentMessageChunk)
            and getattr(update.content, "type", "") == "text"
        ):
            self._agent_message_parts.append(update.content.text)
        elif (
            isinstance(update, AgentThoughtChunk)
            and getattr(update.content, "type", "") == "text"
        ):
            self._agent_thought_parts.append(update.content.text)
        elif isinstance(
            update,
            (
                ToolCallStart,
                ToolCallProgress,
                ToolCallUpdate,
                UserMessageChunk,
                CurrentModeUpdate,
                SessionInfoUpdate,
            ),
        ):
            return

    def _resolve_path(self, path: str) -> Path:
        raw = (path or "").strip()
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self._workspace_dir / candidate
        return candidate.resolve()

    async def write_text_file(
        self, content: str, path: str, session_id: str, **kwargs: Any
    ) -> WriteTextFileResponse:
        target = self._resolve_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._record(
            "fs/write_text_file",
            {"session_id": session_id, "path": str(target), "bytes": len(content)},
        )
        return WriteTextFileResponse()

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        target = self._resolve_path(path)
        text = target.read_text(encoding="utf-8")
        if line is not None and line > 1:
            lines = text.splitlines(True)
            text = "".join(lines[line - 1 :])
        if limit is not None and limit > 0:
            text = text[:limit]
        self._record(
            "fs/read_text_file",
            {"session_id": session_id, "path": str(target), "chars": len(text)},
        )
        return ReadTextFileResponse(content=text)

    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: list[EnvVariable] | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> CreateTerminalResponse:
        terminal_id = f"term_{uuid.uuid4().hex[:10]}"
        merged_env = os.environ.copy()
        for item in env or []:
            merged_env[item.name] = item.value

        proc = await asyncio.create_subprocess_exec(
            command,
            *(args or []),
            cwd=cwd or str(self._workspace_dir),
            env=merged_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._terminal_procs[terminal_id] = proc
        self._terminal_buffers[terminal_id] = bytearray()

        effective_limit = output_byte_limit or self._terminal_output_byte_limit
        self._terminal_limits[terminal_id] = effective_limit

        async def _drain() -> None:
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                buf = self._terminal_buffers[terminal_id]
                if len(buf) < effective_limit:
                    buf.extend(chunk[: max(0, effective_limit - len(buf))])

        asyncio.create_task(_drain())
        self._record(
            "terminal/create",
            {
                "session_id": session_id,
                "terminal_id": terminal_id,
                "command": command,
                "args": args or [],
            },
        )
        return CreateTerminalResponse(terminal_id=terminal_id)

    async def terminal_output(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> TerminalOutputResponse:
        buf = self._terminal_buffers.get(terminal_id, bytearray())
        limit = self._terminal_limits.get(terminal_id, self._terminal_output_byte_limit)
        truncated = len(buf) >= limit
        proc = self._terminal_procs.get(terminal_id)
        exit_status = None
        if proc and proc.returncode is not None:
            exit_status = TerminalExitStatus(exit_code=proc.returncode)
        return TerminalOutputResponse(
            output=buf.decode("utf-8", errors="replace"),
            truncated=truncated,
            exit_status=exit_status,
        )

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> WaitForTerminalExitResponse:
        proc = self._terminal_procs.get(terminal_id)
        if not proc:
            return WaitForTerminalExitResponse(exit_code=127)
        code = await proc.wait()
        return WaitForTerminalExitResponse(exit_code=code)

    async def kill_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> KillTerminalCommandResponse:
        proc = self._terminal_procs.get(terminal_id)
        if proc and proc.returncode is None:
            proc.terminate()
        return KillTerminalCommandResponse()

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> ReleaseTerminalResponse:
        self._terminal_procs.pop(terminal_id, None)
        self._terminal_buffers.pop(terminal_id, None)
        self._terminal_limits.pop(terminal_id, None)
        return ReleaseTerminalResponse()


class OpencodeACPBackend:
    def __init__(
        self,
        *,
        workspace_dir: Path,
        opencode_bin: str = "opencode",
        log_level: str = "INFO",
        prompt_timeout_s: float = 600.0,
        initialize_timeout_s: float = 30.0,
        session_timeout_s: float = 30.0,
    ):
        self._workspace_dir = workspace_dir
        self._opencode_bin = opencode_bin
        self._log_level = log_level
        self._prompt_timeout_s = prompt_timeout_s
        self._initialize_timeout_s = initialize_timeout_s
        self._session_timeout_s = session_timeout_s

    async def run_assignment(
        self,
        *,
        assignment_text: str,
        assignment_id: str | None = None,
        progress_dir: Path | None = None,
        progress_interval_s: float = 2.0,
    ) -> ACPPromptResult:
        assignment_id = assignment_id or f"A-{uuid.uuid4().hex[:8]}"
        client_impl = AutoApproveToolClient(workspace_dir=self._workspace_dir)
        opencode_stderr_parts: list[str] = []
        progress_task: asyncio.Task | None = None
        progress_stop = asyncio.Event()

        async with spawn_stdio_transport(
            self._opencode_bin,
            "acp",
            "--cwd",
            str(self._workspace_dir),
            "--print-logs",
            "--log-level",
            self._log_level,
            limit=ACP_STDIO_LIMIT_BYTES,
        ) as (stdout_reader, stdin_writer, proc):

            async def _drain_stderr() -> None:
                if proc.stderr is None:
                    return
                while True:
                    chunk = await proc.stderr.read(4096)
                    if not chunk:
                        break
                    opencode_stderr_parts.append(
                        chunk.decode("utf-8", errors="replace")
                    )

            stderr_task = asyncio.create_task(_drain_stderr())

            conn = ClientSideConnection(client_impl, stdin_writer, stdout_reader)
            await asyncio.wait_for(
                conn.initialize(
                    protocol_version=1,
                    client_capabilities=ClientCapabilities(
                        fs=FileSystemCapability(
                            read_text_file=True, write_text_file=True
                        ),
                        terminal=True,
                    ),
                    client_info=Implementation(
                        name="decision-context-tracing", version="0.1.0"
                    ),
                ),
                timeout=self._initialize_timeout_s,
            )
            session = await asyncio.wait_for(
                conn.new_session(cwd=str(self._workspace_dir), mcp_servers=[]),
                timeout=self._session_timeout_s,
            )
            session_id = session.session_id

            prompt_blocks = [
                TextContentBlock(
                    type="text",
                    text=(
                        f"{assignment_text.rstrip()}\n\n"
                        "IMPORTANT: Respond in the Manager↔Actor Result format from MANAGER_ACTOR_INTERFACE.md.\n"
                        "OUTPUT_FORMAT: Respond ONLY with these headers (exact):\n"
                        "RESULT: <success|partial|fail>\n"
                        "EVIDENCE: <file path with line numbers or command output>\n"
                        "STATE_DELTA: <one bullet>\n"
                        f"ASSIGNMENT_ID: {assignment_id}\n"
                    ),
                )
            ]

            async def _do_prompt() -> PromptResponse:
                return await conn.prompt(prompt=prompt_blocks, session_id=session_id)

            if progress_dir:
                progress_dir.mkdir(parents=True, exist_ok=True)

                async def _progress_loop() -> None:
                    while not progress_stop.is_set():
                        OpencodeACPBackend.persist_progress(
                            progress_dir,
                            agent_message=client_impl.agent_message,
                            agent_thought=client_impl.agent_thought,
                            transcript=client_impl.transcript,
                            opencode_stderr="".join(opencode_stderr_parts).strip(),
                        )
                        try:
                            await asyncio.wait_for(
                                progress_stop.wait(), timeout=progress_interval_s
                            )
                        except asyncio.TimeoutError:
                            continue

                progress_task = asyncio.create_task(_progress_loop())

            try:
                prompt_response = await asyncio.wait_for(
                    _do_prompt(), timeout=self._prompt_timeout_s
                )
            except asyncio.TimeoutError:
                prompt_response = PromptResponse(stop_reason="max_tokens")
            finally:
                if progress_task is not None:
                    progress_stop.set()
                    progress_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await progress_task
                await conn._conn.close()  # type: ignore[attr-defined]
                stderr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await stderr_task

        return ACPPromptResult(
            stop_reason=str(prompt_response.stop_reason),
            agent_message=client_impl.agent_message,
            agent_thought=client_impl.agent_thought,
            transcript=client_impl.transcript,
            opencode_stderr="".join(opencode_stderr_parts).strip(),
        )

    @staticmethod
    def persist_progress(
        result_dir: Path,
        *,
        agent_message: str,
        agent_thought: str,
        transcript: list[ACPTranscriptEvent],
        opencode_stderr: str,
    ) -> None:
        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / "agent_message.partial.txt").write_text(
            agent_message + "\n", encoding="utf-8"
        )
        (result_dir / "agent_thought.partial.txt").write_text(
            agent_thought + "\n", encoding="utf-8"
        )
        (result_dir / "opencode_stderr.partial.txt").write_text(
            opencode_stderr + "\n", encoding="utf-8"
        )
        (result_dir / "acp_transcript.partial.json").write_text(
            json.dumps([e.__dict__ for e in transcript], indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def persist_result(result_dir: Path, result: ACPPromptResult) -> None:
        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / "agent_message.txt").write_text(
            result.agent_message + "\n", encoding="utf-8"
        )
        (result_dir / "agent_thought.txt").write_text(
            result.agent_thought + "\n", encoding="utf-8"
        )
        (result_dir / "stop_reason.txt").write_text(
            result.stop_reason + "\n", encoding="utf-8"
        )
        (result_dir / "opencode_stderr.txt").write_text(
            result.opencode_stderr + "\n", encoding="utf-8"
        )
        (result_dir / "acp_transcript.json").write_text(
            json.dumps(
                [e.__dict__ for e in result.transcript], indent=2, sort_keys=True
            )
            + "\n",
            encoding="utf-8",
        )
