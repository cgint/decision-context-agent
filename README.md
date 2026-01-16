# Decision Context Tracing

**From Raw Agent Logs to Organizational Physics.**

This project builds a three-stage intelligence pipeline that transforms raw AI agent conversation logs into a structured **Learned Pattern Library**—an emergent catalog of decision heuristics that define the "physics" of successful work in this codebase.

## 🚀 The Pipeline

1.  **Milestone 1 (Event Extraction)**: Flatten complex Gemini CLI JSON logs into a clean, searchable event stream (`events.json`).
2.  **Milestone 2 (Reasoning Traces)**: Use **DSPy + Gemini-3-Flash** to extract the *why* behind agent decisions (patterns, reasoning, outcomes) into `reasoning_traces_automated.json`.
3.  **Milestone 3 (Pattern Synthesis)**: Recursively synthesize global principles from individual session learnings into a **Gold Standard Pattern Library** (`learned_patterns.json`).

## 🧠 The Learned Pattern Library

Our pattern library isn't a pre-defined schema; it is the **residue of successful coordination**. It identifies 8 core behavioral patterns that separate successful senior-level work from failure in this environment.

- **Holistic Context Gathering**: Mandatory "look before leaping."
- **Environment-Aware Recovery**: Fast pivoting when standard tools fail.
- **Terminal-State Consistency**: Preventing UI/UX hangs.
- **Fidelity-Verification**: Trusting but verifying non-deterministic outputs.

## 📁 Repository Structure

- `parse_session.py`: Log-to-Event parser.
- `derive_reasoning_traces.py`: Event-to-Trace insight extractor (DSPy).
- `synthesize_patterns.py`: Trace-to-Patterns recursive synthesizer (DSPy).
- `data/`: Contains processed sessions and the global `/patterns`.
- `sessions/`: Raw Gemini CLI logs.
- `papers/`: Research foundation for context curation and agent architectures.

## 🛠️ Usage

This project uses `uv` for script execution.

```bash
# Extract events from a raw log
uv run parse_session.py sessions/gemini/your_session.json

# Derive reasoning traces for a session
uv run derive_reasoning_traces.py -i data/your_session/events.json

# Synthesize the global pattern library
uv run synthesize_patterns.py
```

## 📜 Documentation

- [Milestone 3: Learned Patterns](./MILESTONE_3_LEARNED_PATTERNS.md)
- [README: What is a Learned Pattern Library?](./README_learned_patterns.md)
- [Vision & Philosophy](./VISION.md)
