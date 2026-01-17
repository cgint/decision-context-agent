# Comparison: Edit Approaches & Supervision Strategy

## Overview
This document compares the file-editing strategies used in **NanoCode**, **Pi Mono**, and our current **Decision Context Agent**, while outlining the future of the Supervision/Orchestration layer.

### 1. NanoCode: Minimalist & Direct
NanoCode uses a very simple "find and replace" mechanism.
*   **How it works:** It looks for an exact string and replaces it with a new one.
*   **Success factor:** High usability due to zero hidden complexity. The tool is a **passive utility**.

### 2. Pi Mono: Surgical & Safe (The Origin)
We inherited the "Surgical Edit" concept from Pi Mono.
*   **How it works:** Emphasizes precision with exact matches (`oldText`) and normalized matching for robustness.
*   **Philosophy:** "Do no harm." Balanced safety and precision.

### 3. Our Current Approach: The "Micromanaging Supervisor"
While we took the core logic from Pi Mono, we wrapped it in a heavy layer of "guardrails" that act as an **active supervisor**.
*   **The Problem:** The supervisor is currently **micromanaging**. It gates individual tool calls (edits) with proactive blocking, hallucination detection, and forced corrective instructions.
*   **Friction:** This middleware often disrupts the agent's flow, adds latency, and creates "instructional noise" that confuses rather than helps.

---

## The Pivot: From Micromanagement to Orchestration

The goal is to move the supervisor from the **Tool-Gatekeeper** level to the **Orchestrator** level.

### The Supervisor's New Role
Instead of checking individual edits, the supervisor (Manager) focuses on:
1.  **High-Level Alignment:** Ensuring the agent is still moving toward the `OVERALL_GOAL`.
2.  **Loop Detection:** Identifying when the agent is stuck (e.g., 3 failed attempts on the same task).
3.  **Strategic Steering:** Providing "Steering Notes" based on outcomes, rather than preventing actions based on predictions.

### Agent Client Protocol (ACP) as the Boundary
The **ACP (Agent Client Protocol)** serves as the formal boundary between the **Manager (Supervisor)** and the **Actor (Agent)**.
*   **Manager ↔ Actor Contract:** Delegation happens at the `TASK` level via `ASSIGNMENT` and `RESULT` headers.
*   **Decoupled Execution:** The Manager provides the task and success criteria; the Actor decides how to use tools (read, write, edit, bash) to achieve it.
*   **Evidence-Based Handoff:** The Manager only intervenes when the Actor returns a result or when higher-level heuristics (step limits, repetition) trigger.

---

## Integration Strategy: "The ACP Bridge"

A recurring question is whether to integrate Pi Mono into our implementation or vice-versa. 
 
### My Analysis & Recommendation
We should adopt an **ACP-First Integration Strategy**.

1.  **Maintain Decision Context Agent as the "Manager":** Our core value is in the recursive reasoning (DSPy + Gemini) and the "organizational physics" extraction. This is the **Supervisor**.
2.  **Use Pi Mono as the "Actor":** Pi Mono provides a mature, battle-tested toolset (surgical edits, TUI, etc.).
3.  **The Strategy:** Instead of importing Pi Mono *code* into our repository (which led to the micromanagement wrapper), we should integrate with Pi Mono *as a service* over ACP.

### Can we still test this with SWE-bench Lite?
**Yes.** In fact, this architecture is **ideal** for SWE-bench Lite for several reasons:

*   **Cleaner Isolation:** The Actor (Pi-Mono) runs as a subprocess. It doesn't need to know about the host's environment or the SWE-bench harness.
*   **Centralized Tool Execution:** In the ACP model, the **Manager** implements the tools (filesystem, terminal) and the **Actor** calls them. Since our Manager is already pointed at the SWE-bench workspace, any edit or command requested by the Actor will automatically happen in the correct repo instance.
*   **Robustness:** By formalizing the boundary between "Decision" and "Action," we reduce the risk of the agent's internal state being corrupted by the evaluation environment.

### What to do next:
1.  **Clean up Local Tools:** Strip the "micromanagement" wrappers from our local `Direct Mode` implementation. Let it be a "Minimal Actor."
2.  **Formalize the Orchestrator:** Refactor `online_replay_loop.py` to treat all execution as an ACP-style delegation.
3.  **Bridge Pi-Mono:** Ensure `pi` (from Pi-Mono) can act as an ACP server (similar to `opencode acp`), or use `opencode` as the primary ACP-compatible Actor while borrowing Pi-Mono's tool philosophies.
4.  **SWE-bench Injection:** Update `run_swebench_eval.py` to support the `--actor opencode-acp` flag, ensuring it passes the instance-specific workspace to the Manager.

By treating the supervisor as an **Orchestrator** at the ACP boundary, we gain the safety and alignment we want without the friction of micromanagement, while maintaining full compatibility with benchmarking suites like SWE-bench Lite.

---

## Pi Mono Hook Points: Skills vs. Extensions

Pi Mono has two customization mechanisms that often get conflated:

### Skills (on-demand capability packages)
Skills are **self-contained instruction/workflow bundles** (`SKILL.md` + optional scripts/docs) that the agent loads on-demand when it decides the task matches the skill description (or when explicitly invoked via `/skill:name`). Skills are best for “how to do X” guidance and helper scripts, not for system-level interception.

### Extensions (actual interception + instrumentation)
Extensions are **TypeScript modules** that can register tools/commands and subscribe to lifecycle events (e.g., `tool_call`, `tool_result`, `turn_end`, compaction events). This is the real “hook yourself into the agent runtime” surface:
- **Observe**: emit structured telemetry for tool calls/results and message deltas.
- **Modify/block**: enforce minimal safety policy (sparingly; avoid recreating micromanagement).
- **Augment**: add custom tools/commands to support tracing/export flows.

### Programmatic embedding (for Manager/Actor integration)
Pi Mono also supports **SDK embedding** (Node/TypeScript) and **RPC mode** for process-isolated, headless integration. This can be an alternative to ACP if ACP transport/servers are a bottleneck.

### How this maps to our architecture
- Keep Decision Context Agent as the **Manager** (supervision, traces, pattern synthesis).
- Use Pi Mono as the **Actor**, and (optionally) add a small **Extension** focused on observability/telemetry so the Manager can capture tool-backed evidence without pushing supervision down into per-tool gatekeeping.
