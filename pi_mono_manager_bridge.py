from __future__ import annotations

import os
import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

os.environ.setdefault(
    "DSPY_CACHEDIR",
    str((Path(__file__).resolve().parent / "data" / "dspy_cache").resolve()),
)

import dspy

from config import (
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_TEMPERATURE,
    DSPyGeminiConfig,
    TYPE_REASONING_EFFORT,
)
from online_replay_loop import DecisionPlannerSignature, KnowledgeConsolidatorSignature

SteerPolicy = Literal["off", "on_error", "always"]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str, *, limit: int) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3] + "..."


def _safe_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _safe_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_jsonable(v) for v in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _safe_jsonable(model_dump(mode="json"))
        except Exception:
            return {"repr": repr(value)}
    return {"repr": repr(value)}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_safe_jsonable(data), indent=2, sort_keys=True), encoding="utf-8")


def _append_jsonl(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_safe_jsonable(data), ensure_ascii=False) + "\n")


_SLUG_ALLOWED = re.compile(r"[^a-zA-Z0-9._-]+")


def _sanitize_slug(value: str, *, fallback: str = "session") -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    cleaned = _SLUG_ALLOWED.sub("_", raw)
    cleaned = cleaned.strip("._-") or fallback
    return cleaned[:120]


def _looks_dangerous_bash(command: str) -> str | None:
    cmd = (command or "").strip()
    if not cmd:
        return None

    lowered = cmd.lower()
    if "rm -rf" in lowered or "rm -fr" in lowered:
        return "refusing `rm -rf`-style command"
    if re.search(r"\bgit\s+reset\s+--hard\b", lowered):
        return "refusing destructive git reset"
    if re.search(r"\bgit\s+clean\s+-f", lowered):
        return "refusing destructive git clean"
    if re.search(r"\bmkfs\.", lowered) or "dd if=" in lowered:
        return "refusing disk-destructive command"
    return None


@dataclass(frozen=True)
class BridgeConfig:
    log_dir: Path = Path("data/pi_mono_bridge")
    enable_lm: bool = False
    model: str = DEFAULT_MODEL
    temperature: float = DEFAULT_TEMPERATURE
    reasoning_effort: TYPE_REASONING_EFFORT = DEFAULT_REASONING_EFFORT
    steer_policy: SteerPolicy = "off"
    max_history_events: int = 60
    max_observation_chars: int = 8_000
    max_steer_chars: int = 2_000


@dataclass
class SessionState:
    session_id: str
    started_at: str
    run_dir: Path
    goal: str = ""
    state: str = ""
    active_rules: str = ""
    open_questions: str = ""
    assumptions: str = ""
    pinned_findings: str = ""
    history_events: list[dict[str, Any]] = field(default_factory=list)
    last_turn_index: int | None = None

    @property
    def events_path(self) -> Path:
        return self.run_dir / "events.jsonl"

    @property
    def lm_usage_events_path(self) -> Path:
        return self.run_dir / "lm_usage_events.jsonl"

    def snapshot(self) -> dict[str, Any]:
        return asdict(self) | {"run_dir": str(self.run_dir)}


class PiMonoManagerBridge:
    """
    Variant B bridge core:
    - Pi runs the agent loop.
    - This bridge receives hook events (tool_call/tool_result/turn_end).
    - It can (optionally) use DSPy to maintain a compact Manager-style state and
      return steering text or tool-call blocks.
    """

    def __init__(self, config: BridgeConfig):
        self._config = config
        self._sessions: dict[str, SessionState] = {}

        self._lm: dspy.LM | None = None
        self._adapter: dspy.JSONAdapter | None = None
        self._consolidator: Any | None = None
        self._planner: Any | None = None
        self._lm_lock = threading.Lock()

        self._config.log_dir.mkdir(parents=True, exist_ok=True)
        dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)

        if self._config.enable_lm:
            self._lm = DSPyGeminiConfig.create_lm(
                model_name=self._config.model,
                temperature=self._config.temperature,
                reasoning_effort=self._config.reasoning_effort,
            )
            self._adapter = dspy.JSONAdapter()
            self._consolidator = dspy.Predict(KnowledgeConsolidatorSignature)
            self._planner = dspy.Predict(DecisionPlannerSignature)

            if self._config.steer_policy == "off":
                self._config = BridgeConfig(**(asdict(self._config) | {"steer_policy": "on_error"}))

    def _ensure_session(self, session_id: str) -> SessionState:
        if session_id in self._sessions:
            return self._sessions[session_id]

        slug = _sanitize_slug(session_id, fallback="session")
        run_dir = (self._config.log_dir / slug).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        session = SessionState(
            session_id=session_id,
            started_at=_utc_now_iso(),
            run_dir=run_dir,
        )
        _write_json(run_dir / "session.json", session.snapshot())
        self._sessions[session_id] = session
        return session

    def _record_event(self, session: SessionState, event: dict[str, Any]) -> None:
        _append_jsonl(
            session.events_path,
            {"at": _utc_now_iso(), "session_id": session.session_id, "event": event},
        )
        _write_json(session.run_dir / "session.json", session.snapshot())

    def _safe_get_lm_usage(self, prediction_result: Any) -> dict[str, Any]:
        try:
            usage = prediction_result.get_lm_usage()  # type: ignore[attr-defined]
            return usage if isinstance(usage, dict) else {}
        except Exception:
            return {}

    def _record_lm_usage(
        self,
        *,
        session: SessionState,
        call_name: str,
        prediction_result: Any,
        timing: dict[str, Any],
    ) -> None:
        usage = self._safe_get_lm_usage(prediction_result)
        _append_jsonl(
            session.lm_usage_events_path,
            {
                "at": _utc_now_iso(),
                "call": call_name,
                "timing": timing,
                "usage": usage,
            },
        )
        _write_json(session.run_dir / f"lm_usage_{call_name}.json", usage)

    def _robust_predict(self, *, session: SessionState, predictor: Any, call_name: str, **kwargs: Any) -> Any:
        if not self._lm or not self._adapter:
            raise RuntimeError("LM disabled (enable_lm=False)")

        max_retries = 2
        started_at = _utc_now_iso()
        start_perf = time.perf_counter()
        last_error: Exception | None = None

        with self._lm_lock:
            for attempt in range(max_retries + 1):
                try:
                    with dspy.context(lm=self._lm, adapter=self._adapter, track_usage=True):
                        prediction = predictor(**kwargs)
                    ended_at = _utc_now_iso()
                    duration_ms = int((time.perf_counter() - start_perf) * 1000)
                    timing: dict[str, Any] = {
                        "started_at": started_at,
                        "ended_at": ended_at,
                        "duration_ms": duration_ms,
                        "attempts": attempt + 1,
                    }
                    if last_error is not None:
                        timing["last_error"] = str(last_error)
                    self._record_lm_usage(
                        session=session,
                        call_name=call_name,
                        prediction_result=prediction,
                        timing=timing,
                    )
                    return prediction
                except Exception as exc:  # pragma: no cover (network/provider variability)
                    last_error = exc
                    if attempt < max_retries:
                        time.sleep(0.35 * (attempt + 1))
                        continue
                    raise

        raise RuntimeError("unreachable")

    def _append_history(self, session: SessionState, *, event: dict[str, Any]) -> None:
        session.history_events.append(event)
        if self._config.max_history_events > 0:
            session.history_events = session.history_events[-self._config.max_history_events :]

    def _format_history_json(self, session: SessionState) -> str:
        return json.dumps(session.history_events, indent=2, sort_keys=True)

    def _build_observation_from_turn_end(self, event: dict[str, Any]) -> str:
        assistant_text = (event.get("assistant_text") or "").strip()
        tool_summary = (event.get("tool_results_summary") or "").strip()
        parts: list[str] = []
        if assistant_text:
            parts.append("Assistant:\n" + assistant_text)
        if tool_summary:
            parts.append("Tool results:\n" + tool_summary)
        merged = "\n\n".join(parts).strip()
        return _truncate(merged, limit=self._config.max_observation_chars)

    def handle_event(self, *, session_id: str, event: dict[str, Any]) -> dict[str, Any]:
        started_at = _utc_now_iso()
        start_perf = time.perf_counter()

        def finalize_response(payload: dict[str, Any]) -> dict[str, Any]:
            payload["timing"] = {
                "started_at": started_at,
                "ended_at": _utc_now_iso(),
                "duration_ms": int((time.perf_counter() - start_perf) * 1000),
            }
            return payload

        session = self._ensure_session(session_id)

        event_type = str(event.get("type", "") or "").strip()
        self._record_event(session, event)

        actions: dict[str, Any] = {}

        if event_type == "before_agent_start":
            prompt = (event.get("prompt") or "").strip()
            if prompt and not session.goal:
                session.goal = prompt
                _write_json(session.run_dir / "session.json", session.snapshot())

        if event_type == "tool_call":
            tool_name = str(event.get("tool_name", "") or "").strip()
            if tool_name == "bash":
                input_obj = event.get("input") or {}
                command = ""
                if isinstance(input_obj, dict):
                    command = str(input_obj.get("command", "") or "")
                reason = _looks_dangerous_bash(command)
                if reason:
                    actions["block"] = True
                    actions["reason"] = reason

            return finalize_response({"ok": True, "actions": actions})

        if event_type == "turn_end":
            turn_index = event.get("turn_index")
            if isinstance(turn_index, int):
                session.last_turn_index = turn_index

            had_error = bool(event.get("had_error", False))

            compact_event = {
                "t": event.get("timestamp"),
                "type": "turn_end",
                "turn": turn_index,
                "had_error": had_error,
            }
            self._append_history(session, event=compact_event)

            should_steer = self._config.enable_lm and self._config.steer_policy != "off"
            if self._config.steer_policy == "on_error" and not had_error:
                should_steer = False

            if should_steer and self._consolidator and self._planner:
                observation = self._build_observation_from_turn_end(event)
                if observation:
                    consolidation = self._robust_predict(
                        session=session,
                        predictor=self._consolidator,
                        call_name="knowledge_consolidator",
                        goal=session.goal or "(unknown goal)",
                        observation=observation,
                        state=session.state,
                        history_events_json=self._format_history_json(session),
                        pinned_findings=session.pinned_findings,
                        rules=session.active_rules,
                        phase="analysis",
                        interaction_mode="human",
                    )
                    session.state = (getattr(consolidation, "state_updated", "") or "").strip()
                    session.active_rules = (getattr(consolidation, "active_rules", "") or "").strip()
                    session.open_questions = (getattr(consolidation, "open_questions", "") or "").strip()
                    session.assumptions = (getattr(consolidation, "assumptions", "") or "").strip()
                    state_delta = (getattr(consolidation, "state_delta", "") or "").strip()
                else:
                    state_delta = ""

                plan = self._robust_predict(
                    session=session,
                    predictor=self._planner,
                    call_name="decision_planner",
                    goal=session.goal or "(unknown goal)",
                    observation=observation or "(no new observation)",
                    state=session.state,
                    history_events_json=self._format_history_json(session),
                    pinned_findings=session.pinned_findings,
                    active_rules=session.active_rules,
                    phase="analysis",
                    interaction_mode="human",
                )
                plan_hint = (getattr(plan, "plan_hint", "") or "").strip()
                decision = (getattr(plan, "decision", "") or "").strip()

                steer_lines: list[str] = []
                if decision:
                    steer_lines.append(f"Manager decision: {decision}")
                if plan_hint:
                    steer_lines.append(f"Plan hint: {plan_hint}")
                if state_delta:
                    steer_lines.append("State delta:\n" + state_delta)
                steer_text = "\n\n".join(steer_lines).strip()
                steer_text = _truncate(steer_text, limit=self._config.max_steer_chars)

                if steer_text:
                    actions["steer"] = steer_text

            _write_json(session.run_dir / "session.json", session.snapshot())

        return finalize_response({"ok": True, "actions": actions})
