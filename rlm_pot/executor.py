"""
RLM-PoT Executor: Persistent Python namespace for agent code execution.

This module implements the "Context as Variable" pattern from RLM-PoT (Recursive Language
Model via Program of Thought). Instead of dumping file contents into the LLM's context,
data is loaded into Python variables that persist across agent steps.

Key benefits:
- Exact pattern extraction (no hallucination of variable names)
- Variables persist across steps (deterministic state)
- Python string operations instead of sed escaping
- Built-in verification via assertions

Usage:
    from rlm_pot import PythonExecutor
    
    executor = PythonExecutor()
    
    # Step 1: Load file into variable
    result = executor.run('''
    content = open('file.py').read()
    import re
    match = re.search(r'pattern', content)
    _result_ = match.group(1)  # Returns to agent
    ''')
    
    # Step 2: Use variable from step 1 (still in namespace!)
    result = executor.run('''
    new_content = content.replace(old_pattern, new_pattern)
    with open('file.py', 'w') as f:
        f.write(new_content)
    _result_ = 'done'
    ''')
"""

import io
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class PythonExecutor:
    """
    Sandboxed Python executor with persistent namespace.
    
    The namespace persists across calls, allowing the agent to:
    - Store file contents in variables
    - Extract patterns programmatically
    - Construct edits deterministically
    - Verify changes before/after writing
    """
    
    # Default allowed imports (safe for file manipulation)
    DEFAULT_ALLOWED_IMPORTS: Set[str] = {
        're',           # Regex for pattern matching
        'inspect',      # Docstring/text helpers (e.g. cleandoc)
        'os',           # Path operations
        'os.path',      # Path utilities
        'json',         # JSON parsing
        'pathlib',      # Modern path handling
        'collections',  # Data structures
        'functools',    # Function utilities
        'itertools',    # Iteration utilities
        'ast',          # AST parsing (for code analysis)
        'difflib',      # Diff generation
        'textwrap',     # Text formatting
        'copy',         # Deep/shallow copy
    }
    
    def __init__(
        self,
        allowed_imports: Optional[Set[str]] = None,
        working_dir: Optional[Path] = None,
        max_output_size: int = 50000,
    ):
        """
        Initialize the executor.
        
        Args:
            allowed_imports: Set of allowed module names. Defaults to safe file-ops modules.
            working_dir: Working directory for file operations. Defaults to cwd.
            max_output_size: Maximum stdout capture size in bytes.
        """
        self.allowed_imports = allowed_imports or self.DEFAULT_ALLOWED_IMPORTS.copy()
        self.working_dir = working_dir or Path.cwd()
        self.max_output_size = max_output_size
        self.namespace: Dict[str, Any] = {}
        self._setup_namespace()
    
    def _setup_namespace(self) -> None:
        """Initialize namespace with safe builtins and helpers."""
        # Restricted builtins (exclude dangerous ones like eval, exec, compile)
        safe_builtins = {
            # Types
            'bool': bool,
            'int': int,
            'float': float,
            'str': str,
            'bytes': bytes,
            'list': list,
            'tuple': tuple,
            'dict': dict,
            'set': set,
            'frozenset': frozenset,
            
            # Functions
            'len': len,
            'range': range,
            'enumerate': enumerate,
            'zip': zip,
            'map': map,
            'filter': filter,
            'sorted': sorted,
            'reversed': reversed,
            'min': min,
            'max': max,
            'sum': sum,
            'any': any,
            'all': all,
            'abs': abs,
            'round': round,
            'pow': pow,
            'divmod': divmod,
            
            # String/repr
            'repr': repr,
            'ascii': ascii,
            'chr': chr,
            'ord': ord,
            'format': format,
            
            # Object inspection
            'isinstance': isinstance,
            'issubclass': issubclass,
            'hasattr': hasattr,
            'getattr': getattr,
            'setattr': setattr,
            'delattr': delattr,
            'type': type,
            'id': id,
            'hash': hash,
            'callable': callable,
            'dir': dir,
            'vars': vars,
            
            # I/O (with controlled open)
            'print': print,
            'input': lambda *args: '',  # Disabled - always returns empty
            'open': self._safe_open,
            
            # Iteration
            'iter': iter,
            'next': next,
            
            # Exceptions
            'Exception': Exception,
            'ValueError': ValueError,
            'TypeError': TypeError,
            'KeyError': KeyError,
            'IndexError': IndexError,
            'AttributeError': AttributeError,
            'FileNotFoundError': FileNotFoundError,
            'IOError': IOError,
            'AssertionError': AssertionError,
            
            # Import helper
            '__import__': self._safe_import,
            
            # None/True/False
            'None': None,
            'True': True,
            'False': False,
        }
        
        self.namespace['__builtins__'] = safe_builtins
        
        # Add working directory reference
        self.namespace['__working_dir__'] = self.working_dir
    
    def _safe_import(self, name: str, globals_: Any = None, locals_: Any = None,
                     fromlist: tuple = (), level: int = 0) -> Any:
        """Import hook that only allows whitelisted modules."""
        base_module = name.split('.')[0]
        if base_module not in self.allowed_imports:
            raise ImportError(
                f"Import '{name}' not allowed. "
                f"Allowed modules: {sorted(self.allowed_imports)}"
            )
        return __import__(name, globals_, locals_, fromlist, level)
    
    def _safe_open(self, file: str, mode: str = 'r', *args, **kwargs) -> Any:
        """
        Controlled file open that resolves paths relative to working_dir.
        
        Security: Only allows access within working_dir subtree.
        """
        path = Path(file)
        if not path.is_absolute():
            path = self.working_dir / path
        
        # Resolve to catch ../ escapes
        resolved = path.resolve()
        working_resolved = self.working_dir.resolve()
        
        # Security check: must be within working_dir
        try:
            resolved.relative_to(working_resolved)
        except ValueError:
            raise PermissionError(
                f"Access denied: '{file}' is outside working directory '{self.working_dir}'"
            )
        
        return open(resolved, mode, *args, **kwargs)
    
    def run(self, code: str) -> Dict[str, Any]:
        """
        Execute code in the persistent namespace.
        
        The agent can set `_result_` to return a structured value.
        
        Args:
            code: Python code to execute
            
        Returns:
            {
                "success": bool,
                "stdout": str,           # Captured print output
                "result": Any,           # Value of _result_ if set
                "error": str | None,     # Error message if failed
                "error_type": str | None,# Exception type name
                "namespace_keys": list,  # Current variables (excluding private)
            }
        """
        # Capture stdout
        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        
        try:
            # Execute in namespace
            exec(code, self.namespace)
            
            # Get stdout (truncate if too large)
            stdout = buffer.getvalue()
            if len(stdout) > self.max_output_size:
                stdout = stdout[:self.max_output_size] + f"\n... [truncated at {self.max_output_size} bytes]"
            
            # Pop _result_ if set (so it doesn't persist)
            result = self.namespace.pop('_result_', None)
            
            return {
                "success": True,
                "stdout": stdout,
                "result": result,
                "error": None,
                "error_type": None,
                "namespace_keys": self._get_visible_keys(),
            }
            
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            tb = traceback.format_exc()
            
            return {
                "success": False,
                "stdout": buffer.getvalue(),
                "result": None,
                "error": f"{error_type}: {error_msg}\n{tb}",
                "error_type": error_type,
                "namespace_keys": self._get_visible_keys(),
            }
            
        finally:
            sys.stdout = old_stdout
    
    def _get_visible_keys(self) -> List[str]:
        """Get namespace keys excluding private/dunder names."""
        return sorted([
            k for k in self.namespace.keys()
            if not k.startswith('_')
        ])
    
    def get(self, name: str) -> Any:
        """Get a variable from the namespace."""
        return self.namespace.get(name)
    
    def set(self, name: str, value: Any) -> None:
        """Set a variable in the namespace."""
        self.namespace[name] = value
    
    def delete(self, name: str) -> bool:
        """Delete a variable from the namespace. Returns True if existed."""
        if name in self.namespace:
            del self.namespace[name]
            return True
        return False
    
    def reset(self) -> None:
        """Clear namespace and reinitialize (fresh start)."""
        self.namespace.clear()
        self._setup_namespace()
    
    def get_namespace_summary(self) -> Dict[str, str]:
        """Get a summary of current namespace (variable names and types)."""
        return {
            k: type(v).__name__
            for k, v in self.namespace.items()
            if not k.startswith('_')
        }
