"""
Action handler for integrating RLM-PoT with the agent loop.

This module provides the bridge between the agent's action selection and
the PythonExecutor. It handles:
- Lifecycle management (create/reset executor per run)
- Result formatting (matching shell command output format)
- Enable/disable toggle

Integration with online_replay_loop.py:
    
    # At top of file
    try:
        from rlm_pot import handle_python_action, reset_executor, is_enabled
        RLM_POT_ENABLED = is_enabled()
    except ImportError:
        RLM_POT_ENABLED = False
    
    # In action execution section (after shell_command handling)
    elif action_type == "python_edit" and RLM_POT_ENABLED:
        result = handle_python_action(code, working_dir=workspace_dir)
        stdout = result["stdout"]
        stderr = result["stderr"]
        returncode = result["returncode"]
"""

from pathlib import Path
from typing import Any, Dict, Optional

from .executor import PythonExecutor


# Module-level executor instance (persists across steps within a run)
_executor: Optional[PythonExecutor] = None
_enabled: bool = True  # Can be toggled via environment or config


def get_executor(working_dir: Optional[Path] = None) -> PythonExecutor:
    """
    Get or create the persistent executor.
    
    Args:
        working_dir: Working directory for file operations.
                     Only used when creating a new executor.
    """
    global _executor
    if _executor is None:
        _executor = PythonExecutor(working_dir=working_dir)
    return _executor


def reset_executor() -> None:
    """
    Reset the executor (clear namespace).
    
    Call this between agent runs to ensure fresh state.
    """
    global _executor
    if _executor is not None:
        _executor.reset()
    _executor = None  # Force recreation on next use


def is_enabled() -> bool:
    """Check if RLM-PoT module is enabled."""
    return _enabled


def set_enabled(enabled: bool) -> None:
    """Enable or disable RLM-PoT module."""
    global _enabled
    _enabled = enabled


def handle_python_action(
    code: str,
    working_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Handle a python_edit action from the agent.
    
    This is the main entry point for the agent loop to execute Python code.
    
    Args:
        code: Python code string from agent
        working_dir: Working directory for file operations
        
    Returns:
        Dict matching shell command result format:
        {
            "returncode": str,  # "0" for success, "1" for failure
            "stdout": str,      # Captured output + result info
            "stderr": str,      # Error message if failed
        }
    """
    executor = get_executor(working_dir)
    result = executor.run(code)
    
    # Format stdout to include result and namespace info
    stdout_parts = []
    
    if result["stdout"]:
        stdout_parts.append(result["stdout"])
    
    if result["result"] is not None:
        stdout_parts.append(f"\n_result_ = {result['result']!r}")
    
    if result["namespace_keys"]:
        stdout_parts.append(f"\n[namespace: {', '.join(result['namespace_keys'])}]")
    
    stdout = "".join(stdout_parts)
    
    return {
        "returncode": "0" if result["success"] else "1",
        "stdout": stdout,
        "stderr": result["error"] or "",
    }


def get_namespace_info() -> Dict[str, Any]:
    """
    Get information about current namespace state.
    
    Useful for debugging or showing agent what variables are available.
    """
    if _executor is None:
        return {"initialized": False, "variables": {}}
    
    return {
        "initialized": True,
        "variables": _executor.get_namespace_summary(),
        "keys": _executor._get_visible_keys(),
    }
