"""
RLM-PoT: Recursive Language Model via Program of Thought

A pluggable module that enables agents to execute Python code with a persistent
namespace, implementing the "Context as Variable" pattern.

Key features:
- Persistent Python namespace across agent steps
- Safe execution with controlled imports and file access
- Deterministic variable management (no hallucination of patterns)
- Easy integration with existing agent loops

Quick start:
    from rlm_pot import handle_python_action, reset_executor
    
    # Execute Python code from agent
    result = handle_python_action('''
    content = open('file.py').read()
    import re
    match = re.search(r'def (\\w+)', content)
    _result_ = match.group(1)
    ''', working_dir=workspace_path)
    
    # Result format matches shell command output
    # result["returncode"] = "0" or "1"
    # result["stdout"] = captured output + _result_
    # result["stderr"] = error message if failed
    
    # Reset between runs
    reset_executor()

For standalone usage (testing, scripting):
    from rlm_pot import PythonExecutor
    
    executor = PythonExecutor(working_dir=Path('/path/to/workspace'))
    result = executor.run('x = 1 + 1; _result_ = x')
    print(result["result"])  # 2
"""

from .executor import PythonExecutor
from .action_handler import (
    handle_python_action,
    reset_executor,
    get_executor,
    is_enabled,
    set_enabled,
    get_namespace_info,
)

__all__ = [
    # Core class
    'PythonExecutor',
    
    # Action handler functions (for agent loop integration)
    'handle_python_action',
    'reset_executor',
    'get_executor',
    'is_enabled',
    'set_enabled',
    'get_namespace_info',
]

__version__ = '0.1.0'
