#!/usr/bin/env python3
"""
Online replay loop (blind): DSPy reasoning + bash execution + DSPy evaluation.

Each step:
1) Reasoner turns observation + knowledge into minimal next action.
2) Execute action via bash (TASK/CONTEXT passed through env).
3) Evaluator summarizes command output into next observation.
4) Persist all inputs/outputs and state.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import dspy

from config import DSPyGeminiConfig
from acp_opencode_backend import OpencodeACPBackend
from manager_actor_protocol import Assignment, parse_result, render_result_observation

# RLM-PoT: Python REPL for deterministic pattern extraction and variable storage
try:
    from rlm_pot import (
        PythonExecutor,
        handle_python_action,
        reset_executor,
        is_enabled as rlm_pot_is_enabled,
    )

    RLM_POT_AVAILABLE = True
except ImportError:
    RLM_POT_AVAILABLE = False
    PythonExecutor = None  # type: ignore[assignment,misc]

    def handle_python_action(*args, **kwargs):  # type: ignore[misc]
        return {
            "returncode": "1",
            "stdout": "",
            "stderr": "rlm_pot module not available",
        }

    def reset_executor():  # type: ignore[misc]
        pass

    def rlm_pot_is_enabled() -> bool:  # type: ignore[misc]
        return False


DEFAULT_MAX_STEPS = 10
DEFAULT_PHASE = "analysis"
DEFAULT_INTERACTION_MODE = "human"
DEFAULT_MAX_HISTORY_EVENTS = 30
DEFAULT_MAX_PINNED_FINDINGS = 10
DEFAULT_ACTOR_MODE = "opencode-acp"
DEFAULT_DEGREES_OF_FREEDOM = "Outcome-only freedom"
DEFAULT_RULES_PATH = "STEP_BY_STEP_REASON_RULES.md"

# Hard caps to prevent "chatty" feedback loops in LM-visible context.
MAX_STATE_LINES = 12
MAX_STATE_DELTA_LINES = 8
MAX_ACTIVE_RULE_LINES = 6
MAX_OPEN_QUESTIONS_LINES = 6
MAX_ASSUMPTIONS_LINES = 6

# Auto-select rules file based on phase
PHASE_TO_RULES: Dict[str, str] = {
    "autopilot": "STEP_BY_STEP_REASON_RULES_AUTOPILOT.md",
    "bench": "STEP_BY_STEP_REASON_RULES_BENCH.md",
}

ASSIGNMENT_RULES = """Rules for composing a Manager->Actor Assignment:
- Task-level only; do not prescribe commands or tool steps.
- Prefer atomic, tool-backed tasks (one observable outcome).
- Provide the minimum context needed (evidence anchors only).
- Constraints only when they materially affect success.
- Degrees of freedom must be one of: Outcome-only freedom, Action-envelope, Action-specified.
- Deliverables must include RESULT, EVIDENCE, STATE_DELTA (others optional).
- EVIDENCE must cite at least one concrete tool output (file path + line numbers or command output snippet).
- If evidence cannot be produced, RESULT must be fail with a clear reason.
"""


@dataclass
class RunConfig:
    run_id: str
    run_dir: str
    model: str
    phase: str
    interaction_mode: str
    actor_mode: str
    max_steps: int
    rules_path: str
    max_history_events: int
    max_pinned_findings: int
    started_at: str
    user_request: str
    workspace_dir: str
    snapshot_root: str
    snapshot_autodetect: bool
    opencode_bin: str
    opencode_log_level: str
    opencode_timeout_s: float


class StepReasonerSignature(dspy.Signature):
    """Deprecated: kept for compatibility."""


class KnowledgeConsolidatorSignature(dspy.Signature):
    """Consolidate observation into durable state."""

    goal = dspy.InputField(desc="Overall goal / user request.")
    observation = dspy.InputField(
        desc="Newest observation (user input or tool output)."
    )
    state = dspy.InputField(
        desc="Current durable state (stable facts, decisions, environment)."
    )
    history_events_json = dspy.InputField(
        desc="Minimal event stream as a JSON array string (bounded)."
    )
    pinned_findings = dspy.InputField(
        desc="Pinned findings to carry forward (very short)."
    )
    rules = dspy.InputField(desc="Global rules and constraints.")
    phase = dspy.InputField(desc="Current phase.")
    interaction_mode = dspy.InputField(desc="Interaction mode: human or none.")

    state_delta = dspy.OutputField(
        desc="Bullets only. Max 8 lines. Each line <= 140 chars. No narrative."
    )
    state_updated = dspy.OutputField(
        desc="Bullets only. Keep stable facts only. Max 12 lines. No environment trivia unless decision-relevant. No 'I will'/'need to'."
    )
    active_rules = dspy.OutputField(
        desc="Bullets only. Max 6 lines. Each <= 12 words. No prose."
    )
    open_questions = dspy.OutputField(desc="Bullets only. Max 6 lines. Empty if none.")
    assumptions = dspy.OutputField(desc="Bullets only. Max 6 lines. Empty if none.")


class ActionComposerSignature(dspy.Signature):
    """Select the next action and minimal context."""

    goal = dspy.InputField(desc="Overall goal / user request.")
    observation = dspy.InputField(
        desc="Newest observation (user input or tool output)."
    )
    state = dspy.InputField(desc="Current durable state.")
    history_events_json = dspy.InputField(
        desc="Minimal event stream as a JSON array string (bounded)."
    )
    pinned_findings = dspy.InputField(
        desc="Pinned findings to carry forward (very short)."
    )
    active_rules = dspy.InputField(desc="Concise active rules.")
    phase = dspy.InputField(desc="Current phase.")
    interaction_mode = dspy.InputField(desc="Interaction mode: human or none.")

    plan_hint = dspy.OutputField(desc="1-3 candidate next actions (short).")
    decision = dspy.OutputField(desc="One-line decision + why.")
    action_type = dspy.OutputField(
        desc="One of: shell_command, python_edit, ask_user, stop."
    )
    task = dspy.OutputField(desc="One-sentence next action.")
    success_criteria = dspy.OutputField(desc="What a correct result looks like.")
    command = dspy.OutputField(
        desc="Shell command if action_type == shell_command; Python code if action_type == python_edit; otherwise empty."
    )


class DecisionPlannerSignature(dspy.Signature):
    """Produce a short candidate plan and one-line decision."""

    goal = dspy.InputField(desc="Overall goal / user request.")
    observation = dspy.InputField(
        desc="Newest observation (user input or tool output)."
    )
    state = dspy.InputField(desc="Current durable state.")
    history_events_json = dspy.InputField(
        desc="Minimal event stream as a JSON array string (bounded)."
    )
    pinned_findings = dspy.InputField(
        desc="Pinned findings to carry forward (very short)."
    )
    active_rules = dspy.InputField(desc="Concise active rules.")
    phase = dspy.InputField(desc="Current phase.")
    interaction_mode = dspy.InputField(desc="Interaction mode: human or none.")

    plan_hint = dspy.OutputField(desc="1-3 candidate next actions (short).")
    decision = dspy.OutputField(desc="One-line decision + why.")


class ActionSelectorSignature(dspy.Signature):
    """Pick the next concrete action to run. All output fields are REQUIRED."""

    goal = dspy.InputField(desc="Overall goal / user request.")
    observation = dspy.InputField(
        desc="Newest observation (user input or tool output)."
    )
    state = dspy.InputField(desc="Current durable state.")
    history_events_json = dspy.InputField(
        desc="Minimal event stream as a JSON array string (bounded)."
    )
    pinned_findings = dspy.InputField(
        desc="Pinned findings to carry forward (very short)."
    )
    active_rules = dspy.InputField(desc="Concise active rules.")
    phase = dspy.InputField(desc="Current phase.")
    interaction_mode = dspy.InputField(desc="Interaction mode: human or none.")

    action_type = dspy.OutputField(
        desc="One of: shell_command, python_edit, edit, ask_user, stop."
    )
    task = dspy.OutputField(desc="One-sentence next action.")
    success_criteria = dspy.OutputField(desc="What a correct result looks like.")
    command = dspy.OutputField(
        desc="Shell command if action_type == shell_command; Python code if action_type == python_edit; JSON-string if action_type == edit (path: str, old_text: str, new_text: str); otherwise empty. Keep it concise; avoid excessive comments."
    )
    command_intent = dspy.OutputField(
        desc="Semantic intent of command: 'read' (view/search files), 'edit' (modify files), "
        "'verify' (check state/changes), 'execute' (run code/tests), 'navigate' (cd/pwd/ls). "
        "Mandatory if action_type in (shell_command, python_edit, edit)."
    )


class AssignmentComposerSignature(dspy.Signature):
    """Compose a Manager->Actor assignment with minimal context."""

    goal = dspy.InputField(desc="Overall goal / user request.")
    observation = dspy.InputField(
        desc="Newest observation (user input or tool output)."
    )
    state = dspy.InputField(desc="Current durable state.")
    history_events_json = dspy.InputField(
        desc="Minimal event stream as a JSON array string (bounded)."
    )
    pinned_findings = dspy.InputField(
        desc="Pinned findings to carry forward (very short)."
    )
    active_rules = dspy.InputField(desc="Concise active rules.")
    phase = dspy.InputField(desc="Current phase.")
    interaction_mode = dspy.InputField(desc="Interaction mode: human or none.")
    assignment_rules = dspy.InputField(desc="Rules for composing the Assignment.")

    action_type = dspy.OutputField(desc="One of: delegate, stop.")
    task = dspy.OutputField(desc="One-sentence subtask (task-level, no commands).")
    success_criteria = dspy.OutputField(desc="How we know this subtask succeeded.")
    context = dspy.OutputField(
        desc="Minimum context for the actor (evidence anchors only)."
    )
    context_budget = dspy.OutputField(
        desc="Optional context budget hint (e.g., <= 10 lines)."
    )
    constraints = dspy.OutputField(desc="Hard boundaries or constraints (short).")
    degrees_of_freedom = dspy.OutputField(
        desc="Outcome-only freedom | Action-envelope | Action-specified."
    )
    deliverables = dspy.OutputField(desc="What the actor should return (short).")


class OutcomeEvaluatorSignature(dspy.Signature):
    """Summarize execution output into next observation + artifacts."""

    goal = dspy.InputField(desc="Overall goal / user request.")
    task = dspy.InputField(desc="The task that was executed.")
    success_criteria = dspy.InputField(desc="Success criteria for the task.")
    command = dspy.InputField(desc="Shell command that was executed.")
    command_intent = dspy.InputField(
        desc="Semantic intent: read|edit|verify|execute|navigate. "
        "CRITICAL for 'edit': success requires evidence of actual file change, not just exit code 0. "
        "Commands like sed/patch return 0 even when pattern matches nothing!"
    )
    returncode = dspy.InputField(desc="Exit code from the shell command.")
    stdout = dspy.InputField(desc="Stdout from the command (may be truncated).")
    stderr = dspy.InputField(desc="Stderr from the command (may be truncated).")

    observation = dspy.OutputField(
        desc="Next observation (what matters for the next step)."
    )
    artifacts = dspy.OutputField(
        desc="Files/paths likely created/modified/used (best-effort)."
    )
    state_delta = dspy.OutputField(
        desc="New durable facts learned from the outcome (concise bullets)."
    )
    success = dspy.OutputField(
        desc="true/false. For intent='edit': only true if stdout/stderr shows evidence of actual change."
    )
    issues = dspy.OutputField(desc="Errors, blockers, anomalies.")
    needs_verification = dspy.OutputField(
        desc="true if intent='edit' but no change evidence in output; suggests running 'git diff' or 'grep' to confirm."
    )


class EvidencePrefilterSignature(dspy.Signature):
    """
    Compress raw tool output into a small evidence snippet (foundational facts only).

    This is used to avoid bloating the HistoryBuilder input while still providing
    enough detail (error type + excerpt lines) for a later reasoning step to adapt.
    """

    task = dspy.InputField(desc="The task that was executed.")
    command = dspy.InputField(desc="The shell command that was executed.")
    returncode = dspy.InputField(desc="Exit code from the command (string).")
    stdout_truncated = dspy.InputField(desc="Truncated stdout (small).")
    stderr_truncated = dspy.InputField(desc="Truncated stderr (small).")

    evidence_summary = dspy.OutputField(
        desc="1-3 short lines describing what happened (observation only)."
    )
    error_type = dspy.OutputField(
        desc="Error type if present (e.g. SyntaxError, ModuleNotFoundError), else empty."
    )
    error_excerpt = dspy.OutputField(
        desc="1-3 verbatim lines excerpt that best anchor the failure/success (no paraphrase)."
    )


class HistoryBuilderSignature(dspy.Signature):
    """
    Maintain a minimal, generic event stream and pinned findings.

    Goal: observational continuity. Avoid prescribing next actions; capture what happened with
    enough detail (especially failure anchors) so a later reasoning step can adapt.
    """

    goal = dspy.InputField(desc="Overall goal / user request.")
    phase = dspy.InputField(desc="Current phase.")
    interaction_mode = dspy.InputField(desc="Interaction mode: human or none.")

    history_events_json = dspy.InputField(
        desc=(
            "Previous minimal event stream as JSON array string.\n"
            "Schema guidance: a JSON array of event objects with at least: {step:int,type:str}.\n"
            "Event types: request, decision, action, outcome.\n"
            "Include short observational anchors when available (evidence_summary, error_type, error_excerpt, returncode)."
        )
    )
    pinned_findings = dspy.InputField(
        desc="Previous pinned findings (very short bullets). Keep bounded."
    )
    new_step_json = dspy.InputField(
        desc=(
            "JSON object describing the newest step (observation, decision, action, execution, outcome).\n"
            "Important: preserve short observational anchors in history events when present:\n"
            "- execution.evidence_summary\n"
            "- execution.error_type and execution.error_excerpt\n"
            "- outcome.success and outcome.issues"
        )
    )
    max_history_events = dspy.InputField(
        desc="Max number of history events to keep (integer as string)."
    )
    max_pinned_findings = dspy.InputField(
        desc="Max number of pinned findings to keep (integer as string)."
    )

    history_events_updated_json = dspy.OutputField(
        desc="Updated minimal event stream as a JSON array string."
    )
    pinned_findings_updated = dspy.OutputField(
        desc="Updated pinned findings (very short bullets)."
    )
    history_delta_json = dspy.OutputField(
        desc="JSON array string of newly added/changed events (for auditing)."
    )


class StepEvaluatorSignature(dspy.Signature):
    """Summarize command output into the next observation."""

    task = dspy.InputField(desc="The task that was executed.")
    success_criteria = dspy.InputField(desc="Success criteria for the task.")
    command = dspy.InputField(desc="Shell command that was executed.")
    returncode = dspy.InputField(desc="Exit code from the shell command.")
    stdout = dspy.InputField(desc="Stdout from the command (may be truncated).")
    stderr = dspy.InputField(desc="Stderr from the command (may be truncated).")

    observation_summary = dspy.OutputField(
        desc="Concise summary of what the output means for the next step."
    )
    success = dspy.OutputField(desc="true/false")
    issues = dspy.OutputField(desc="Any errors, blockers, or anomalies to note.")


def now_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: Dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def append_jsonl(path: Path, obj: Any) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(obj, ensure_ascii=False) + "\n")


def truncate_text(
    text: str, limit: int = 8000, strategy: str = "head", context_hint: str = ""
) -> str:
    """
    Truncate text with explicit guidance to prevent hallucination.

    Args:
        text: The text to truncate.
        limit: Maximum character limit.
        strategy: "head" (keep beginning) or "tail" (keep end).
        context_hint: Optional hint about what was truncated (e.g., file path).

    Returns:
        Truncated text with clear warning and guidance.
    """
    if len(text) <= limit:
        return text

    total_lines = text.count("\n") + 1
    total_chars = len(text)

    if strategy == "tail":
        truncated = text[-limit:]
        lines_shown = truncated.count("\n") + 1
        warning = (
            f"\n...[TRUNCATED: showing last {lines_shown} of ~{total_lines} lines "
            f"({limit} of {total_chars} chars)]...\n"
        )
        return warning + truncated

    # Head truncation (default)
    truncated = text[:limit]
    lines_shown = truncated.count("\n") + 1

    # Calculate approximate line number where truncation occurred
    approx_cutoff_line = lines_shown

    guidance = ""
    if context_hint:
        guidance = (
            f"\n\n**WARNING: Output truncated at ~line {approx_cutoff_line}. "
            f"You only saw {lines_shown} of ~{total_lines} lines. "
            f"DO NOT assume or guess content beyond this point. "
            f"To see more, use: `sed -n '{approx_cutoff_line + 1},{approx_cutoff_line + 100}p' {context_hint}` "
            f"or read specific line ranges.**"
        )
    else:
        guidance = (
            f"\n\n**WARNING: Output truncated at ~line {approx_cutoff_line}. "
            f"You only saw {lines_shown} of ~{total_lines} lines ({limit} of {total_chars} chars). "
            f"DO NOT assume or guess content beyond this point. "
            f"To see more content, read specific line ranges using `sed -n 'START,ENDp' FILE`.**"
        )

    return truncated + guidance


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_rules_text(path: Path) -> str:
    if not path.exists():
        return ""
    return read_text(path)


def normalize_action_type(value: str) -> str:
    value = (value or "").strip().lower()
    if value in {"shell_command", "command", "shell"}:
        return "shell_command"
    if value in {"python_edit", "eval_python", "python", "python_repl", "repl"}:
        return "python_edit"
    if value in {"edit", "surgical_edit"}:
        return "edit"
    if value in {"ask_user", "ask"}:
        return "ask_user"
    if value in {"stop", "done"}:
        return "stop"
    return value or "shell_command"


def normalize_assignment_action_type(value: str) -> str:
    value = (value or "").strip().lower()
    if value in {"delegate", "assign", "assignment"}:
        return "delegate"
    if value in {"stop", "done", "halt"}:
        return "stop"
    return value or "delegate"


def compile_action(
    action_type: str, command: str, interaction_mode: str
) -> Dict[str, str]:
    normalized = normalize_action_type(action_type)
    command = (command or "").strip()
    rewrite_reason = ""
    if interaction_mode == "none" and normalized == "ask_user":
        normalized = "shell_command"
        rewrite_reason = "ask_user_blocked_no_interaction"
        if not command:
            command = "command -v rg >/dev/null && rg --files || ls"
    return {
        "action_type": normalized,
        "command": command,
        "rewrite_reason": rewrite_reason,
    }


def sanitize_noninteractive_command(command: str) -> tuple[str, str]:
    """
    Best-effort rewrite to avoid interactive / huge-output programs when running
    in interaction_mode=none (stdout/stderr are truncated and no TTY is present).
    """
    original = command
    command = (command or "").strip()
    reasons: list[str] = []

    # Common: `cat file | less`
    stripped_less = re.sub(r"\s*\|\s*less(\s+-\S+)*\s*$", "", command)
    if stripped_less != command:
        command = stripped_less
        reasons.append("sanitized_interactive_command")

    match = re.match(r"^less\s+([^\s]+)\s*$", command)
    if match:
        path = match.group(1)
        command = f"sed -n '1,200p' {path}"
        reasons.append("sanitized_interactive_command")

    # Avoid misleading partial "function snippet" reads that stop at the first `return`.
    # Example: sed -n '/def foo/,/return/p' file.py
    match = re.match(
        r"""^sed\s+-n\s+['"]\/def\s+([A-Za-z_][A-Za-z0-9_]*)[^,]*,\/return\/p['"]\s+(\S+)\s*$""",
        command,
    )
    if match:
        func = match.group(1)
        path = match.group(2)
        command = f"sed -n '/^def {func}/,/^def /p' {path}"
        reasons.append("sanitized_partial_function_read")

    if command != original:
        return command, "+".join(reasons) if reasons else "sanitized_command"
    return command, ""


def extract_file_path_from_command(command: str) -> str:
    """Extract file path from common read commands for better truncation context."""
    command = (command or "").strip()

    patterns = [
        r"^cat\s+([^\s|><]+)",
        r"^less\s+([^\s]+)",
        r"^head\s+(?:-n\s*\d+\s+)?([^\s]+)",
        r"^tail\s+(?:-n\s*\d+\s+)?([^\s]+)",
        r"^sed\s+-n\s+['\"]?\d+,\d+p['\"]?\s+([^\s]+)",
        r"^grep\s+.*\s+([^\s]+)$",
    ]

    for pattern in patterns:
        match = re.match(pattern, command)
        if match:
            return match.group(1)

    return ""


def is_simple_inplace_sed(command: str) -> bool:
    """
    Allow only very simple in-place sed substitutions:
      sed -i '' -e 's/old/new/' file
      sed -i -e 's/old/new/' file
    """
    cmd = (command or "").strip()
    lowered = cmd.lower()
    if "sed" not in lowered or " -i" not in lowered:
        return False
    if "\n" in cmd or "&&" in cmd or "||" in cmd or ";" in cmd:
        return False
    if cmd.count(" -e ") != 1:
        return False
    # Very permissive s/// matcher: we only care that it's a single quoted s/// and one path.
    return bool(
        re.match(
            r"""^sed\s+-i(\s+''|\s+""|\s+)?\s+-e\s+['"]s/.+/.+/[gI]*['"]\s+\S+\s*$""",
            cmd,
        )
    )


_PY_FROM_IMPORT_RE = re.compile(r"(?m)^\s*from\s+([A-Za-z_][A-Za-z0-9_]*)\s+import\b")
_PY_IMPORT_RE = re.compile(r"(?m)^\s*import\s+([A-Za-z_][A-Za-z0-9_]*)\b")


def extract_python_modules(text: str, max_modules: int = 5) -> list[str]:
    text = text or ""
    modules: list[str] = []
    seen: set[str] = set()
    for match in _PY_FROM_IMPORT_RE.findall(text):
        if match not in seen:
            seen.add(match)
            modules.append(match)
    for match in _PY_IMPORT_RE.findall(text):
        if match not in seen:
            seen.add(match)
            modules.append(match)
    return modules[:max_modules]


def instruction_needs_exploration(text: str) -> bool:
    lowered = (text or "").lower()
    if "http://" in lowered or "https://" in lowered:
        return True
    if "github.com" in lowered:
        return True
    if "traceback" in lowered or "syntaxerror" in lowered:
        return True
    if "test" in lowered or "pytest" in lowered:
        return True
    if "/" in (text or "") and ".py" in lowered:
        return True
    if "bug" in lowered or "broken" in lowered:
        return True
    return False


def build_environment_probe_command(goal: str, observation: str) -> str:
    """
    A small, safe probe that helps the agent orient itself in an unknown workspace.
    Keep output bounded; prefer read-only commands.
    """
    combined = f"{goal}\n{observation}"
    modules = extract_python_modules(combined)
    parts: list[str] = [
        "echo 'ENV_PROBE_BEGIN'",
        "pwd",
        "ls -la",
        "(git rev-parse --show-toplevel >/dev/null 2>&1 && git status -sb 2>/dev/null | head -n 50) || echo 'no_git_repo'",
        "command -v python >/dev/null 2>&1 && python -V || true",
        "command -v rg >/dev/null 2>&1 && (rg --files | head -n 50) || (find . -maxdepth 2 -type f | head -n 50)",
    ]
    if modules:
        for mod in modules:
            parts.append(
                "python -c "
                f"\"import importlib; m=importlib.import_module('{mod}'); "
                f"print('module:{mod} file:'+str(getattr(m,'__file__','')))\" 2>&1 || true"
            )
    parts.append("echo 'ENV_PROBE_END'")
    return "; ".join(parts)


def command_is_destructive(command: str) -> bool:
    command = (command or "").strip().lower()
    destructive_markers = [
        "rm -rf",
        "rm -r",
        "git reset --hard",
        "git clean -fd",
        "git clean -ff",
        "sudo ",
        "curl ",
        "wget ",
        "| bash",
        "| sh",
    ]
    return any(marker in command for marker in destructive_markers)


def command_is_clone(command: str) -> bool:
    return "git clone" in (command or "").strip().lower()


def goal_explicitly_allows_clone(goal: str) -> bool:
    lowered = (goal or "").lower()
    if "git clone" in lowered:
        return True
    if "clone" in lowered and ("repo" in lowered or "repository" in lowered):
        return True
    return False


def goal_explicitly_allows_delete(goal: str) -> bool:
    lowered = (goal or "").lower()
    return any(
        word in lowered
        for word in ["rm -rf", "delete ", "remove ", "clean up", "wipe "]
    )


def apply_command_policy(
    *,
    step: int,
    goal: str,
    observation: str,
    history_events_json: str,
    proposed_task: str,
    proposed_success_criteria: str,
    action_type: str,
    command: str,
    command_intent: str,
) -> dict:
    """
    Enforce a small set of generic safety + orientation policies:
    - Prefer an initial exploration step for repo-like tasks.
    - Block destructive workspace resets (e.g. rm -rf) unless explicitly requested.
    - Block `git clone` unless explicitly requested; prefer inspecting local workspace first.
    """
    rewrites: list[dict] = []
    task = proposed_task
    success_criteria = proposed_success_criteria
    action_type = (action_type or "").strip()
    command_intent = (command_intent or "").strip().lower()

    history_is_empty = len(safe_parse_history_events(history_events_json)) == 0
    force_probe = step == 1 and history_is_empty and instruction_needs_exploration(goal)
    if force_probe and action_type == "shell_command":
        probe_cmd = build_environment_probe_command(goal, observation)
        if (command or "").strip() != probe_cmd.strip():
            rewrites.append(
                {
                    "kind": "forced_environment_probe",
                    "reason": "task looks repo-like; orient in workspace before modifying or fetching anything",
                    "original_command": command,
                    "final_command": probe_cmd,
                }
            )
            command = probe_cmd
            task = "Probe the environment (pwd/ls/git status; locate relevant modules/files) to orient before making changes."
            success_criteria = "Workspace layout and any relevant module file locations are identified (paths printed)."

    if (
        action_type == "shell_command"
        and command_is_clone(command)
        and not goal_explicitly_allows_clone(goal)
    ):
        probe_cmd = build_environment_probe_command(goal, observation)
        rewrites.append(
            {
                "kind": "blocked_git_clone",
                "reason": "git clone is disallowed unless explicitly requested; inspect existing workspace first",
                "original_command": command,
                "final_command": probe_cmd,
            }
        )
        command = probe_cmd
        task = task or "Inspect existing workspace; avoid cloning until necessary."
        success_criteria = (
            success_criteria or "Relevant files are found locally without cloning."
        )

    if (
        action_type == "shell_command"
        and command_is_destructive(command)
        and not goal_explicitly_allows_delete(goal)
    ):
        probe_cmd = build_environment_probe_command(goal, observation)
        rewrites.append(
            {
                "kind": "blocked_destructive_command",
                "reason": "destructive commands are blocked unless explicitly requested; gather evidence and adapt safely",
                "original_command": command,
                "final_command": probe_cmd,
            }
        )
        command = probe_cmd
        task = task or "Gather evidence safely; avoid destructive operations."
        success_criteria = (
            success_criteria or "Evidence collected to choose a safe next step."
        )

    def _is_old_text_not_found_error(event: dict) -> bool:
        if not isinstance(event, dict):
            return False
        if str(event.get("action_type", "")).strip().lower() != "edit":
            return False
        if event.get("ok") is not False:
            return False
        excerpt = str(event.get("error_excerpt", "") or "").lower()
        return "old_text not found" in excerpt or "could not find old_text" in excerpt

    def _extract_locate_tokens(old_text: str, max_tokens: int = 3) -> list[str]:
        raw = old_text or ""
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", raw)
        stop = {
            "if",
            "elif",
            "else",
            "for",
            "while",
            "return",
            "import",
            "from",
            "def",
            "class",
            "try",
            "except",
            "with",
            "as",
            "and",
            "or",
            "not",
            "isinstance",
            "self",
            "true",
            "false",
            "none",
        }
        unique: list[str] = []
        for tok in tokens:
            if tok.lower() in stop:
                continue
            if tok not in unique:
                unique.append(tok)

        def score(tok: str) -> tuple[int, int]:
            bonus = 0
            if tok.startswith("_"):
                bonus += 3
            if "_" in tok:
                bonus += 2
            if any(c.isupper() for c in tok):
                bonus += 1
            return (bonus, len(tok))

        unique.sort(key=score, reverse=True)
        return unique[:max_tokens]

    def _build_locate_command(edit_command: str) -> str:
        try:
            payload = json.loads(edit_command)
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        path = str(payload.get("path", "") or "").strip()
        old_text = str(payload.get("old_text", "") or "")
        tokens = _extract_locate_tokens(old_text, max_tokens=3)
        query = "|".join(tokens) if tokens else ""
        if not query:
            first_line = next(
                (line.strip() for line in old_text.splitlines() if line.strip()), ""
            )
            query = first_line[:80] if first_line else "TODO"
        quoted_path = shlex.quote(path) if path else "."
        quoted_query = shlex.quote(query)
        return f"rg -n {quoted_query} {quoted_path}"

    history = safe_parse_history_events(history_events_json)
    recent = history[-8:] if isinstance(history, list) else []
    old_text_not_found_failures = sum(
        1 for evt in recent if isinstance(evt, dict) and _is_old_text_not_found_error(evt)
    )
    last_evt = recent[-1] if recent else {}
    if (
        str(action_type).strip().lower() == "edit"
        and command_intent == "edit"
        and old_text_not_found_failures >= 2
        and _is_old_text_not_found_error(last_evt if isinstance(last_evt, dict) else {})
    ):
        locate_cmd = _build_locate_command(command)
        rewrites.append(
            {
                "kind": "loop_breaker_locate_old_text",
                "reason": "recent edits failed due to old_text mismatch; locate the exact region before editing again",
                "original_action_type": action_type,
                "final_action_type": "shell_command",
                "final_command": locate_cmd,
            }
        )
        action_type = "shell_command"
        command_intent = "read"
        command = locate_cmd
        task = task or "Locate the intended edit region (old_text was not found)."
        success_criteria = (
            success_criteria
            or "Locate matching code with line numbers so the next edit can be applied precisely."
        )

    return {
        "task": task,
        "success_criteria": success_criteria,
        "action_type": action_type,
        "command_intent": command_intent,
        "command": command,
        "rewrites": rewrites,
    }


def find_latest_snapshot_workspace(snapshot_root: Path) -> Optional[Path]:
    if not snapshot_root.exists():
        return None
    candidates = [
        path
        for path in snapshot_root.iterdir()
        if path.is_dir() and (path / "workspace").is_dir()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.name, reverse=True)
    return candidates[0] / "workspace"


def looks_like_write_command(command: str) -> bool:
    lowered = command.lower()
    write_markers = [
        "apply_patch",
        "sed -i",
        "perl -pi",
        ">>",
        "> ",
        "tee ",
        "rm ",
        "mv ",
        "cp ",
        "git add",
        "git commit",
        "chmod ",
        "chown ",
        "mkdir ",
        "touch ",
    ]
    return any(marker in lowered for marker in write_markers)


def perform_surgical_edit(path: Path, old_text: str, new_text: str) -> dict:
    if not path.exists():
        return {"success": False, "error": f"File not found: {path}"}

    content = path.read_text(encoding="utf-8")

    if content.count(old_text) == 1:
        new_content = content.replace(old_text, new_text)
        path.write_text(new_content, encoding="utf-8")
        return {"success": True, "method": "exact_match"}

    return {
        "success": False,
        "error": (
            "Could not find old_text in file. "
            "Ensure old_text matches the file content exactly."
        ),
    }

    content = path.read_text(encoding="utf-8")
    total_lines = content.count("\n") + 1

    if content.count(old_text) == 1:
        new_content = content.replace(old_text, new_text)
        path.write_text(new_content, encoding="utf-8")
        return {"success": True, "method": "exact_match"}

    def normalize(t: str) -> str:
        return "\n".join(line.strip() for line in t.splitlines())

    norm_content = normalize(content)
    norm_old = normalize(old_text)

    if norm_content.count(norm_old) == 1:
        start_idx = norm_content.find(norm_old)

        lines = content.splitlines(keepends=True)
        norm_lines = [line.strip() for line in lines]

        original_start_idx = -1
        original_end_idx = -1

        target_norm_start = start_idx
        target_norm_end = start_idx + len(norm_old)

        cumulative_norm_len = 0
        for i, norm_line in enumerate(norm_lines):
            line_norm_start = cumulative_norm_len
            line_norm_end = cumulative_norm_len + len(norm_line)

            if original_start_idx == -1 and line_norm_end > target_norm_start:
                original_start_idx = i

            if original_start_idx != -1 and line_norm_start < target_norm_end:
                original_end_idx = i

            cumulative_norm_len += len(norm_line) + 1

        if original_start_idx != -1 and original_end_idx != -1:
            new_lines = lines[:original_start_idx] + [new_text]
            if not new_text.endswith("\n") and original_end_idx < len(lines) - 1:
                new_lines[-1] += "\n"
            new_lines += lines[original_end_idx + 1 :]

            path.write_text("".join(new_lines), encoding="utf-8")
            return {"success": True, "method": "normalized_match"}

    if content.count(old_text) > 1:
        return {
            "success": False,
            "error": "Ambiguous edit: old_text appears multiple times.",
        }

    old_text_preview = old_text[:100].replace("\n", "\\n")
    if len(old_text) > 100:
        old_text_preview += "..."

    return {
        "success": False,
        "error": (
            f"old_text not found in {path.name} ({total_lines} lines). "
            f"Searched for: '{old_text_preview}'. "
            f"LIKELY CAUSE: You may have only seen truncated file content. "
            f"ACTION REQUIRED: Re-read the file with `sed -n 'START,ENDp' {path}` "
            f"to see the exact lines you want to edit. DO NOT guess code structure."
        ),
    }


def build_action_pack(reasoner_out: Dict) -> str:
    def line(key: str) -> str:
        value = (reasoner_out.get(key, "") or "").strip()
        return f"{key}: {value}" if value else f"{key}:"

    ordered_keys = [
        "ASSIGNMENT_ID",
        "GOAL",
        "STATE",
        "HISTORY_EVENTS",
        "PINNED_FINDINGS",
        "ACTIVE_RULES",
        "OPEN_QUESTIONS",
        "ASSUMPTIONS",
        "OBSERVATION",
        "PLAN_HINT",
        "DECISION",
        "TASK",
        "SUCCESS_CRITERIA",
        "ARTIFACTS",
        "STATE_DELTA",
    ]
    return "\n".join(line(k) for k in ordered_keys).rstrip() + "\n"


def build_assignment_pack(reasoner_out: Dict) -> str:
    def line(key: str) -> str:
        value = (reasoner_out.get(key, "") or "").strip()
        return f"{key}: {value}" if value else f"{key}:"

    ordered_keys = [
        "GOAL",
        "STATE",
        "HISTORY_EVENTS",
        "PINNED_FINDINGS",
        "ACTIVE_RULES",
        "OPEN_QUESTIONS",
        "ASSUMPTIONS",
        "OBSERVATION",
        "PLAN_HINT",
        "DECISION",
        "TASK",
        "SUCCESS_CRITERIA",
        "CONTEXT",
        "CONTEXT_BUDGET",
        "CONSTRAINTS",
        "DEGREES_OF_FREEDOM",
        "DELIVERABLES",
        "STATE_DELTA",
    ]
    return "\n".join(line(k) for k in ordered_keys).rstrip() + "\n"


def merge_bullets(existing: str, delta: str) -> str:
    existing = (existing or "").strip()
    delta = (delta or "").strip()
    if not existing:
        return delta
    if not delta:
        return existing
    return existing + "\n" + delta


def safe_parse_history_events(text: str) -> list:
    text = (text or "").strip()
    if not text:
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return value


def safe_dump_history_events(events: list) -> str:
    return json.dumps(events, indent=2, sort_keys=True)


def maybe_limit_history_events(events: list, max_events: int) -> list:
    if max_events <= 0:
        return events
    # Keep the most recent events (tail), not the oldest.
    return events[-max_events:]


def _collapse_ws_one_line(text: str, *, limit: int) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip())
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3] + "..."


def _short_command_head(command: str, *, limit: int = 120) -> str:
    return _collapse_ws_one_line(command, limit=limit)


def _short_id_for_command(command: str) -> str:
    # Stable, short identifier for offline analysis without storing full commands in history.
    digest = hashlib.sha256((command or "").encode("utf-8")).hexdigest()
    return digest[:12]


def _extract_artifact_paths(text: str, *, max_items: int = 3) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    candidates: list[str] = []
    for line in raw.splitlines():
        for chunk in line.split(","):
            value = chunk.strip()
            if not value:
                continue
            candidates.append(value)
    out: list[str] = []
    for item in candidates:
        if item not in out:
            out.append(item)
        if len(out) >= max_items:
            break
    return out


def build_compact_history_event(
    *,
    step: int,
    action_type: str,
    command_intent: str,
    task: str,
    command: str,
    returncode: str,
    outcome_data: Dict[str, Any],
    prefilter_output: Dict[str, str],
) -> Dict[str, Any]:
    ok = str(outcome_data.get("success", "")).strip().lower() == "true"
    verification_required = (
        str(outcome_data.get("needs_verification", "")).strip().lower() == "true"
    )
    files = _extract_artifact_paths(str(outcome_data.get("artifacts", "") or ""))
    event: Dict[str, Any] = {
        "step": step,
        "action_type": (action_type or "").strip(),
        "intent": (command_intent or "").strip(),
        "task": _collapse_ws_one_line(task, limit=160),
        "ok": ok,
        "returncode": str(returncode or "").strip(),
    }
    if command:
        event["command_id"] = _short_id_for_command(command)
        event["command_head"] = _short_command_head(command, limit=120)
    if files:
        event["files"] = files
    if verification_required:
        event["verification_required"] = True
    evidence_summary = (prefilter_output.get("evidence_summary", "") or "").strip()
    if evidence_summary:
        event["evidence_summary"] = _collapse_ws_one_line(evidence_summary, limit=220)
    error_type = (prefilter_output.get("error_type", "") or "").strip()
    error_excerpt = (prefilter_output.get("error_excerpt", "") or "").strip()
    if error_type:
        event["error_type"] = _collapse_ws_one_line(error_type, limit=80)
    if error_excerpt:
        event["error_excerpt"] = _collapse_ws_one_line(error_excerpt, limit=220)
    return event


def _prepend_bullets(existing: str, delta: str) -> str:
    existing = (existing or "").strip()
    delta = (delta or "").strip()
    if not delta:
        return existing
    if not existing:
        return delta
    return delta + "\n" + existing


def update_pinned_findings_from_event(
    pinned_findings: str,
    *,
    event: Dict[str, Any],
    max_lines: int,
) -> str:
    # Keep this conservative and self-explanatory. Prefer the most recent anchors.
    bullets: list[str] = []
    files = event.get("files") or []
    if isinstance(files, list) and files:
        bullets.append(f"- Files: {', '.join(str(x) for x in files[:3])}")
    if event.get("verification_required") is True:
        bullets.append(
            "- Verification required: confirm edit via `git diff` or re-read."
        )
    if event.get("ok") is False and event.get("error_type"):
        excerpt = str(event.get("error_excerpt", "") or "").strip()
        if excerpt:
            bullets.append(f"- Last error: {event.get('error_type')}: {excerpt}")
        else:
            bullets.append(f"- Last error: {event.get('error_type')}")
    delta = "\n".join(bullets).strip()
    updated = _prepend_bullets(pinned_findings, delta)
    return maybe_limit_bullets(updated, max_lines)


def maybe_limit_bullets(text: str, max_lines: int) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if max_lines <= 0:
        return text
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines[:max_lines]).strip()


def render_context_for_bash(pack: Dict[str, str]) -> str:
    parts = []
    for key in [
        "GOAL",
        "STATE",
        "ACTIVE_RULES",
        "ASSUMPTIONS",
        "OPEN_QUESTIONS",
        "OBSERVATION",
    ]:
        value = (pack.get(key, "") or "").strip()
        if value:
            parts.append(f"{key}:\n{value}")
    return "\n\n".join(parts).strip()


def run_bash_step(
    step_dir: Path, env: Dict[str, str], workspace_dir: Optional[Path]
) -> None:
    script_path = Path(__file__).resolve().parent / "scripts" / "execute_task.sh"
    subprocess.run(
        ["bash", str(script_path)],
        check=False,
        env=env,
        cwd=workspace_dir if workspace_dir else Path.cwd(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Online replay loop (DSPy + bash).")
    parser.add_argument("--user-request", required=True, help="Initial user request.")
    parser.add_argument("--run-dir", default="", help="Run directory (optional).")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    parser.add_argument("--model", default="gemini-3-flash-preview")
    parser.add_argument("--phase", default=DEFAULT_PHASE)
    parser.add_argument("--interaction-mode", default=DEFAULT_INTERACTION_MODE)
    parser.add_argument(
        "--actor",
        default=DEFAULT_ACTOR_MODE,
        choices=["bash", "opencode-acp"],
        help="Execution mode: bash (local) or opencode-acp (Manager->Actor).",
    )
    parser.add_argument(
        "--rules-path",
        default=None,
        help="Rules file path. If not provided, auto-selected based on --phase.",
    )
    parser.add_argument(
        "--max-history-events",
        type=int,
        default=DEFAULT_MAX_HISTORY_EVENTS,
        help="Maximum number of history events to retain (0 = unbounded).",
    )
    parser.add_argument(
        "--max-pinned-findings",
        type=int,
        default=DEFAULT_MAX_PINNED_FINDINGS,
        help="Maximum number of pinned finding lines to retain (0 = unbounded).",
    )
    parser.add_argument(
        "--workspace-dir", default="", help="Workspace directory for bash execution."
    )
    parser.add_argument(
        "--snapshot-root",
        default="data/snapshots/elix-live-chat",
        help="Snapshot root used to auto-detect the latest workspace.",
    )
    parser.add_argument(
        "--no-snapshot-autodetect",
        action="store_true",
        help="Disable auto-detecting the latest snapshot workspace.",
    )
    parser.add_argument(
        "--opencode-bin", default="opencode", help="Path to the opencode executable."
    )
    parser.add_argument(
        "--opencode-log-level", default="INFO", help="OpenCode log level."
    )
    parser.add_argument(
        "--opencode-timeout-s",
        type=float,
        default=600.0,
        help="OpenCode prompt timeout.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Auto-select rules file based on phase if not explicitly provided
    if args.rules_path is None:
        args.rules_path = PHASE_TO_RULES.get(args.phase, DEFAULT_RULES_PATH)

    run_id = now_stamp()
    run_dir = (
        Path(args.run_dir) if args.run_dir else Path("data/online_replay") / run_id
    )
    steps_dir = run_dir / "steps"
    ensure_dir(steps_dir)

    workspace_dir = Path(args.workspace_dir) if args.workspace_dir else None
    if not workspace_dir and not args.no_snapshot_autodetect:
        workspace_dir = find_latest_snapshot_workspace(Path(args.snapshot_root))
    if workspace_dir:
        if not workspace_dir.exists():
            raise FileNotFoundError(f"Workspace not found: {workspace_dir}")
        workspace_dir = workspace_dir.resolve()

    config = RunConfig(
        run_id=run_id,
        run_dir=str(run_dir),
        model=args.model,
        phase=args.phase,
        interaction_mode=args.interaction_mode,
        actor_mode=args.actor,
        max_steps=args.max_steps,
        rules_path=args.rules_path,
        max_history_events=args.max_history_events,
        max_pinned_findings=args.max_pinned_findings,
        started_at=datetime.utcnow().isoformat() + "Z",
        user_request=args.user_request,
        workspace_dir=str(workspace_dir) if workspace_dir else "",
        snapshot_root=args.snapshot_root,
        snapshot_autodetect=not args.no_snapshot_autodetect,
        opencode_bin=args.opencode_bin,
        opencode_log_level=args.opencode_log_level,
        opencode_timeout_s=args.opencode_timeout_s,
    )
    write_json(run_dir / "run_config.json", asdict(config))

    lm = DSPyGeminiConfig.create_lm(
        model_name=args.model, temperature=0.2, reasoning_effort="disable"
    )
    adapter = dspy.JSONAdapter()

    consolidator = dspy.Predict(KnowledgeConsolidatorSignature)
    decision_planner = dspy.Predict(DecisionPlannerSignature)
    action_selector = dspy.Predict(ActionSelectorSignature)
    assignment_composer = dspy.Predict(AssignmentComposerSignature)
    outcome_evaluator = dspy.Predict(OutcomeEvaluatorSignature)
    evidence_prefilter = dspy.Predict(EvidencePrefilterSignature)
    # History is maintained deterministically as a compact, bounded event stream.

    rules_text = load_rules_text(Path(args.rules_path))
    state = ""
    observation = args.user_request
    history_events_json = "[]"
    pinned_findings = ""

    trace_path = run_dir / "trace.md"
    write_text(
        trace_path,
        "# Online replay trace\n\n"
        f"Run ID: {run_id}\n"
        f"Model: {args.model}\n"
        f"Phase: {args.phase}\n"
        f"Interaction mode: {args.interaction_mode}\n"
        f"Actor: {args.actor}\n"
        f"Workspace: {workspace_dir or 'cwd'}\n"
        f"Started: {config.started_at}\n\n",
    )

    opencode_backend = None
    if args.actor == "opencode-acp":
        backend_workspace = workspace_dir or Path.cwd()
        opencode_backend = OpencodeACPBackend(
            workspace_dir=backend_workspace,
            opencode_bin=args.opencode_bin,
            log_level=args.opencode_log_level,
            prompt_timeout_s=args.opencode_timeout_s,
        )

    # Reset Python executor for fresh state at run start
    reset_executor()

    # Token usage tracking (best-effort; requires track_usage=True in dspy.context)
    total_input_tokens = 0
    total_output_tokens = 0

    # Append-only run-level logs for offline analysis (full raw dicts, no extraction).
    lm_usage_events_path = run_dir / "lm_usage_events.jsonl"
    token_usage_steps_path = run_dir / "token_usage_steps.jsonl"

    def safe_get_lm_usage(prediction_result: Any) -> dict[str, Any]:
        """
        Return the raw LM usage dict for this prediction, or {}.

        Example shape:
          {"vertex_ai/gemini-2.5-flash": {"prompt_tokens": 123, "completion_tokens": 45, ...}}
        """
        try:
            usage = prediction_result.get_lm_usage()  # type: ignore[attr-defined]
            return usage if isinstance(usage, dict) else {}
        except Exception:
            return {}

    def record_lm_usage(
        step: int,
        step_dir: Path,
        call_name: str,
        prediction_result: Any,
        timing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Persist the full raw usage dict "as-is" (best-effort) both:
        - per-step file: steps/step_XXX/lm_usage_{call_name}.json
        - run-level append-only log: lm_usage_events.jsonl (includes timing)
        """
        usage = safe_get_lm_usage(prediction_result)
        write_json(step_dir / f"lm_usage_{call_name}.json", usage)
        append_jsonl(
            lm_usage_events_path,
            {
                "step": step,
                "call": call_name,
                "timing": timing or {},
                "usage": usage,
            },
        )
        return usage

    def extract_token_usage(prediction_result: Any) -> tuple[int, int]:
        """
        Extract token counts from a dspy.Prediction-like object.

        DSPy usage is typically a dict of model -> {"prompt_tokens": int, "completion_tokens": int, ...}.
        """
        try:
            usage = safe_get_lm_usage(prediction_result)
            if not usage:
                return 0, 0
            in_tokens = 0
            out_tokens = 0
            if isinstance(usage, dict):
                for model_usage in usage.values():
                    if isinstance(model_usage, dict):
                        in_tokens += int(model_usage.get("prompt_tokens", 0) or 0)
                        out_tokens += int(model_usage.get("completion_tokens", 0) or 0)
            return in_tokens, out_tokens
        except Exception:
            return 0, 0

    def robust_predict(predictor, **kwargs) -> tuple[Any, dict[str, Any]]:
        max_retries = 2
        started_at = datetime.now(timezone.utc).isoformat()
        start_perf = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                with dspy.context(lm=lm, adapter=adapter, track_usage=True):
                    prediction = predictor(**kwargs)
                ended_at = datetime.now(timezone.utc).isoformat()
                duration_ms = int((time.perf_counter() - start_perf) * 1000)
                timing: dict[str, Any] = {
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "duration_ms": duration_ms,
                    "attempts": attempt + 1,
                }
                if last_error is not None:
                    timing["last_error"] = str(last_error)
                return prediction, timing
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    if "observation" in kwargs:
                        kwargs["observation"] = (
                            str(kwargs["observation"])
                            + f"\n\n[SYSTEM WARNING: Previous attempt failed to parse due to: {e}. "
                            "Please ensure all required fields are present and the response is concise.]"
                        )
                    continue
                raise e

    def persist_token_usage(step: int | None = None) -> None:
        payload = {
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
        }
        write_json(run_dir / "token_usage.json", payload)

    for step in range(1, args.max_steps + 1):
        step_dir = steps_dir / f"step_{step:03d}"
        ensure_dir(step_dir)

        step_input_tokens = 0
        step_output_tokens = 0

        write_text(step_dir / "observation_input.txt", observation)

        consolidator_input = {
            "goal": args.user_request,
            "observation": observation,
            "state": state,
            "history_events_json": history_events_json,
            "pinned_findings": pinned_findings,
            "rules": rules_text
            + "\n- CRITICAL: Avoid repeating context from the observation in your output. Be concise.",
            "phase": args.phase,
            "interaction_mode": args.interaction_mode,
        }
        write_json(step_dir / "consolidator_input.json", consolidator_input)
        consolidator_out, consolidator_timing = robust_predict(
            consolidator, **consolidator_input
        )
        record_lm_usage(
            step, step_dir, "consolidator", consolidator_out, consolidator_timing
        )
        in_tokens, out_tokens = extract_token_usage(consolidator_out)
        total_input_tokens += in_tokens
        total_output_tokens += out_tokens
        step_input_tokens += in_tokens
        step_output_tokens += out_tokens
        consolidated = {
            "STATE_DELTA": getattr(consolidator_out, "state_delta", ""),
            "STATE": getattr(consolidator_out, "state_updated", ""),
            "ACTIVE_RULES": getattr(consolidator_out, "active_rules", ""),
            "OPEN_QUESTIONS": getattr(consolidator_out, "open_questions", ""),
            "ASSUMPTIONS": getattr(consolidator_out, "assumptions", ""),
        }
        # Hard-cap chattiness in LM-visible carry-forward fields.
        consolidated["STATE_DELTA"] = maybe_limit_bullets(
            consolidated["STATE_DELTA"], MAX_STATE_DELTA_LINES
        )
        consolidated["STATE"] = maybe_limit_bullets(
            consolidated["STATE"], MAX_STATE_LINES
        )
        consolidated["ACTIVE_RULES"] = maybe_limit_bullets(
            consolidated["ACTIVE_RULES"], MAX_ACTIVE_RULE_LINES
        )
        consolidated["OPEN_QUESTIONS"] = maybe_limit_bullets(
            consolidated["OPEN_QUESTIONS"], MAX_OPEN_QUESTIONS_LINES
        )
        consolidated["ASSUMPTIONS"] = maybe_limit_bullets(
            consolidated["ASSUMPTIONS"], MAX_ASSUMPTIONS_LINES
        )
        write_json(step_dir / "consolidator_output.json", consolidated)
        write_text(step_dir / "state_delta.txt", consolidated["STATE_DELTA"].strip())
        write_text(
            step_dir / "open_questions.txt", consolidated["OPEN_QUESTIONS"].strip()
        )
        write_text(step_dir / "assumptions.txt", consolidated["ASSUMPTIONS"].strip())

        state = consolidated.get("STATE", state).strip()

        def run_decision_planner() -> tuple[Dict[str, str], Any, dict[str, Any]]:
            out, timing = robust_predict(
                decision_planner,
                goal=args.user_request,
                observation=observation,
                state=state,
                history_events_json=history_events_json,
                pinned_findings=pinned_findings,
                active_rules=consolidated["ACTIVE_RULES"],
                phase=args.phase,
                interaction_mode=args.interaction_mode,
            )
            return {
                "PLAN_HINT": getattr(out, "plan_hint", ""),
                "DECISION": getattr(out, "decision", ""),
            }, out, timing

        def run_action_selector() -> tuple[Dict[str, str], Any, dict[str, Any]]:
            out, timing = robust_predict(
                action_selector,
                goal=args.user_request,
                observation=observation,
                state=state,
                history_events_json=history_events_json,
                pinned_findings=pinned_findings,
                active_rules=consolidated["ACTIVE_RULES"],
                phase=args.phase,
                interaction_mode=args.interaction_mode,
            )
            return {
                "action_type": getattr(out, "action_type", ""),
                "TASK": getattr(out, "task", ""),
                "SUCCESS_CRITERIA": getattr(out, "success_criteria", ""),
                "command": getattr(out, "command", ""),
                "command_intent": getattr(out, "command_intent", ""),
            }, out, timing

        def run_action_selector_with_observation(
            obs: str,
        ) -> tuple[Dict[str, str], Any, dict[str, Any]]:
            out, timing = robust_predict(
                action_selector,
                goal=args.user_request,
                observation=obs,
                state=state,
                history_events_json=history_events_json,
                pinned_findings=pinned_findings,
                active_rules=consolidated["ACTIVE_RULES"],
                phase=args.phase,
                interaction_mode=args.interaction_mode,
            )
            return {
                "action_type": getattr(out, "action_type", ""),
                "TASK": getattr(out, "task", ""),
                "SUCCESS_CRITERIA": getattr(out, "success_criteria", ""),
                "command": getattr(out, "command", ""),
                "command_intent": getattr(out, "command_intent", ""),
            }, out, timing

        def run_assignment_composer() -> tuple[Dict[str, str], Any, dict[str, Any]]:
            out, timing = robust_predict(
                assignment_composer,
                goal=args.user_request,
                observation=observation,
                state=state,
                history_events_json=history_events_json,
                pinned_findings=pinned_findings,
                active_rules=consolidated["ACTIVE_RULES"],
                phase=args.phase,
                interaction_mode=args.interaction_mode,
                assignment_rules=ASSIGNMENT_RULES,
            )
            return {
                "action_type": getattr(out, "action_type", ""),
                "TASK": getattr(out, "task", ""),
                "SUCCESS_CRITERIA": getattr(out, "success_criteria", ""),
                "CONTEXT": getattr(out, "context", ""),
                "CONTEXT_BUDGET": getattr(out, "context_budget", ""),
                "CONSTRAINTS": getattr(out, "constraints", ""),
                "DEGREES_OF_FREEDOM": getattr(out, "degrees_of_freedom", ""),
                "DELIVERABLES": getattr(out, "deliverables", ""),
            }, out, timing

        with ThreadPoolExecutor(max_workers=3) as pool:
            future_decision = pool.submit(run_decision_planner)
            future_assignment = pool.submit(run_assignment_composer)
            future_action = (
                pool.submit(run_action_selector)
                if args.actor != "opencode-acp"
                else None
            )
            composed = {}
            decision_dict, decision_out, decision_timing = future_decision.result()
            assignment_dict, assignment_out, assignment_timing = (
                future_assignment.result()
            )
            action_dict: dict = {}
            action_out = None
            action_timing: dict[str, Any] | None = None
            if future_action is not None:
                action_dict, action_out, action_timing = future_action.result()
                composed.update(action_dict)
            composed.update(decision_dict)
            composed.update(assignment_dict)
            if future_action is not None:
                composed["action_type"] = action_dict.get("action_type", "")
                composed["command"] = action_dict.get("command", "")
                composed["command_intent"] = action_dict.get("command_intent", "")

            record_lm_usage(
                step, step_dir, "decision_planner", decision_out, decision_timing
            )
            in_tokens, out_tokens = extract_token_usage(decision_out)
            total_input_tokens += in_tokens
            total_output_tokens += out_tokens
            step_input_tokens += in_tokens
            step_output_tokens += out_tokens

            record_lm_usage(
                step,
                step_dir,
                "assignment_composer",
                assignment_out,
                assignment_timing,
            )
            in_tokens, out_tokens = extract_token_usage(assignment_out)
            total_input_tokens += in_tokens
            total_output_tokens += out_tokens
            step_input_tokens += in_tokens
            step_output_tokens += out_tokens

            if future_action is not None and action_out is not None:
                record_lm_usage(
                    step, step_dir, "action_selector", action_out, action_timing
                )
                in_tokens, out_tokens = extract_token_usage(action_out)
                total_input_tokens += in_tokens
                total_output_tokens += out_tokens
                step_input_tokens += in_tokens
                step_output_tokens += out_tokens

        write_json(
            step_dir / "decision_planner_output.json",
            {k: composed[k] for k in ["PLAN_HINT", "DECISION"]},
        )
        if args.actor == "opencode-acp":
            write_json(
                step_dir / "assignment_composer_output.json",
                {
                    k: composed[k]
                    for k in [
                        "action_type",
                        "TASK",
                        "SUCCESS_CRITERIA",
                        "CONTEXT",
                        "CONTEXT_BUDGET",
                        "CONSTRAINTS",
                        "DEGREES_OF_FREEDOM",
                        "DELIVERABLES",
                    ]
                },
            )
        else:
            write_json(
                step_dir / "action_selector_output.json",
                {
                    k: composed[k]
                    for k in [
                        "action_type",
                        "TASK",
                        "SUCCESS_CRITERIA",
                        "command",
                        "command_intent",
                    ]
                },
            )

        if args.actor == "opencode-acp":
            observation_input = observation
            assignment_id = f"A-{step:03d}"
            action_type = normalize_assignment_action_type(
                composed.get("action_type", "")
            )
            task = (composed.get("TASK", "") or "").strip()
            success_criteria = (composed.get("SUCCESS_CRITERIA", "") or "").strip()
            context = (composed.get("CONTEXT", "") or "").strip()
            context_budget = (composed.get("CONTEXT_BUDGET", "") or "").strip()
            constraints = (composed.get("CONSTRAINTS", "") or "").strip()
            degrees_of_freedom = (composed.get("DEGREES_OF_FREEDOM", "") or "").strip()
            if not degrees_of_freedom:
                degrees_of_freedom = DEFAULT_DEGREES_OF_FREEDOM
            deliverables = (composed.get("DELIVERABLES", "") or "").strip()
            if not deliverables:
                deliverables = (
                    "Result headers: RESULT, EVIDENCE, STATE_DELTA; "
                    "optional CHANGES, ARTIFACTS, RISKS, OPEN_QUESTIONS, NEXT_OPTIONS."
                )

            assignment_pack_data = {
                "ASSIGNMENT_ID": assignment_id,
                "GOAL": args.user_request,
                "STATE": state,
                "HISTORY_EVENTS": history_events_json,
                "PINNED_FINDINGS": pinned_findings,
                "ACTIVE_RULES": consolidated["ACTIVE_RULES"],
                "OPEN_QUESTIONS": consolidated["OPEN_QUESTIONS"],
                "ASSUMPTIONS": consolidated["ASSUMPTIONS"],
                "OBSERVATION": observation,
                "PLAN_HINT": composed.get("PLAN_HINT", ""),
                "DECISION": composed.get("DECISION", ""),
                "TASK": task,
                "SUCCESS_CRITERIA": success_criteria,
                "CONTEXT": context,
                "CONTEXT_BUDGET": context_budget,
                "CONSTRAINTS": constraints,
                "DEGREES_OF_FREEDOM": degrees_of_freedom,
                "DELIVERABLES": deliverables,
                "STATE_DELTA": consolidated["STATE_DELTA"],
                "action_type": action_type,
            }
            write_json(step_dir / "assignment_pack.json", assignment_pack_data)
            write_text(
                step_dir / "assignment_pack.md",
                build_assignment_pack(assignment_pack_data),
            )

            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write(f"## Step {step}\n\n")
                handle.write("Assignment pack:\n")
                handle.write(build_assignment_pack(assignment_pack_data) + "\n")
                handle.write(f"Action type (composer): {action_type}\n\n")

            if action_type != "delegate":
                with trace_path.open("a", encoding="utf-8") as handle:
                    handle.write("Stopped: assignment composer requested stop.\n\n")
                break
            if not task or not success_criteria:
                with trace_path.open("a", encoding="utf-8") as handle:
                    handle.write("Stopped: missing TASK or SUCCESS_CRITERIA.\n\n")
                break
            if args.dry_run:
                with trace_path.open("a", encoding="utf-8") as handle:
                    handle.write("Stopped: dry-run (no actor execution).\n\n")
                break
            if opencode_backend is None:
                raise RuntimeError("OpenCode ACP backend not initialized.")

            assignment = Assignment(
                assignment_id=assignment_id,
                task=task,
                success_criteria=success_criteria,
                overall_goal=args.user_request,
                context=context,
                context_budget=context_budget,
                constraints=constraints,
                degrees_of_freedom=degrees_of_freedom,
                deliverables=deliverables,
            )
            assignment_text = assignment.to_text()
            write_text(step_dir / "assignment.txt", assignment_text)
            write_json(step_dir / "assignment.json", asdict(assignment))

            opencode_result_dir = step_dir / "opencode_result"
            result = asyncio.run(
                opencode_backend.run_assignment(
                    assignment_text=assignment_text,
                    assignment_id=assignment_id,
                    progress_dir=opencode_result_dir,
                )
            )
            OpencodeACPBackend.persist_result(opencode_result_dir, result)
            write_text(step_dir / "actor_result.txt", result.agent_message)

            parsed = parse_result(
                result.agent_message, fallback_assignment_id=assignment_id
            )
            write_json(step_dir / "parsed_result.json", asdict(parsed))
            if parsed.errors:
                write_text(
                    step_dir / "result_parse_errors.txt", "\n".join(parsed.errors)
                )

            observation = render_result_observation(parsed.result).strip()
            if not observation:
                observation = truncate_text(result.agent_message, limit=1200).strip()
            write_text(step_dir / "observation_summary.txt", observation)

            evidence_text = parsed.result.evidence or ""
            evidence_has_path = bool(
                re.search(r"\S+\.\w+(:\d+(-\d+)?)?", evidence_text)
            )
            evidence_has_cmd = bool(re.search(r"`.+`", evidence_text))
            evidence_ok = evidence_has_path or evidence_has_cmd
            if parsed.errors or not evidence_ok:
                issues = []
                if parsed.errors:
                    issues.extend(parsed.errors)
                if not evidence_ok:
                    issues.append("missing evidence with file+line or command snippet")
                observation = (
                    observation
                    + "\n\nEVIDENCE_REQUIRED: Provide RESULT/EVIDENCE/STATE_DELTA with a tool-backed citation (file path + line numbers or command output snippet)."
                )
                write_text(step_dir / "result_parse_errors.txt", "\n".join(issues))

            new_step_json = json.dumps(
                {
                    "step": step,
                    "observation_input": observation_input,
                    "decision": assignment_pack_data.get("DECISION", ""),
                    "plan_hint": assignment_pack_data.get("PLAN_HINT", ""),
                    "assignment": {
                        "id": assignment_id,
                        "task": task,
                        "success_criteria": success_criteria,
                        "context": context,
                        "constraints": constraints,
                        "degrees_of_freedom": degrees_of_freedom,
                        "deliverables": deliverables,
                    },
                    "actor": {
                        "kind": "opencode-acp",
                        "stop_reason": result.stop_reason,
                        "opencode_stderr": truncate_text(
                            result.opencode_stderr, limit=300
                        ),
                    },
                    "result": {
                        "result": parsed.result.result,
                        "evidence": parsed.result.evidence,
                        "state_delta": parsed.result.state_delta,
                        "changes": parsed.result.changes,
                        "artifacts": parsed.result.artifacts,
                        "risks": parsed.result.risks,
                        "open_questions": parsed.result.open_questions,
                        "next_options": parsed.result.next_options,
                        "parse_errors": parsed.errors,
                    },
                },
                indent=2,
                sort_keys=True,
            )
            # Persist verbose step payload for offline analysis (not fed back to the LM).
            write_text(step_dir / "new_step.json", new_step_json + "\n")

            # Deterministic compact history (LM-visible): append one bounded event.
            result_label = (parsed.result.result or "").strip().lower()
            ok = result_label in {"ok", "success", "done", "true"}
            compact_event: Dict[str, Any] = {
                "step": step,
                "action_type": "delegate",
                "intent": "assignment",
                "task": _collapse_ws_one_line(task, limit=160),
                "ok": ok,
            }
            files = _extract_artifact_paths(parsed.result.artifacts)
            if files:
                compact_event["files"] = files

            history_list = safe_parse_history_events(history_events_json)
            history_list.append(compact_event)
            history_events_json = safe_dump_history_events(
                maybe_limit_history_events(history_list, args.max_history_events)
            )
            pinned_findings = update_pinned_findings_from_event(
                pinned_findings, event=compact_event, max_lines=args.max_pinned_findings
            )

            write_json(step_dir / "history_compact_event.json", compact_event)
            write_text(step_dir / "history_events.json", history_events_json + "\n")
            write_text(
                step_dir / "pinned_findings.md",
                (pinned_findings + "\n") if pinned_findings else "",
            )
            write_text(
                step_dir / "history_delta.json",
                json.dumps([compact_event], indent=2, sort_keys=True) + "\n",
            )
            write_text(run_dir / "history_events.json", history_events_json + "\n")
            write_text(
                run_dir / "pinned_findings.md",
                (pinned_findings + "\n") if pinned_findings else "",
            )

            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write("Assignment:\n")
                handle.write(assignment_text + "\n")
                handle.write("Actor result (raw):\n")
                handle.write(truncate_text(result.agent_message, limit=2000) + "\n\n")
                handle.write("Observation summary:\n")
                handle.write(observation + "\n\n")
                if parsed.errors:
                    handle.write("Result parse errors:\n")
                    for err in parsed.errors:
                        handle.write(f"- {err}\n")
                    handle.write("\n")

            if not observation:
                with trace_path.open("a", encoding="utf-8") as handle:
                    handle.write("Stopped: empty observation summary.\n\n")
                break

            # Persist token usage for delegate path (we `continue` before the normal
            # outcome/prefilter token persistence below).
            step_token_payload = {
                "step": step,
                "input_tokens_step": step_input_tokens,
                "output_tokens_step": step_output_tokens,
                "total_tokens_step": step_input_tokens + step_output_tokens,
                "input_tokens_total": total_input_tokens,
                "output_tokens_total": total_output_tokens,
                "total_tokens_total": total_input_tokens + total_output_tokens,
            }
            write_json(step_dir / "token_usage.json", step_token_payload)
            append_jsonl(token_usage_steps_path, step_token_payload)
            persist_token_usage(step=step)
            continue

        raw_action_type = composed.get("action_type", "")
        compiled = compile_action(
            raw_action_type, composed.get("command", ""), args.interaction_mode
        )
        action_type = compiled["action_type"]
        command = compiled["command"]
        rewrite_reason = compiled["rewrite_reason"]
        command_intent = (composed.get("command_intent", "") or "").strip().lower()

        sanitize_reason = ""
        if args.interaction_mode == "none" and action_type == "shell_command":
            sanitized, sanitize_reason = sanitize_noninteractive_command(command)
            if sanitize_reason:
                command = sanitized
                write_text(step_dir / "command_rewrite.txt", sanitize_reason + "\n")

        policy = apply_command_policy(
            step=step,
            goal=args.user_request,
            observation=observation,
            history_events_json=history_events_json,
            proposed_task=(composed.get("TASK", "") or "").strip(),
            proposed_success_criteria=(
                composed.get("SUCCESS_CRITERIA", "") or ""
            ).strip(),
            action_type=action_type,
            command=command,
            command_intent=command_intent,
        )
        command_policy_rewrites = policy["rewrites"]
        if command_policy_rewrites:
            command = policy["command"]
        action_type = policy.get("action_type", action_type)
        command_intent = policy.get("command_intent", command_intent)

        # Re-apply noninteractive sanitization after policy rewrites (policy may change
        # action_type and/or command).
        if args.interaction_mode == "none" and action_type == "shell_command":
            sanitized2, sanitize_reason2 = sanitize_noninteractive_command(command)
            if sanitize_reason2:
                command = sanitized2
                sanitize_reason = (
                    sanitize_reason + "+" + sanitize_reason2
                    if sanitize_reason
                    else sanitize_reason2
                )
                write_text(step_dir / "command_rewrite.txt", sanitize_reason + "\n")

        action_pack_data = {
            "GOAL": args.user_request,
            "STATE": state,
            "HISTORY_EVENTS": history_events_json,
            "PINNED_FINDINGS": pinned_findings,
            "ACTIVE_RULES": consolidated["ACTIVE_RULES"],
            "OPEN_QUESTIONS": consolidated["OPEN_QUESTIONS"],
            "ASSUMPTIONS": consolidated["ASSUMPTIONS"],
            "OBSERVATION": observation,
            "PLAN_HINT": composed.get("PLAN_HINT", ""),
            "DECISION": composed.get("DECISION", ""),
            "TASK": policy["task"],
            "SUCCESS_CRITERIA": policy["success_criteria"],
            "ARTIFACTS": "",
            "STATE_DELTA": consolidated["STATE_DELTA"],
            "action_type": action_type,
            "command": command,
            "command_intent": command_intent,
            "interaction_mode": args.interaction_mode,
            "rewrite_reason": rewrite_reason,
            "sanitize_reason": sanitize_reason,
            "command_policy_rewrites": command_policy_rewrites,
        }
        write_json(step_dir / "action_pack.json", action_pack_data)
        if command_policy_rewrites:
            write_json(
                step_dir / "command_policy_rewrites.json", command_policy_rewrites
            )

        action_pack = build_action_pack(action_pack_data)
        write_text(step_dir / "action_pack.md", action_pack)

        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(f"## Step {step}\n\n")
            handle.write(f"Observation:\n{observation}\n\n")
            handle.write("Action pack:\n")
            handle.write(action_pack + "\n")
            handle.write(
                f"Action type (reasoner): {normalize_action_type(raw_action_type)}\n"
            )
            if rewrite_reason:
                handle.write(f"Action rewrite: {rewrite_reason}\n")
            if command_policy_rewrites:
                handle.write("Command policy rewrites:\n")
                for item in command_policy_rewrites:
                    handle.write(f"- {item.get('kind','')}: {item.get('reason','')}\n")
            handle.write(f"Action type (compiled): {action_type}\n\n")

        if action_type not in ("shell_command", "python_edit", "edit"):
            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write(f"Stopped: unsupported action type '{action_type}'.\n\n")
            break

        if not command and action_type != "stop":
            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write("Stopped: empty command.\n\n")
            break

        constraints_text = consolidated.get("ACTIVE_RULES", "").lower()
        if "no code changes" in constraints_text and looks_like_write_command(command):
            write_text(step_dir / "command_blocked.txt", command)
            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    "Stopped: command blocked by no-code-change constraint.\n\n"
                )
            break

        write_text(step_dir / "command.txt", command)

        # Execute action based on type
        if action_type == "edit":
            if not args.dry_run:
                try:
                    edit_params = json.loads(command)
                    edit_path = Path(edit_params["path"])
                    if not edit_path.is_absolute():
                        edit_path = (workspace_dir or Path.cwd()) / edit_path

                    edit_res = perform_surgical_edit(
                        edit_path, edit_params["old_text"], edit_params["new_text"]
                    )

                    if edit_res["success"]:
                        stdout = (
                            f"SUCCESS: surgical edit applied via {edit_res['method']}"
                        )
                        stderr = ""
                        returncode = "0"
                    else:
                        stdout = ""
                        stderr = f"FAILURE: {edit_res['error']}"
                        returncode = "1"
                except Exception as e:
                    stdout = ""
                    stderr = f"ERROR parsing edit command: {e}"
                    returncode = "1"
            else:
                stdout = "DRY_RUN: surgical edit would be applied"
                stderr = ""
                returncode = "0"

        elif action_type == "python_edit":
            # Execute Python code via RLM-PoT
            if not args.dry_run:
                python_result = handle_python_action(command, working_dir=workspace_dir)
                write_text(step_dir / "stdout.txt", python_result["stdout"])
                write_text(step_dir / "stderr.txt", python_result["stderr"])
                write_text(step_dir / "returncode.txt", python_result["returncode"])
            stdout = (
                read_text(step_dir / "stdout.txt")
                if (step_dir / "stdout.txt").exists()
                else ""
            )
            stderr = (
                read_text(step_dir / "stderr.txt")
                if (step_dir / "stderr.txt").exists()
                else ""
            )
            returncode = (
                read_text(step_dir / "returncode.txt").strip()
                if (step_dir / "returncode.txt").exists()
                else ""
            )
        else:
            # Execute shell command via bash
            env = os.environ.copy()
            env.update(
                {
                    "STEP_DIR": str(step_dir.resolve()),
                    "TASK": action_pack_data.get("TASK", ""),
                    "CONTEXT": render_context_for_bash(action_pack_data),
                    "CONSTRAINTS": action_pack_data.get("ACTIVE_RULES", ""),
                    "SUCCESS_CRITERIA": action_pack_data.get("SUCCESS_CRITERIA", ""),
                    "ASSUMPTIONS": action_pack_data.get("ASSUMPTIONS", ""),
                    "OPEN_QUESTIONS": action_pack_data.get("OPEN_QUESTIONS", ""),
                    "COMMAND": command,
                    "WORKSPACE_DIR": str(workspace_dir) if workspace_dir else "",
                }
            )
            write_json(
                step_dir / "command_env.json",
                {
                    k: env[k]
                    for k in env
                    if k
                    in {
                        "TASK",
                        "CONTEXT",
                        "CONSTRAINTS",
                        "SUCCESS_CRITERIA",
                        "COMMAND",
                        "ASSUMPTIONS",
                        "OPEN_QUESTIONS",
                        "STEP_DIR",
                        "WORKSPACE_DIR",
                    }
                },
            )

            if not args.dry_run:
                run_bash_step(step_dir, env, workspace_dir)

            stdout = (
                read_text(step_dir / "stdout.txt")
                if (step_dir / "stdout.txt").exists()
                else ""
            )
            stderr = (
                read_text(step_dir / "stderr.txt")
                if (step_dir / "stderr.txt").exists()
                else ""
            )
            returncode = (
                read_text(step_dir / "returncode.txt").strip()
                if (step_dir / "returncode.txt").exists()
                else ""
            )

        is_read_operation = command_intent in ("read", "grep", "navigate")
        file_context = (
            extract_file_path_from_command(command) if is_read_operation else ""
        )

        outcome_input = {
            "goal": args.user_request,
            "task": action_pack_data.get("TASK", ""),
            "success_criteria": action_pack_data.get("SUCCESS_CRITERIA", ""),
            "command": command,
            "command_intent": command_intent,
            "returncode": returncode,
            "stdout": truncate_text(
                stdout,
                limit=12000 if is_read_operation else 8000,
                strategy="head" if is_read_operation else "tail",
                context_hint=file_context,
            ),
            "stderr": truncate_text(stderr, strategy="tail"),
        }
        write_json(step_dir / "outcome_input.json", outcome_input)

        outcome_out, outcome_timing = robust_predict(outcome_evaluator, **outcome_input)
        record_lm_usage(
            step, step_dir, "outcome_evaluator", outcome_out, outcome_timing
        )
        in_tokens, out_tokens = extract_token_usage(outcome_out)
        total_input_tokens += in_tokens
        total_output_tokens += out_tokens
        step_input_tokens += in_tokens
        step_output_tokens += out_tokens
        outcome_data = {
            "observation": getattr(outcome_out, "observation", ""),
            "artifacts": getattr(outcome_out, "artifacts", ""),
            "state_delta": getattr(outcome_out, "state_delta", ""),
            "success": getattr(outcome_out, "success", ""),
            "issues": getattr(outcome_out, "issues", ""),
            "needs_verification": getattr(outcome_out, "needs_verification", ""),
        }

        # EDIT VERIFICATION ENFORCEMENT (DETERMINISTIC):
        # If command_intent is 'edit' and either:
        # 1. LLM said needs_verification is true, OR
        # 2. DETERMINISTIC: stdout is empty and returncode is 0 (silent success pattern)
        # Then override success to false and require verification.
        # This prevents the agent from assuming success without evidence - LLMs often
        # incorrectly report success for sed/patch commands that silently do nothing.
        is_edit_intent = command_intent == "edit"
        llm_says_verify = (
            str(outcome_data.get("needs_verification", "")).strip().lower() == "true"
        )
        silent_success = not stdout.strip() and returncode == "0"

        if is_edit_intent and (llm_says_verify or silent_success):
            outcome_data["success"] = "false"
            outcome_data["needs_verification"] = "true"
            verification_reminder = (
                "\n\n**VERIFICATION REQUIRED:** Edit command returned exit code 0 but "
                "produced no output. Commands like `sed -i` return 0 even when no changes are made. "
                "You MUST verify the edit was applied by running `git diff <file>` or re-reading "
                "the modified section before proceeding."
            )
            outcome_data["observation"] = (
                outcome_data.get("observation", "") + verification_reminder
            )
            outcome_data["issues"] = (
                outcome_data.get("issues", "") + " Edit verification required."
            ).strip()

        write_json(step_dir / "outcome_output.json", outcome_data)
        write_text(
            step_dir / "artifacts.txt", outcome_data.get("artifacts", "").strip()
        )
        write_text(
            step_dir / "outcome_state_delta.txt",
            outcome_data.get("state_delta", "").strip(),
        )

        prefilter_input = {
            "task": action_pack_data.get("TASK", ""),
            "command": command,
            "returncode": returncode,
            "stdout_truncated": truncate_text(
                stdout,
                limit=2000 if is_read_operation else 1200,
                strategy="head" if is_read_operation else "tail",
                context_hint=file_context,
            ),
            "stderr_truncated": truncate_text(stderr, limit=1200, strategy="tail"),
        }
        write_json(step_dir / "evidence_prefilter_input.json", prefilter_input)
        prefilter_out, prefilter_timing = robust_predict(
            evidence_prefilter, **prefilter_input
        )
        record_lm_usage(
            step, step_dir, "evidence_prefilter", prefilter_out, prefilter_timing
        )
        in_tokens, out_tokens = extract_token_usage(prefilter_out)
        total_input_tokens += in_tokens
        total_output_tokens += out_tokens
        step_input_tokens += in_tokens
        step_output_tokens += out_tokens
        prefilter_output = {
            "evidence_summary": getattr(prefilter_out, "evidence_summary", "") or "",
            "error_type": getattr(prefilter_out, "error_type", "") or "",
            "error_excerpt": getattr(prefilter_out, "error_excerpt", "") or "",
        }
        write_json(step_dir / "evidence_prefilter_output.json", prefilter_output)

        # Persist token usage (per-step and cumulative) for offline analysis
        step_token_payload = {
            "step": step,
            "input_tokens_step": step_input_tokens,
            "output_tokens_step": step_output_tokens,
            "total_tokens_step": step_input_tokens + step_output_tokens,
            "input_tokens_total": total_input_tokens,
            "output_tokens_total": total_output_tokens,
            "total_tokens_total": total_input_tokens + total_output_tokens,
        }
        write_json(step_dir / "token_usage.json", step_token_payload)
        append_jsonl(token_usage_steps_path, step_token_payload)
        persist_token_usage(step=step)

        new_step_json = json.dumps(
            {
                "step": step,
                "observation_input": observation,
                "decision": action_pack_data.get("DECISION", ""),
                "plan_hint": action_pack_data.get("PLAN_HINT", ""),
                "task": action_pack_data.get("TASK", ""),
                "success_criteria": action_pack_data.get("SUCCESS_CRITERIA", ""),
                "policy": {
                    "compile_rewrite_reason": action_pack_data.get(
                        "rewrite_reason", ""
                    ),
                    "command_policy_rewrites": action_pack_data.get(
                        "command_policy_rewrites", []
                    ),
                },
                "action": {
                    "type": action_type,
                    "command": command,
                },
                "execution": {
                    "returncode": returncode,
                    "stdout_truncated": truncate_text(
                        stdout,
                        limit=300,
                        strategy="head"
                        if command_intent in ("read", "grep", "navigate")
                        else "tail",
                    ),
                    "stderr_truncated": truncate_text(
                        stderr, limit=300, strategy="tail"
                    ),
                    "evidence_summary": prefilter_output["evidence_summary"],
                    "error_type": prefilter_output["error_type"],
                    "error_excerpt": prefilter_output["error_excerpt"],
                },
                "outcome": {
                    "success": outcome_data.get("success", ""),
                    "issues": outcome_data.get("issues", ""),
                    "observation_output": outcome_data.get("observation", ""),
                    "artifacts": outcome_data.get("artifacts", ""),
                },
            },
            indent=2,
            sort_keys=True,
        )
        # Persist verbose step payload for offline analysis (not fed back to the LM).
        write_text(step_dir / "new_step.json", new_step_json + "\n")

        compact_event = build_compact_history_event(
            step=step,
            action_type=action_type,
            command_intent=command_intent,
            task=action_pack_data.get("TASK", "") or "",
            command=command,
            returncode=returncode,
            outcome_data=outcome_data,
            prefilter_output=prefilter_output,
        )
        history_list = safe_parse_history_events(history_events_json)
        history_list.append(compact_event)
        history_events_json = safe_dump_history_events(
            maybe_limit_history_events(history_list, args.max_history_events)
        )
        pinned_findings = update_pinned_findings_from_event(
            pinned_findings, event=compact_event, max_lines=args.max_pinned_findings
        )

        write_json(step_dir / "history_compact_event.json", compact_event)
        write_text(step_dir / "history_events.json", history_events_json + "\n")
        write_text(
            step_dir / "pinned_findings.md",
            (pinned_findings + "\n") if pinned_findings else "",
        )
        write_text(
            step_dir / "history_delta.json",
            json.dumps([compact_event], indent=2, sort_keys=True) + "\n",
        )
        write_text(run_dir / "history_events.json", history_events_json + "\n")
        write_text(
            run_dir / "pinned_findings.md",
            (pinned_findings + "\n") if pinned_findings else "",
        )

        state = merge_bullets(state, outcome_data.get("state_delta", ""))
        observation = outcome_data.get("observation", "").strip()
        write_text(step_dir / "observation_summary.txt", observation)

        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write("Command:\n")
            handle.write(f"`{command}`\n\n")
            handle.write("Stdout (truncated):\n")
            handle.write(truncate_text(stdout) + "\n\n")
            if stderr:
                handle.write("Stderr (truncated):\n")
                handle.write(truncate_text(stderr) + "\n\n")
            handle.write("Observation summary:\n")
            handle.write(observation + "\n\n")

        if not observation:
            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write("Stopped: empty observation summary.\n\n")
            break


if __name__ == "__main__":
    main()
