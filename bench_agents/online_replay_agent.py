from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


import dspy

from terminal_bench.agents.base_agent import BaseAgent, AgentResult
from terminal_bench.agents.failure_mode import FailureMode
from terminal_bench.terminal.tmux_session import TmuxSession

from config import DEFAULT_MODEL, DEFAULT_REASONING_EFFORT, DEFAULT_TEMPERATURE, DSPyGeminiConfig, TYPE_REASONING_EFFORT
from online_replay_loop import (
    ActionSelectorSignature,
    DEFAULT_MAX_HISTORY_EVENTS,
    DEFAULT_MAX_PINNED_FINDINGS,
    DecisionPlannerSignature,
    EvidencePrefilterSignature,
    KnowledgeConsolidatorSignature,
    MAX_ACTIVE_RULE_LINES,
    MAX_ASSUMPTIONS_LINES,
    MAX_OPEN_QUESTIONS_LINES,
    MAX_STATE_DELTA_LINES,
    MAX_STATE_LINES,
    OutcomeEvaluatorSignature,
    apply_command_policy,
    build_compact_history_event,
    build_action_pack,
    compile_action,
    maybe_limit_bullets,
    maybe_limit_history_events,
    merge_bullets,
    normalize_action_type,
    safe_dump_history_events,
    safe_parse_history_events,
    truncate_text,
    update_pinned_findings_from_event,
)

# RLM-PoT: Python REPL for deterministic pattern extraction and variable storage
try:
    from rlm_pot import handle_python_action, reset_executor, is_enabled as rlm_pot_is_enabled
    RLM_POT_AVAILABLE = True
except ImportError:
    RLM_POT_AVAILABLE = False
    def handle_python_action(*args, **kwargs):  # type: ignore[misc]
        return {"returncode": "1", "stdout": "", "stderr": "rlm_pot module not available"}
    def reset_executor():  # type: ignore[misc]
        pass
    def rlm_pot_is_enabled() -> bool:  # type: ignore[misc]
        return False


@dataclass
class RunConfig:
    run_id: str
    started_at: str
    model: str
    phase: str
    interaction_mode: str
    max_steps: int
    rules_path: str


def now_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_rules_text(path: Path) -> str:
    if not path.exists():
        return ""
    return read_text(path)


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


_RETURN_CODE_PATTERN = re.compile(r"__RC=(\d+)__")


def wrap_command_for_return_code(command: str) -> str:
    """
    Ensure the return code of `command` is printed before Terminal-Bench appends its
    internal `; tmux wait -S done` completion marker.
    """
    command = command.strip()
    return f"({command}); printf \"__RC=%s__\\\\n\" $?"


def sanitize_noninteractive_command(command: str) -> tuple[str, str]:
    """
    Best-effort rewrite to avoid interactive programs in `interaction_mode=none`.
    """
    original = command
    command = command.strip()

    # Common: `cat file | less`
    command = re.sub(r"\s*\|\s*less(\s+-\S+)*\s*$", "", command)

    # Simple: `less file`
    match = re.match(r"^less\s+([^\s]+)\s*$", command)
    if match:
        path = match.group(1)
        command = f"sed -n '1,200p' {path}"

    # Avoid dumping huge files into the terminal: `cat file` -> show head snippet.
    match = re.match(r"^cat\s+([^\s]+)\s*$", command)
    if match:
        path = match.group(1)
        command = f"sed -n '1,200p' {path}"

    if command != original:
        return command, "sanitized_interactive_command"
    return command, ""


class OnlineReplayAgent(BaseAgent):
    @staticmethod
    def name() -> str:
        return "online_replay"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        phase: str = "autopilot",
        interaction_mode: str = "none",
        max_steps: int = 10,
        max_history_events: int = DEFAULT_MAX_HISTORY_EVENTS,
        max_pinned_findings: int = DEFAULT_MAX_PINNED_FINDINGS,
        rules_path: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        reasoning_effort: TYPE_REASONING_EFFORT = DEFAULT_REASONING_EFFORT,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._model = model
        self._phase = phase
        self._interaction_mode = interaction_mode
        self._max_steps = max_steps
        self._max_history_events = max_history_events
        self._max_pinned_findings = max_pinned_findings
        self._temperature = temperature
        self._reasoning_effort: TYPE_REASONING_EFFORT = reasoning_effort

        repo_root = Path(__file__).resolve().parents[1]
        if rules_path:
            self._rules_path = Path(rules_path)
        else:
            self._rules_path = repo_root / "STEP_BY_STEP_REASON_RULES_AUTOPILOT.md"
            if not self._rules_path.exists():
                self._rules_path = repo_root / "STEP_BY_STEP_REASON_RULES_BENCH.md"
            if not self._rules_path.exists():
                self._rules_path = repo_root / "STEP_BY_STEP_REASON_RULES.md"

    def perform_task(
        self,
        instruction: str,
        session: TmuxSession,
        logging_dir: Path | None = None,
    ) -> AgentResult:
        run_id = now_stamp()
        run_dir = Path(logging_dir) / "online_replay" / run_id if logging_dir else Path.cwd() / "online_replay" / run_id
        steps_dir = run_dir / "steps"
        ensure_dir(steps_dir)

        # Reset Python executor for fresh state at task start
        reset_executor()

        rules_text = load_rules_text(self._rules_path)

        lm = DSPyGeminiConfig.create_lm(
            model_name=self._model,
            temperature=self._temperature,
            reasoning_effort=self._reasoning_effort,
        )
        adapter = dspy.JSONAdapter()

        consolidator = dspy.Predict(KnowledgeConsolidatorSignature)
        decision_planner = dspy.Predict(DecisionPlannerSignature)
        action_selector = dspy.Predict(ActionSelectorSignature)
        outcome_evaluator = dspy.Predict(OutcomeEvaluatorSignature)
        evidence_prefilter = dspy.Predict(EvidencePrefilterSignature)
        # History is maintained deterministically as a compact, bounded event stream.

        run_config = RunConfig(
            run_id=run_id,
            started_at=datetime.utcnow().isoformat() + "Z",
            model=self._model,
            phase=self._phase,
            interaction_mode=self._interaction_mode,
            max_steps=self._max_steps,
            rules_path=str(self._rules_path),
        )
        write_json(run_dir / "run_config.json", asdict(run_config))

        trace_path = run_dir / "trace.md"
        write_text(
            trace_path,
            "# Online replay trace (Terminal-Bench)\n\n"
            f"Run ID: {run_id}\n"
            f"Model: {self._model}\n"
            f"Phase: {self._phase}\n"
            f"Interaction mode: {self._interaction_mode}\n"
            f"Started: {run_config.started_at}\n\n",
        )

        # Disable history expansion so commands containing "!" (e.g. `Hello, world!`)
        # don't fail with "event not found".
        session.send_keys(["set +H", "Enter"], block=True)
        session.send_keys(["export PAGER=cat", "Enter"], block=True)

        state = ""
        observation = instruction
        history_events_json = "[]"
        pinned_findings = ""

        # Initialize token counters
        total_input_tokens = 0
        total_output_tokens = 0

        def extract_token_usage(prediction_result):
            """Extract token counts from a dspy.Prediction result."""
            try:
                usage = prediction_result.get_lm_usage()
                if usage:
                    # Usage is typically a dict mapping model names to usage stats
                    # Aggregate across all models if it's a dict of dicts
                    input_tokens = 0
                    output_tokens = 0
                    if isinstance(usage, dict):
                        for model_usage in usage.values():
                            if isinstance(model_usage, dict):
                                input_tokens += model_usage.get('prompt_tokens', 0)
                                output_tokens += model_usage.get('completion_tokens', 0)
                    return input_tokens, output_tokens
            except (AttributeError, TypeError):
                pass
            return 0, 0

        for step in range(1, self._max_steps + 1):
            step_dir = steps_dir / f"step_{step:03d}"
            ensure_dir(step_dir)

            write_text(step_dir / "observation_input.txt", observation)

            consolidator_input = {
                "goal": instruction,
                "observation": observation,
                "state": state,
                "history_events_json": history_events_json,
                "pinned_findings": pinned_findings,
                "rules": rules_text,
                "phase": self._phase,
                "interaction_mode": self._interaction_mode,
            }
            write_json(step_dir / "consolidator_input.json", consolidator_input)
            with dspy.context(lm=lm, adapter=adapter):
                consolidator_out = consolidator(**consolidator_input)
            # Collect token usage from consolidator
            in_tokens, out_tokens = extract_token_usage(consolidator_out)
            total_input_tokens += in_tokens
            total_output_tokens += out_tokens
            consolidated = {
                "STATE_DELTA": getattr(consolidator_out, "state_delta", ""),
                "STATE": getattr(consolidator_out, "state_updated", ""),
                "ACTIVE_RULES": getattr(consolidator_out, "active_rules", ""),
                "OPEN_QUESTIONS": getattr(consolidator_out, "open_questions", ""),
                "ASSUMPTIONS": getattr(consolidator_out, "assumptions", ""),
            }
            consolidated["STATE_DELTA"] = maybe_limit_bullets(consolidated["STATE_DELTA"], MAX_STATE_DELTA_LINES)
            consolidated["STATE"] = maybe_limit_bullets(consolidated["STATE"], MAX_STATE_LINES)
            consolidated["ACTIVE_RULES"] = maybe_limit_bullets(consolidated["ACTIVE_RULES"], MAX_ACTIVE_RULE_LINES)
            consolidated["OPEN_QUESTIONS"] = maybe_limit_bullets(consolidated["OPEN_QUESTIONS"], MAX_OPEN_QUESTIONS_LINES)
            consolidated["ASSUMPTIONS"] = maybe_limit_bullets(consolidated["ASSUMPTIONS"], MAX_ASSUMPTIONS_LINES)
            write_json(step_dir / "consolidator_output.json", consolidated)
            write_text(step_dir / "state_delta.txt", consolidated["STATE_DELTA"].strip())
            write_text(step_dir / "open_questions.txt", consolidated["OPEN_QUESTIONS"].strip())
            write_text(step_dir / "assumptions.txt", consolidated["ASSUMPTIONS"].strip())

            state = consolidated.get("STATE", state).strip()

            def run_decision_planner() -> tuple[dict, object]:
                with dspy.context(lm=lm, adapter=adapter):
                    out = decision_planner(
                        goal=instruction,
                        observation=observation,
                        state=state,
                        history_events_json=history_events_json,
                        pinned_findings=pinned_findings,
                        active_rules=consolidated["ACTIVE_RULES"],
                        phase=self._phase,
                        interaction_mode=self._interaction_mode,
                    )
                return {
                    "PLAN_HINT": getattr(out, "plan_hint", ""),
                    "DECISION": getattr(out, "decision", ""),
                }, out

            def run_action_selector() -> tuple[dict, object]:
                with dspy.context(lm=lm, adapter=adapter):
                    out = action_selector(
                        goal=instruction,
                        observation=observation,
                        state=state,
                        history_events_json=history_events_json,
                        pinned_findings=pinned_findings,
                        active_rules=consolidated["ACTIVE_RULES"],
                        phase=self._phase,
                        interaction_mode=self._interaction_mode,
                    )
                return {
                    "action_type": getattr(out, "action_type", ""),
                    "TASK": getattr(out, "task", ""),
                    "SUCCESS_CRITERIA": getattr(out, "success_criteria", ""),
                    "command": getattr(out, "command", ""),
                    "command_intent": getattr(out, "command_intent", ""),
                }, out

            with ThreadPoolExecutor(max_workers=2) as pool:
                future_decision = pool.submit(run_decision_planner)
                future_action = pool.submit(run_action_selector)
                decision_dict, decision_out = future_decision.result()
                action_dict, action_out = future_action.result()
                
                # Collect token usage from parallel calls
                in_tokens, out_tokens = extract_token_usage(decision_out)
                total_input_tokens += in_tokens
                total_output_tokens += out_tokens
                in_tokens, out_tokens = extract_token_usage(action_out)
                total_input_tokens += in_tokens
                total_output_tokens += out_tokens
                
                composed = {}
                composed.update(decision_dict)
                composed.update(action_dict)

            write_json(step_dir / "decision_planner_output.json", {k: composed[k] for k in ["PLAN_HINT", "DECISION"]})
            write_json(
                step_dir / "action_selector_output.json",
                {k: composed[k] for k in ["action_type", "TASK", "SUCCESS_CRITERIA", "command"]},
            )

            raw_action_type = composed.get("action_type", "")
            compiled = compile_action(raw_action_type, composed.get("command", ""), self._interaction_mode)
            action_type = compiled["action_type"]
            command = compiled["command"]
            rewrite_reason = compiled["rewrite_reason"]
            command_intent = (composed.get("command_intent", "") or "").strip().lower()

            if action_type not in ("shell_command", "python_edit"):
                with trace_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"Stopped: unsupported action type '{action_type}'.\n\n")
                break

            if not command:
                with trace_path.open("a", encoding="utf-8") as handle:
                    handle.write("Stopped: empty command.\n\n")
                break

            sanitize_reason = ""
            if self._interaction_mode == "none" and action_type == "shell_command":
                sanitized_command, sanitize_reason = sanitize_noninteractive_command(command)
                if sanitize_reason:
                    command = sanitized_command
                    write_text(step_dir / "command_rewrite.txt", sanitize_reason)
                    with trace_path.open("a", encoding="utf-8") as handle:
                        handle.write(f"Command rewrite: {sanitize_reason}\n\n")

            policy = apply_command_policy(
                step=step,
                goal=instruction,
                observation=observation,
                history_events_json=history_events_json,
                proposed_task=(composed.get("TASK", "") or "").strip(),
                proposed_success_criteria=(composed.get("SUCCESS_CRITERIA", "") or "").strip(),
                action_type=action_type,
                command=command,
            )
            command_policy_rewrites = policy["rewrites"]
            if command_policy_rewrites:
                command = policy["command"]
                write_json(step_dir / "command_policy_rewrites.json", command_policy_rewrites)

            action_pack_data = {
                "GOAL": instruction,
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
                "interaction_mode": self._interaction_mode,
                "rewrite_reason": rewrite_reason,
                "sanitize_reason": sanitize_reason,
                "command_policy_rewrites": command_policy_rewrites,
            }
            write_json(step_dir / "action_pack.json", action_pack_data)

            action_pack = build_action_pack(action_pack_data)
            write_text(step_dir / "action_pack.md", action_pack)

            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write(f"## Step {step}\n\n")
                handle.write(f"Observation:\n{observation}\n\n")
                handle.write("Action pack:\n")
                handle.write(action_pack + "\n")
                handle.write(f"Action type (reasoner): {normalize_action_type(raw_action_type)}\n")
                if rewrite_reason:
                    handle.write(f"Action rewrite: {rewrite_reason}\n")
                if sanitize_reason:
                    handle.write(f"Command rewrite: {sanitize_reason}\n")
                if command_policy_rewrites:
                    handle.write("Command policy rewrites:\n")
                    for item in command_policy_rewrites:
                        handle.write(f"- {item.get('kind','')}: {item.get('reason','')}\n")
                handle.write(f"Action type (compiled): {action_type}\n\n")

            constraints_text = consolidated.get("ACTIVE_RULES", "").lower()
            if "no code changes" in constraints_text and looks_like_write_command(command):
                write_text(step_dir / "command_blocked.txt", command)
                with trace_path.open("a", encoding="utf-8") as handle:
                    handle.write("Stopped: command blocked by no-code-change constraint.\n\n")
                break

            write_text(step_dir / "command.txt", command)

            # Execute action based on type
            if action_type == "python_edit":
                # Execute Python code via RLM-PoT (bypasses tmux)
                # Get workspace directory from session's current working directory
                workspace_path = None
                try:
                    session.send_keys(["pwd", "Enter"], block=True)
                    pwd_output = session.get_incremental_output().strip()
                    if pwd_output:
                        workspace_path = Path(pwd_output.split('\n')[-1].strip())
                except Exception:
                    pass
                python_result = handle_python_action(command, working_dir=workspace_path)
                returncode = python_result["returncode"]
                combined_output = python_result["stdout"]
                if python_result["stderr"]:
                    combined_output += f"\n[stderr]: {python_result['stderr']}"
            else:
                # Execute shell command via tmux
                wrapped = wrap_command_for_return_code(command)
                session.send_keys([wrapped, "Enter"], block=True)
                output = session.get_incremental_output()
                matches = _RETURN_CODE_PATTERN.findall(output)
                returncode = matches[-1] if matches else "unknown"
                combined_output = output.strip()

            outcome_input = {
                "goal": instruction,
                "task": action_pack_data.get("TASK", ""),
                "success_criteria": action_pack_data.get("SUCCESS_CRITERIA", ""),
                "command": command,
                "command_intent": command_intent,
                "returncode": returncode,
                "stdout": truncate_text(combined_output),
                "stderr": "",
            }
            write_json(step_dir / "outcome_input.json", outcome_input)

            with dspy.context(lm=lm, adapter=adapter):
                outcome_out = outcome_evaluator(**outcome_input)
            # Collect token usage from outcome evaluator
            in_tokens, out_tokens = extract_token_usage(outcome_out)
            total_input_tokens += in_tokens
            total_output_tokens += out_tokens
            outcome_data = {
                "observation": getattr(outcome_out, "observation", ""),
                "artifacts": getattr(outcome_out, "artifacts", ""),
                "state_delta": getattr(outcome_out, "state_delta", ""),
                "success": getattr(outcome_out, "success", ""),
                "issues": getattr(outcome_out, "issues", ""),
                "needs_verification": getattr(outcome_out, "needs_verification", ""),
            }
            write_json(step_dir / "outcome_output.json", outcome_data)
            write_text(step_dir / "artifacts.txt", outcome_data.get("artifacts", "").strip())
            write_text(step_dir / "outcome_state_delta.txt", outcome_data.get("state_delta", "").strip())

            prefilter_input = {
                "task": action_pack_data.get("TASK", ""),
                "command": command,
                "returncode": returncode,
                "stdout_truncated": truncate_text(combined_output, limit=1200),
                "stderr_truncated": "",
            }
            write_json(step_dir / "evidence_prefilter_input.json", prefilter_input)
            with dspy.context(lm=lm, adapter=adapter):
                prefilter_out = evidence_prefilter(**prefilter_input)
            # Collect token usage from evidence prefilter
            in_tokens, out_tokens = extract_token_usage(prefilter_out)
            total_input_tokens += in_tokens
            total_output_tokens += out_tokens
            prefilter_output = {
                "evidence_summary": getattr(prefilter_out, "evidence_summary", "") or "",
                "error_type": getattr(prefilter_out, "error_type", "") or "",
                "error_excerpt": getattr(prefilter_out, "error_excerpt", "") or "",
            }
            write_json(step_dir / "evidence_prefilter_output.json", prefilter_output)

            new_step_json = json.dumps(
                {
                    "step": step,
                    "observation_input": observation,
                    "decision": action_pack_data.get("DECISION", ""),
                    "plan_hint": action_pack_data.get("PLAN_HINT", ""),
                    "task": action_pack_data.get("TASK", ""),
                    "success_criteria": action_pack_data.get("SUCCESS_CRITERIA", ""),
                    "policy": {
                        "compile_rewrite_reason": action_pack_data.get("rewrite_reason", ""),
                        "sanitize_reason": action_pack_data.get("sanitize_reason", ""),
                        "command_policy_rewrites": action_pack_data.get("command_policy_rewrites", []),
                    },
                    "action": {
                        "type": action_type,
                        "command": command,
                    },
                    "execution": {
                        "returncode": returncode,
                        "terminal_output_truncated": truncate_text(combined_output, limit=300),
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
                maybe_limit_history_events(history_list, self._max_history_events)
            )
            pinned_findings = update_pinned_findings_from_event(
                pinned_findings, event=compact_event, max_lines=self._max_pinned_findings
            )

            write_json(step_dir / "history_compact_event.json", compact_event)
            write_text(step_dir / "history_events.json", history_events_json + "\n")
            write_text(step_dir / "pinned_findings.md", (pinned_findings + "\n") if pinned_findings else "")
            write_text(step_dir / "history_delta.json", json.dumps([compact_event], indent=2, sort_keys=True) + "\n")
            write_text(run_dir / "history_events.json", history_events_json + "\n")
            write_text(run_dir / "pinned_findings.md", (pinned_findings + "\n") if pinned_findings else "")

            state = merge_bullets(state, outcome_data.get("state_delta", ""))
            observation = outcome_data.get("observation", "").strip()
            write_text(step_dir / "observation_summary.txt", observation)

            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write("Command:\n")
                handle.write(f"`{command}`\n\n")
                handle.write("Terminal output (truncated):\n")
                handle.write(truncate_text(combined_output) + "\n\n")
                handle.write("Observation summary:\n")
                handle.write(observation + "\n\n")

            if not observation:
                with trace_path.open("a", encoding="utf-8") as handle:
                    handle.write("Stopped: empty observation summary.\n\n")
                break

        return AgentResult(
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            failure_mode=FailureMode.NONE,
        )
