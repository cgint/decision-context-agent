from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, List


ASSIGNMENT_HEADER_ORDER = [
    "ASSIGNMENT_ID",
    "TASK",
    "SUCCESS_CRITERIA",
    "OVERALL_GOAL",
    "CONTEXT",
    "CONTEXT_BUDGET",
    "STATE",
    "HISTORY",
    "CONSTRAINTS",
    "DEGREES_OF_FREEDOM",
    "BUDGET",
    "DELIVERABLES",
]

RESULT_HEADER_ORDER = [
    "ASSIGNMENT_ID",
    "RESULT",
    "EVIDENCE",
    "STATE_DELTA",
    "CHANGES",
    "ARTIFACTS",
    "RISKS",
    "OPEN_QUESTIONS",
    "NEXT_OPTIONS",
]

_RESULT_HEADER_KEYS = set(RESULT_HEADER_ORDER)
_HEADER_PATTERN = re.compile(r"^([A-Z_]+):\s*(.*)$")
_HEADER_MD_PATTERN = re.compile(r"^#{1,6}\s+([A-Z_]+)\s*$")
_VALID_RESULTS = {"success", "partial", "fail"}


def _parse_markdown_blocks(lines: List[str]) -> Dict[str, str]:
    blocks: Dict[str, str] = {}
    current_key: str | None = None
    for line in lines:
        header_match = _HEADER_MD_PATTERN.match(line.strip())
        if header_match:
            key = header_match.group(1)
            if key in _RESULT_HEADER_KEYS:
                current_key = key
                if key not in blocks:
                    blocks[key] = ""
                continue
            current_key = None
        if current_key:
            value = line.rstrip()
            if value:
                blocks[current_key] = (blocks[current_key] + "\n" + value).strip()
    return blocks


def _extract_evidence_from_text(text: str) -> str:
    if not text:
        return ""
    evidence_lines: List[str] = []
    path_re = re.compile(r"([\w./-]+\.[\w]+(?::\d+(?:-\d+)?)?)")
    for line in text.splitlines():
        value = line.strip()
        if not value:
            continue
        if path_re.search(value) or "`" in value:
            evidence_lines.append(value)
        if len(evidence_lines) >= 3:
            break
    return "\n".join(evidence_lines).strip()


def _maybe_line(key: str, value: str) -> str:
    value = (value or "").strip()
    if value:
        return f"{key}: {value}"
    return ""


@dataclass(frozen=True)
class Assignment:
    assignment_id: str
    task: str
    success_criteria: str
    overall_goal: str = ""
    context: str = ""
    context_budget: str = ""
    state: str = ""
    history: str = ""
    constraints: str = ""
    degrees_of_freedom: str = ""
    budget: str = ""
    deliverables: str = ""
    body: str = ""

    def to_text(self) -> str:
        lines: List[str] = []
        for key in ASSIGNMENT_HEADER_ORDER:
            value = getattr(self, key.lower(), "")
            line = _maybe_line(key, value)
            if line:
                lines.append(line)
        if self.body.strip():
            lines.append("")
            lines.append(self.body.strip())
        return "\n".join(lines).rstrip() + "\n"


@dataclass(frozen=True)
class Result:
    assignment_id: str
    result: str
    evidence: str
    state_delta: str
    changes: str = ""
    artifacts: str = ""
    risks: str = ""
    open_questions: str = ""
    next_options: str = ""
    body: str = ""


@dataclass(frozen=True)
class ParsedResult:
    result: Result
    errors: List[str]
    raw_fields: Dict[str, str]


def parse_result(text: str, fallback_assignment_id: str = "") -> ParsedResult:
    lines = (text or "").splitlines()
    fields: Dict[str, str] = {}
    body_lines: List[str] = []
    current_key: str | None = None
    in_headers = True
    saw_header = False

    for line in lines:
        match = _HEADER_PATTERN.match(line)
        if in_headers and match:
            key = match.group(1)
            if key in _RESULT_HEADER_KEYS:
                fields[key] = match.group(2).strip()
                current_key = key
                saw_header = True
                continue
        if in_headers:
            if saw_header and not line.strip():
                in_headers = False
                current_key = None
                continue
            if current_key:
                continuation = line.rstrip()
                if continuation:
                    fields[current_key] = (
                        fields[current_key] + "\n" + continuation
                    ).strip()
                continue
            in_headers = False
        body_lines.append(line)

    if "RESULT" not in fields:
        md_blocks = _parse_markdown_blocks(lines)
        for key, value in md_blocks.items():
            fields.setdefault(key, value)

    assignment_id = fields.get("ASSIGNMENT_ID", "").strip() or fallback_assignment_id
    result_value = (fields.get("RESULT", "") or "").strip()
    normalized_result = result_value.lower()
    if normalized_result in _VALID_RESULTS:
        result_value = normalized_result

    evidence_value = (fields.get("EVIDENCE", "") or "").strip()
    body_text = "\n".join(body_lines).strip()
    if not evidence_value:
        evidence_value = _extract_evidence_from_text(body_text)

    parsed = Result(
        assignment_id=assignment_id,
        result=result_value,
        evidence=evidence_value,
        state_delta=(fields.get("STATE_DELTA", "") or "").strip(),
        changes=(fields.get("CHANGES", "") or "").strip(),
        artifacts=(fields.get("ARTIFACTS", "") or "").strip(),
        risks=(fields.get("RISKS", "") or "").strip(),
        open_questions=(fields.get("OPEN_QUESTIONS", "") or "").strip(),
        next_options=(fields.get("NEXT_OPTIONS", "") or "").strip(),
        body=body_text,
    )

    errors: List[str] = []
    if not parsed.assignment_id:
        errors.append("missing ASSIGNMENT_ID")
    if not parsed.result:
        errors.append("missing RESULT")
    if not parsed.evidence:
        errors.append("missing EVIDENCE")
    if not parsed.state_delta:
        errors.append("missing STATE_DELTA")
    if parsed.result and parsed.result not in _VALID_RESULTS:
        errors.append("invalid RESULT (expected success/partial/fail)")

    return ParsedResult(result=parsed, errors=errors, raw_fields=fields)


def render_result_observation(result: Result) -> str:
    lines = [
        _maybe_line("RESULT", result.result),
        _maybe_line("EVIDENCE", result.evidence),
        _maybe_line("STATE_DELTA", result.state_delta),
        _maybe_line("CHANGES", result.changes),
        _maybe_line("ARTIFACTS", result.artifacts),
        _maybe_line("RISKS", result.risks),
        _maybe_line("OPEN_QUESTIONS", result.open_questions),
        _maybe_line("NEXT_OPTIONS", result.next_options),
    ]
    return "\n".join([line for line in lines if line]).strip()
