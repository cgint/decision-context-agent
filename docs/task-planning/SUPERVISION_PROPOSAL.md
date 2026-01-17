# Proposal: Shifting Supervision to High-Level Orchestration

## The Problem: Micromanagement
Our current "Direct Mode" loop acts as a micromanaging supervisor. It gates individual tool calls (especially edits) with heavy deterministic logic:
*   **Hallucination Detection:** Rejects edits before they run if they look "guessed."
*   **Complex Normalization:** Attempts to "fix" agent edits behind the scenes.
*   **Deterministic Loop Breaking:** Forces specific commands (like `rg`) when edits fail.

This creates friction, increases latency, and often confuses the agent by providing corrective instructions that don't match its current mental model.

## The Vision: High-Level Orchestration
The supervisor should transition from a **Tool-Gatekeeper** to an **Orchestrator**. 

### 1. Passive Tools, Active Supervision
Tools should be simple and direct (like NanoCode's `edit` or Pi Mono's base `edit`). They should execute exactly what the agent asks and return raw evidence (success/fail + diff). 

Supervision happens at the **loop level**:
*   **Loop Detection:** The `DecisionPlanner` analyzes the history to see if the agent is stuck (e.g., 3 failed attempts on the same file).
*   **Alignment Checking:** The supervisor verifies if the agent's recent findings and decisions still align with the `OVERALL_GOAL`.
*   **Strategic Steering:** Instead of rewriting a command, the supervisor provides a "Steering Note" in the next observation (e.g., *"You've failed this edit 3 times. It seems you're guessing the content; try reading the file with line numbers first."*).

### 2. Standardizing on the ACP Pattern
The **Manager ↔ Actor Interface** (Assignment/Result) should be the primary boundary.
*   **Manager (Supervisor):** Defines the `TASK` and `SUCCESS_CRITERIA`.
*   **Actor (Agent):** Bounded execution. Decides *how* to use tools to meet the criteria.
*   **Handoff:** The Manager only intervenes when the Actor returns a `RESULT` or when a high-level heuristic (like a step limit or repetition) is triggered.

## Proposed Action Plan

### Phase 1: Clean up Direct Mode
*   Simplify `perform_surgical_edit` to be a transparent find/replace (matching NanoCode/Pi Mono style).
*   Remove `python_edit_preflight` blocks that attempt to predict failure.
*   Let the agent see the raw tool failure and decide how to recover.

### Phase 2: Implement Orchestral Supervision
*   Enhance the `DecisionPlanner` prompt to specifically look for loops and goal drift in the `history_events_json`.
*   Add a `Steering` component that can inject guidance into the prompt without overriding the agent's choice of tool.

### Phase 3: Transition to ACP-First
*   Make `opencode-acp` the default path.
*   Treat the local `Direct Mode` loop as a "Minimal Actor" that follows the same Assignment/Result contract, allowing us to use the same Supervisor logic for both local and remote agents.
