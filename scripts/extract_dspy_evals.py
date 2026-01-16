"""
Extract DSPy-style eval datasets from our SWE-bench run artifacts.

Reads:
  data/swebench_eval/**/instances/*/agent_run/steps/step_*/...

Writes:
  eval/<SignatureName>/examples.jsonl
  eval/manifest.json

Each JSONL line has:
  {
    "signature": "ActionSelectorSignature",
    "inputs": {...},
    "labels": {...},
    "meta": {...}
  }
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


JsonDict = dict[str, Any]


def _load_json(path: Path) -> JsonDict:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_load_json(path: Path) -> JsonDict | None:
    try:
        return _load_json(path)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _iter_run_dirs(root: Path) -> Iterable[Path]:
    # Expecting:
    #   data/swebench_eval/<run_timestamp>/
    # But allow a user to pass either "data/swebench_eval" or a single run dir.
    if (root / "instances").exists():
        yield root
        return

    if not root.exists():
        return

    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "instances").exists():
            yield child


def _iter_instance_dirs(run_dir: Path) -> Iterable[Path]:
    instances_dir = run_dir / "instances"
    if not instances_dir.exists():
        return
    for inst in sorted(instances_dir.iterdir()):
        if inst.is_dir():
            yield inst


def _iter_step_dirs(agent_run_dir: Path) -> Iterable[Path]:
    steps_dir = agent_run_dir / "steps"
    if not steps_dir.exists():
        return
    for step_dir in sorted(steps_dir.glob("step_*")):
        if step_dir.is_dir():
            yield step_dir


def _phase_and_interaction_mode(step_dir: Path) -> tuple[str, str]:
    consolidator_in = _safe_load_json(step_dir / "consolidator_input.json") or {}
    phase = str(consolidator_in.get("phase", "") or "")
    interaction_mode = str(consolidator_in.get("interaction_mode", "") or "")
    return phase, interaction_mode


def _action_pack_to_planner_selector_inputs(action_pack: JsonDict, *, phase: str, interaction_mode: str) -> JsonDict:
    # DecisionPlannerSignature / ActionSelectorSignature inputs
    return {
        "goal": action_pack.get("GOAL", "") or "",
        "observation": action_pack.get("OBSERVATION", "") or "",
        "state": action_pack.get("STATE", "") or "",
        "history_events_json": action_pack.get("HISTORY_EVENTS", "[]") or "[]",
        "pinned_findings": action_pack.get("PINNED_FINDINGS", "") or "",
        "active_rules": action_pack.get("ACTIVE_RULES", "") or "",
        "phase": phase,
        "interaction_mode": interaction_mode,
    }


@dataclass(frozen=True)
class SignatureExtractor:
    signature_name: str
    extract: Callable[[Path], tuple[JsonDict, JsonDict] | None]  # (inputs, labels)


def _extract_knowledge_consolidator(step_dir: Path) -> tuple[JsonDict, JsonDict] | None:
    inp = _safe_load_json(step_dir / "consolidator_input.json")
    out = _safe_load_json(step_dir / "consolidator_output.json")
    if not inp or not out:
        return None

    labels = {
        "state_delta": out.get("STATE_DELTA", "") or "",
        "state_updated": out.get("STATE", "") or "",
        "active_rules": out.get("ACTIVE_RULES", "") or "",
        "open_questions": out.get("OPEN_QUESTIONS", "") or "",
        "assumptions": out.get("ASSUMPTIONS", "") or "",
    }
    return inp, labels


def _extract_decision_planner(step_dir: Path) -> tuple[JsonDict, JsonDict] | None:
    action_pack = _safe_load_json(step_dir / "action_pack.json")
    out = _safe_load_json(step_dir / "decision_planner_output.json")
    if not action_pack or not out:
        return None
    phase, interaction_mode = _phase_and_interaction_mode(step_dir)
    inputs = _action_pack_to_planner_selector_inputs(action_pack, phase=phase, interaction_mode=interaction_mode)
    labels = {
        "plan_hint": out.get("PLAN_HINT", "") or "",
        "decision": out.get("DECISION", "") or "",
    }
    return inputs, labels


def _extract_action_selector(step_dir: Path) -> tuple[JsonDict, JsonDict] | None:
    action_pack = _safe_load_json(step_dir / "action_pack.json")
    out = _safe_load_json(step_dir / "action_selector_output.json")
    if not action_pack or not out:
        return None
    phase, interaction_mode = _phase_and_interaction_mode(step_dir)
    inputs = _action_pack_to_planner_selector_inputs(action_pack, phase=phase, interaction_mode=interaction_mode)
    labels = {
        "action_type": out.get("action_type", "") or "",
        "task": out.get("TASK", "") or "",
        "success_criteria": out.get("SUCCESS_CRITERIA", "") or "",
        "command": out.get("command", "") or "",
        # This *is* logged; useful for optimizing this output too.
        "command_intent": out.get("command_intent", "") or "",
    }
    return inputs, labels


def _extract_outcome_evaluator(step_dir: Path) -> tuple[JsonDict, JsonDict] | None:
    inp = _safe_load_json(step_dir / "outcome_input.json")
    out = _safe_load_json(step_dir / "outcome_output.json")
    if not inp or not out:
        return None
    labels = {
        "observation": out.get("observation", "") or "",
        "artifacts": out.get("artifacts", "") or "",
        "state_delta": out.get("state_delta", "") or "",
        "success": out.get("success", "") or "",
        "issues": out.get("issues", "") or "",
        "needs_verification": out.get("needs_verification", "") or "",
    }
    return inp, labels


def _extract_evidence_prefilter(step_dir: Path) -> tuple[JsonDict, JsonDict] | None:
    inp = _safe_load_json(step_dir / "evidence_prefilter_input.json")
    out = _safe_load_json(step_dir / "evidence_prefilter_output.json")
    if not inp or not out:
        return None
    labels = {
        "evidence_summary": out.get("evidence_summary", "") or "",
        "error_type": out.get("error_type", "") or "",
        "error_excerpt": out.get("error_excerpt", "") or "",
    }
    return inp, labels


EXTRACTORS: list[SignatureExtractor] = [
    SignatureExtractor("KnowledgeConsolidatorSignature", _extract_knowledge_consolidator),
    SignatureExtractor("DecisionPlannerSignature", _extract_decision_planner),
    SignatureExtractor("ActionSelectorSignature", _extract_action_selector),
    SignatureExtractor("OutcomeEvaluatorSignature", _extract_outcome_evaluator),
    SignatureExtractor("EvidencePrefilterSignature", _extract_evidence_prefilter),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/swebench_eval"))
    parser.add_argument("--out", type=Path, default=Path("eval"))
    parser.add_argument("--max-runs", type=int, default=0, help="0 = no limit")
    parser.add_argument("--max-instances", type=int, default=0, help="0 = no limit")
    parser.add_argument("--max-steps", type=int, default=0, help="0 = no limit per instance")
    parser.add_argument("--append", action="store_true", help="Append to existing JSONL (default overwrites).")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report counts only.")
    args = parser.parse_args(argv)

    out_root: Path = args.out
    out_root.mkdir(parents=True, exist_ok=True)

    # One file handle per signature for speed.
    writers: dict[str, Any] = {}
    counts: dict[str, int] = {ex.signature_name: 0 for ex in EXTRACTORS}
    skipped_steps: int = 0
    scanned_steps: int = 0

    try:
        if not args.dry_run:
            for ex in EXTRACTORS:
                sig_dir = out_root / ex.signature_name
                sig_dir.mkdir(parents=True, exist_ok=True)
                mode = "a" if args.append else "w"
                writers[ex.signature_name] = (sig_dir / "examples.jsonl").open(mode, encoding="utf-8")

        run_dirs = list(_iter_run_dirs(args.root))
        if args.max_runs and len(run_dirs) > args.max_runs:
            run_dirs = run_dirs[: args.max_runs]

        for run_dir in run_dirs:
            instance_dirs = list(_iter_instance_dirs(run_dir))
            if args.max_instances and len(instance_dirs) > args.max_instances:
                instance_dirs = instance_dirs[: args.max_instances]

            for inst_dir in instance_dirs:
                agent_run_dir = inst_dir / "agent_run"
                if not agent_run_dir.exists():
                    continue

                step_dirs = list(_iter_step_dirs(agent_run_dir))
                if args.max_steps and len(step_dirs) > args.max_steps:
                    step_dirs = step_dirs[: args.max_steps]

                for step_dir in step_dirs:
                    scanned_steps += 1
                    wrote_any = False
                    for ex in EXTRACTORS:
                        pair = ex.extract(step_dir)
                        if not pair:
                            continue
                        inputs, labels = pair
                        counts[ex.signature_name] += 1
                        wrote_any = True
                        if args.dry_run:
                            continue
                        record = {
                            "signature": ex.signature_name,
                            "inputs": inputs,
                            "labels": labels,
                            "meta": {
                                "run": run_dir.name,
                                "instance": inst_dir.name,
                                "step": step_dir.name,
                                "source_step_dir": str(step_dir),
                            },
                        }
                        writers[ex.signature_name].write(json.dumps(record, ensure_ascii=False) + "\n")

                    if not wrote_any:
                        skipped_steps += 1

        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "root": str(args.root),
            "out": str(out_root),
            "counts": counts,
            "scanned_steps": scanned_steps,
            "skipped_steps": skipped_steps,
            "signatures": [ex.signature_name for ex in EXTRACTORS],
        }
        if not args.dry_run:
            _write_json(out_root / "manifest.json", manifest)
        else:
            print(json.dumps(manifest, indent=2, sort_keys=True))

    finally:
        for f in writers.values():
            try:
                f.close()
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

