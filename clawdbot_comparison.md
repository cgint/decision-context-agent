# Comparison: Clawdbot vs. Decision Context Agent (Genie)

This document provides a comparative analysis between **Clawdbot** (a safety-focused chat gateway) and the **Decision Context Agent** (also known as Genie/SkitHub, a learning-focused intelligence pipeline).

## Executive Summary

| Feature | Clawdbot | Decision Context Agent (Genie) |
| :--- | :--- | :--- |
| **Core Philosophy** | **Safety & Stability.** Prevent "agentic chaos" via strict gating and central authority. | **Learning & Evolution.** Transform raw logs into "Organizational Physics" via recursive reasoning. |
| **Primary Output** | **Active Conversation.** A helpful, safe, persistent chat assistant. | **Learned Pattern Library.** A catalog of heuristics ("why" decisions were made) and code variables. |
| **Memory Model** | **Vector RAG + Flush.** Periodically flushes chat to disk; uses fuzzy semantic search. | **RLM-PoT (Program of Thought).** Uses a persistent Python namespace to store *exact* variables ("Context as Variable"). |
| **Execution** | **Gateway Control Plane.** Agents are execution frames managed by a central gateway. | **Manager-Actor Loop.** A structured task assignment loop (`Assignment` -> `Result`). |
| **Isolation** | **Docker by Default.** Enforces containers for non-main sessions. | **Local / Hybrid.** Runs via `uv` locally; uses Docker primarily for benchmarks (SWE-bench). |
| **Gating** | **Pairing Protocol.** Challenge-response with manual CLI approval (`clawdbot pairing approve`). | **Internal Protocol.** Focuses on internal task delegation rather than external user gating. |

## 1. Architecture & Control

### Clawdbot: The Strict Gateway
Clawdbot is designed as a **Central Authority**. It assumes the world is chaotic and untrusted.
*   **Single Source of Truth:** The Gateway manages all state updates atomically using single-writer locks (`proper-lockfile`).
*   **Inbound Gating:** Implements a strict "Pairing Protocol" where new users must be explicitly approved via a CLI challenge-response.
*   **Goal:** Prevent independent processes from drifting or causing race conditions.

### Genie: The Manager-Actor Loop
Genie operates as a **Task Solver**. It assumes the goal is to execute complex tasks and learn from them.
*   **Protocol:** Uses a text-based `Assignment` -> `Result` protocol (`manager_actor_protocol.py`).
*   **Structure:** The "Manager" (Planner) assigns structured tasks to the "Actor" (Executor), who returns evidence-backed results.
*   **Goal:** Optimize the decision loop and extract reasoning traces for future improvement.

## 2. Memory Strategy

### Clawdbot: "Infinite" Fuzzy Recall
Clawdbot treats memory as a storage problem for a long-running chat.
*   **Pre-Compaction Flush:** Before the context window fills, it triggers a silent agent turn to save facts to `memory/YYYY-MM-DD.md`.
*   **Vector Database:** Uses `sqlite` to index these files. Agents use `memory_search` to find relevant past info.
*   **Nature:** **Semantic & Approximate.** "I remember we talked about Docker setup last week."

### Genie: "Context as Variable" (RLM-PoT)
Genie treats memory as a precision tool for software engineering.
*   **RLM-PoT:** Instead of asking the LLM to "remember" a file content (which leads to hallucination), Genie executes Python code to store data in a persistent namespace.
*   **Mechanism:** `PythonExecutor` keeps variables alive across steps.
*   **Nature:** **Exact & Deterministic.** `var_name = match.group(1)` ensures the agent uses the *exact* string found in the file, not a hallucinated version.

## 3. Isolation & Environment

### Clawdbot: Defense in Depth
Clawdbot is built for **Hostile Environments** (public chat platforms).
*   **Docker Enforcement:** Non-main sessions (e.g., group chats) are forced into Docker containers.
*   **Workspace Isolation:** Each agent gets a dedicated file system path (`~/clawd`).

### Genie: Development Pipeline
Genie is built for **Developer Workflows** and **Benchmarks**.
*   **Tool-Level Sandbox:** The `RLM-PoT` module implements a Python-level sandbox (restricted imports, path limits).
*   **Benchmark Isolation:** Uses Docker for "Gold Standard" evaluation (SWE-bench), but standard runs often occur directly in the user's environment (managed via `uv`).

## 4. Conclusion

**Clawdbot** is a **Shield**. It is designed to safely expose LLM agents to the real world (WhatsApp, Telegram) without allowing them to wreck the host machine or get confused by concurrent messages. Its memory is designed for *continuity*.

**Genie (Decision Context Agent)** is a **Scientist**. It is designed to dissect the software engineering process, extract "Reasoning Traces," and build a library of successful behaviors. Its memory (RLM-PoT) is designed for *precision* and *execution*.
