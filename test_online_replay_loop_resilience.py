"""
Targeted regression tests for online_replay_loop resilience guardrails.

Run with:
  uv run python test_online_replay_loop_resilience.py
"""

import json

from online_replay_loop import apply_command_policy, sanitize_noninteractive_command


def test_loop_breaker_old_text_not_found():
    history = [
        {
            "step": 1,
            "action_type": "edit",
            "intent": "edit",
            "ok": False,
            "error_excerpt": "FAILURE: Could not find old_text in file (even after normalization).",
        },
        {
            "step": 2,
            "action_type": "shell_command",
            "intent": "read",
            "ok": True,
        },
        {
            "step": 3,
            "action_type": "edit",
            "intent": "edit",
            "ok": False,
            "error_excerpt": "old_text not found in separable.py (312 lines).",
        },
    ]
    history_events_json = json.dumps(history)
    edit_command = json.dumps(
        {
            "path": "src/example.py",
            "old_text": "elif isinstance(transform, CompoundModel):\n    return something()",
            "new_text": "elif isinstance(transform, CompoundModel):\n    return other()",
        }
    )

    out = apply_command_policy(
        step=4,
        goal="Fix bug",
        observation="",
        history_events_json=history_events_json,
        proposed_task="Edit file",
        proposed_success_criteria="Edit applied",
        action_type="edit",
        command=edit_command,
        command_intent="edit",
    )

    assert out["action_type"] == "shell_command"
    assert out["command_intent"] == "read"
    assert "rg -n" in out["command"]
    assert "src/example.py" in out["command"]


def test_loop_breaker_uses_stable_identifiers_for_locate():
    # Regression: if the first line of old_text is a guessed control-flow header (e.g. "if ...:"),
    # the locate command should prefer stable identifiers (e.g. function names) so the search returns results.
    history = [
        {
            "step": 1,
            "action_type": "edit",
            "intent": "edit",
            "ok": False,
            "error_excerpt": "FAILURE: Could not find old_text in file (even after normalization).",
        },
        {
            "step": 2,
            "action_type": "edit",
            "intent": "edit",
            "ok": False,
            "error_excerpt": "old_text not found in file.",
        },
    ]
    history_events_json = json.dumps(history)
    edit_command = json.dumps(
        {
            "path": "src/example.py",
            "old_text": "if isinstance(model, CompoundModel):\n    cleft = _coord_matrix(model.left, pos, model.left.n_inputs)\n",
            "new_text": "noop",
        }
    )

    out = apply_command_policy(
        step=3,
        goal="Fix bug",
        observation="",
        history_events_json=history_events_json,
        proposed_task="Edit file",
        proposed_success_criteria="Edit applied",
        action_type="edit",
        command=edit_command,
        command_intent="edit",
    )

    assert out["action_type"] == "shell_command"
    assert out["command_intent"] == "read"
    assert "rg -n" in out["command"]
    # Prefer stable identifiers over a likely-missing control-flow header.
    assert "_coord_matrix" in out["command"]
    assert "if isinstance(model, CompoundModel):" not in out["command"]


def test_sanitize_partial_function_read():
    cmd = "sed -n '/def foo/,/return/p' my_file.py"
    rewritten, reason = sanitize_noninteractive_command(cmd)
    assert "sanitized_partial_function_read" in reason
    assert "sed -n '/^def foo/,/^def /p' my_file.py" == rewritten


def main() -> None:
    test_loop_breaker_old_text_not_found()
    print("✓ test_loop_breaker_old_text_not_found passed")
    test_loop_breaker_uses_stable_identifiers_for_locate()
    print("✓ test_loop_breaker_uses_stable_identifiers_for_locate passed")
    test_sanitize_partial_function_read()
    print("✓ test_sanitize_partial_function_read passed")


if __name__ == "__main__":
    main()

