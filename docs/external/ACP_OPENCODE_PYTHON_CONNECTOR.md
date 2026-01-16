# OpenCode via ACP (Python Connector)

Goal: run an OpenCode “actor” behind the **text-first** manager↔actor contract (`MANAGER_ACTOR_INTERFACE.md`), using ACP only as the transport.

## What is implemented

- A small ACP **client** backend that spawns `opencode acp` over stdio (NDJSON JSON-RPC) using the official Python SDK (`agent-client-protocol`, import `acp`).
- Auto-approves all permission requests and bridges filesystem + terminal tool calls locally.
- Captures a transcript of `session/update` events (message chunks, thought chunks, tool call updates).

Files:
- `acp_opencode_backend.py`
- `scripts/opencode_acp_run_assignment.py`

## Usage (single assignment)

Create an Assignment message (per `MANAGER_ACTOR_INTERFACE.md`) and run:

```bash
uv run python scripts/opencode_acp_run_assignment.py --workspace-dir . --out-dir /tmp/acp_run
```

Then paste the Assignment on stdin (or use `--assignment-file path/to/assignment.txt`).

Outputs:
- `/tmp/acp_run/agent_message.txt` (what OpenCode returned as the agent “message” stream)
- `/tmp/acp_run/agent_thought.txt` (if OpenCode emits thought chunks)
- `/tmp/acp_run/acp_transcript.json` (all captured ACP updates)
- `/tmp/acp_run/opencode_stderr.txt` (OpenCode logs)

## Contract boundary (important)

- Manager↔actor interface stays **text-only**.
- ACP backend sends the assignment text as a single `session/prompt` text block.
- The assignment includes an instruction to respond using the **Result** format from `MANAGER_ACTOR_INTERFACE.md`, so the manager can parse it deterministically.

## Current limitations

- Spawns a new `opencode acp` process per assignment (fine for “first tries”; later we can reuse a session/process).
- The connector currently runs with “auto-approve everything” to maximize throughput; later we can add a policy layer without changing the manager↔actor contract.
