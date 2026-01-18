# Pi Mono Manager-Bridge (Variant B: Manager Outside Pi)

Variant B flips the control boundary:

- **Pi Mono runs the agent loop** (LLM + tools).
- A **Pi extension** forwards lifecycle events (`tool_call`, `tool_result`, `turn_end`) to a **Manager process outside Pi**.
- The external Manager can:
  - record structured traces (for later synthesis),
  - block unsafe tool calls,
  - optionally emit short **steering messages** back into Pi.

This matches our “Manager add-ons” scope (supervision, traces, pattern synthesis) without re-implementing Pi’s core loop.

## What’s implemented in this repo

- Python bridge core: `pi_mono_manager_bridge.py`
  - Session state + event logging to `data/pi_mono_bridge/<session>/events.jsonl`
  - Optional DSPy-based “steer” generation on `turn_end`
- HTTP server wrapper: `scripts/pi_mono_manager_bridge_server.py`
  - `POST /v1/event` with `{session_id, event}`
- Pi extension (manager-bridge): `integrations/pi_mono/extensions/manager_bridge/index.ts`
  - Forwards Pi hook events to the server
  - Applies `{ block, reason }` responses to `tool_call`
  - Injects `actions.steer` back via `pi.sendMessage(..., { deliverAs: "steer"|"nextTurn" })` (no immediate extra turn; default steer on errors only)

## Run the bridge server

Trace-only mode (no LM calls; no API keys required):

`uv run python scripts/pi_mono_manager_bridge_server.py --port 8787`

Unix domain socket mode (avoids TCP ports):

`uv run python scripts/pi_mono_manager_bridge_server.py --unix-socket /tmp/dca_manager_bridge.sock`

No-bind “oneshot” mode (useful in restricted environments; reads stdin JSON, writes stdout JSON):

`echo '{"session_id":"s","event":{"type":"tool_call","tool_name":"bash","input":{"command":"echo hi"}}}' | uv run python scripts/pi_mono_manager_bridge_server.py --oneshot`

Persistent stdio mode (no sockets; reads JSON lines, writes JSON lines; exits on EOF):

`printf '%s\n' '{"session_id":"s","event":{"type":"tool_call","tool_name":"bash","input":{"command":"echo hi"}}}' | uv run python scripts/pi_mono_manager_bridge_server.py --stdio`

Enable steering via DSPy (requires `GEMINI_API_KEY` or Vertex env vars per `config.py`):

`uv run python scripts/pi_mono_manager_bridge_server.py --enable-lm --steer-policy on_error`

## Load the extension in Pi Mono

Run Pi with the extension path and point it at the server:

`DCA_MANAGER_URL=http://127.0.0.1:8787 pi -e /path/to/decision-context-agent/integrations/pi_mono/extensions/manager_bridge/index.ts`

Or use a Unix socket URL (matches `--unix-socket`):

`DCA_MANAGER_URL=unix:/tmp/dca_manager_bridge.sock pi -e /path/to/decision-context-agent/integrations/pi_mono/extensions/manager_bridge/index.ts`

Or via flags (preferred when scripting):

`pi -e /path/to/decision-context-agent/integrations/pi_mono/extensions/manager_bridge/index.ts --manager-url http://127.0.0.1:8787 --manager-timeout-ms 800`

Event forwarding (reduce overhead by default; use `"all"` for full tracing):

- Default: `session_start,before_agent_start,tool_call,turn_end`
- Example (full): `--manager-events all`
- Example (minimal): `--manager-events tool_call,turn_end`

Steering policy (controls whether `turn_end` blocks on waiting for manager guidance):

- Default: `--manager-steer-policy on_error` (awaits manager only if a tool error happened)
- Always steer: `--manager-steer-policy always`
- Disable steering: `--manager-steer-policy off` (still forwards events if enabled, but won’t inject)

## Event contract (v1)

Request:

```json
{
  "session_id": "string",
  "event": { "type": "tool_call|tool_result|turn_end|before_agent_start|session_start", "...": "..." }
}
```

Response:

```json
{
  "ok": true,
  "actions": {
    "block": true,
    "reason": "string",
    "steer": "string"
  },
  "timing": {
    "started_at": "2026-01-18T13:37:00.000000+00:00",
    "ended_at": "2026-01-18T13:37:00.012000+00:00",
    "duration_ms": 12
  }
}
```

## Notes / open edges

- Steering injection uses `pi.sendMessage(..., { deliverAs: "steer"|"nextTurn" })` to avoid creating a new “user prompt” turn; default policy is conservative (`on_error`).
- `tool_call` blocking is currently heuristic-only (no LM), focused on obviously destructive commands.
- If you want “stronger steering”, the next increment is a dedicated DSPy signature for “Manager guidance to Pi” rather than reusing the online-replay planner.

## Testing (step-by-step)

### Step 1: Unit tests (no Pi required)

- Python bridge core:
  - `uv run python test_pi_mono_manager_bridge.py`
- Extension behavior (bundles TS with pi-mono’s esbuild):
  - `node scripts/test_pi_mono_manager_bridge_extension.mjs`

### Step 2: Smoke test the bridge server (no bind)

- `echo '{"session_id":"s","event":{"type":"tool_call","tool_name":"bash","input":{"command":"rm -rf /tmp/nope"}}}' | uv run python scripts/pi_mono_manager_bridge_server.py --oneshot`
- Expect: JSON response with `actions.block=true`.

### Step 3: End-to-end with Pi (trace-only, fastest)

1. Start the manager bridge:
   - `uv run python scripts/pi_mono_manager_bridge_server.py --port 8787`
2. Run Pi with the extension:
   - `pi -e /path/to/decision-context-agent/integrations/pi_mono/extensions/manager_bridge/index.ts --manager-url http://127.0.0.1:8787`
3. Trigger a simple tool call in Pi (e.g., ask it to run `echo hi`).
4. Verify logs:
   - Find the new session folder under `data/pi_mono_bridge/`
   - Confirm `events.jsonl` is being appended

### Step 4: Optional — enable DSPy steering + verify LM timings

1. Start the manager bridge with LM enabled:
   - `uv run python scripts/pi_mono_manager_bridge_server.py --enable-lm --steer-policy on_error`
2. Ensure Pi is set to await steering (or set it explicitly):
   - `--manager-steer-policy on_error` (default) or `--manager-steer-policy always`
3. After a run, inspect:
   - `data/pi_mono_bridge/<session>/lm_usage_events.jsonl`
4. Expect each entry to include `timing.duration_ms` (plus `started_at`/`ended_at`).
