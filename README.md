# Decision Context Agent (SkitHub)

**From Raw Agent Logs to Organizational Physics.**

> "The schema isn't something you define upfront. It emerges from the walks."

## 🔍 Overview

This repository hosts the **Decision Context Agent** (also referred to as SkitHub), an experimental intelligence pipeline designed to transform raw AI agent conversation logs into a structured **Learned Pattern Library**.

Unlike traditional logging systems that capture *what* happened, this system uses advanced recursive reasoning (DSPy + Gemini) to extract the *why*—the implicit decision heuristics and "organizational physics" that distinguish successful expert work from failure.

## 🧠 The Core Problem & Solution

**The Problem:** Most AI agents lack "transferable wisdom." They don't learn from past sessions. Explicit ontologies (databases, predefined schemas) fail to capture the subtle, implicit context of *why* a decision was made.

**The Solution:** Instead of pre-defining a schema, we let it emerge. We treat agent trajectories (logs) as training data for an organizational world model.
1.  **Record** immutable events.
2.  **Derive** reasoning traces (the "thought process" behind the action).
3.  **Synthesize** global patterns that can guide future agents.

## 🏗 Technical Architecture

This project implements a three-stage processing pipeline:

### 1. Event Extraction (ETL)
Flattens complex, nested CLI logs (JSON) into a linear, analyzable stream of events.
*   **Input:** Raw Gemini CLI session logs.
*   **Output:** `events.json` (Time, Actor, Message, Tool Use).
*   **Tool:** `parse_session.py`

### 2. Reasoning Traces (DSPy + Gemini-3-Flash)
Uses **DSPy** programs to analyze the event stream. It reconstructs the agent's "State of Mind" at critical decision points, identifying:
*   **Context:** What did the agent know?
*   **Intent:** What was it trying to achieve?
*   **Outcome:** Did it work?
*   **Output:** `reasoning_traces_automated.json`

### 3. Pattern Synthesis (RLM-PoT)
Recursively aggregates individual traces to find recurring behaviors. It generates a **Learned Pattern Library**—a catalog of "physics" for this specific codebase (e.g., "Holistic Context Gathering", "Environment-Aware Recovery").
*   **Method:** **RLM-PoT (Recursive Language Model via Program of Thought)**. Instead of stuffing context, the model writes code to query and analyze the traces, ensuring 100% deterministic coverage of the dataset.

## 🚀 Getting Started

This project uses `uv` for fast, reliable Python package and environment management.

### Prerequisites
*   Python 3.12+
*   `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

### Installation
```bash
git clone https://github.com/yourusername/decision-context-agent.git
cd decision-context-agent
uv sync
```

### Usage Pipeline

1.  **Parse a Session:**
    ```bash
    uv run parse_session.py sessions/gemini/raw_log.json
    ```

2.  **Extract Reasoning:**
    ```bash
    uv run derive_reasoning_traces.py -i data/processed/events.json
    ```

3.  **Synthesize Patterns:**
    ```bash
    uv run synthesize_patterns.py
    ```

## 📂 Repository Structure

*   `bench_agents/`: Agent implementations for evaluation.
*   `docs/`: Documentation.
    *   `external/`: Third-party integrations & benchmarks (SWE-bench).
    *   `information/`: Vision, theory, and architectural concepts.
    *   `task-planning/`: Project status and goals.
*   `rlm_pot/`: Implementation of the Recursive Language Model / Program of Thought executor.
*   `data/`: Storage for processed traces and the global pattern library.
*   `scripts/`: Utility scripts for benchmarks and evaluations.

## 📚 Further Reading

*   **[Vision & Philosophy](docs/information/VISION.md):** The "North Star" for this project.
*   **[RLM-PoT Architecture](docs/information/INFO_RLM_POT_SUMMARY.md):** Deep dive into the "Thinking in Code" approach.
*   **[Idea Origin](docs/information/IDEA.md):** The tweetstorm that sparked the concept of Context Graphs.