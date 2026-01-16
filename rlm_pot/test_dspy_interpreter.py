"""
Comparison tests: DSPy PythonInterpreter vs our rlm_pot Executor.

Run with: uv run python -m rlm_pot.test_dspy_interpreter

This file runs the same tests against both implementations to compare:
- API differences
- Persistence behavior
- Security features
- Performance
"""

import tempfile
import time
from pathlib import Path
from typing import Callable

# Check if DSPy is available
try:
    from dspy import PythonInterpreter as DspyInterpreter
    DSPY_AVAILABLE = True
except ImportError:
    DSPY_AVAILABLE = False
    print("WARNING: DSPy not available, skipping DSPy tests")

from .executor import PythonExecutor


class DspyWrapper:
    """
    Wrapper to make DSPy's PythonInterpreter API similar to our PythonExecutor.
    
    DSPy uses a context manager and returns stdout directly.
    Our executor returns a dict with success, result, stdout, error.
    """
    
    def __init__(self, working_dir: Path = None):
        self.working_dir = working_dir
        self._interp = None
        self._context = None
        
        # Configure paths for DSPy
        read_paths = [str(working_dir)] if working_dir else None
        write_paths = [str(working_dir)] if working_dir else None
        
        self._interp = DspyInterpreter(
            enable_read_paths=read_paths,
            enable_write_paths=write_paths,
            sync_files=True,
        )
        self._context = self._interp.__enter__()
    
    def run(self, code: str) -> dict:
        """Execute code and return result in our format."""
        try:
            stdout = self._interp(code)
            return {
                "success": True,
                "stdout": stdout or "",
                "result": None,  # DSPy doesn't have _result_ convention
                "error": None,
            }
        except Exception as e:
            return {
                "success": False,
                "stdout": "",
                "result": None,
                "error": str(e),
            }
    
    def close(self):
        """Clean up the interpreter."""
        if self._interp:
            self._interp.__exit__(None, None, None)
            self._interp = None


def run_test(name: str, test_fn: Callable, executor_name: str) -> tuple[bool, float, str]:
    """Run a test and return (passed, time_ms, error_msg)."""
    start = time.perf_counter()
    try:
        test_fn()
        elapsed = (time.perf_counter() - start) * 1000
        return True, elapsed, ""
    except AssertionError as e:
        elapsed = (time.perf_counter() - start) * 1000
        return False, elapsed, str(e)
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return False, elapsed, f"{type(e).__name__}: {e}"


# =============================================================================
# Test functions that work with both implementations
# =============================================================================

def test_basic_execution(executor):
    """Test basic code execution."""
    result = executor.run('x = 1 + 1; print(x)')
    assert result["success"], f"Expected success: {result['error']}"
    assert "2" in result["stdout"], f"Expected '2' in stdout: {result['stdout']}"


def test_namespace_persistence(executor):
    """Test that variables persist across calls."""
    result1 = executor.run('my_var = "hello"')
    assert result1["success"], f"Step 1 failed: {result1['error']}"
    
    result2 = executor.run('combined = my_var + " world"; print(combined)')
    assert result2["success"], f"Step 2 failed: {result2['error']}"
    assert "hello world" in result2["stdout"], f"Expected 'hello world': {result2['stdout']}"


def test_pattern_extraction(executor):
    """Test extracting patterns from code (the core RLM use case)."""
    code_content = '_line_type_re = re.compile(_type_re)'
    
    # Step 1: Load content and extract
    result1 = executor.run(f'''
content = """{code_content}"""
import re
match = re.search(r're\\.compile\\((\\w+)\\)', content)
extracted_var = match.group(1)
print(extracted_var)
''')
    assert result1["success"], f"Extraction failed: {result1['error']}"
    assert "_type_re" in result1["stdout"], f"Expected '_type_re': {result1['stdout']}"
    
    # Step 2: Use extracted variable (tests persistence)
    result2 = executor.run('''
old_pattern = f're.compile({extracted_var})'
new_pattern = f're.compile({extracted_var}, re.IGNORECASE)'
new_content = content.replace(old_pattern, new_pattern)
success = new_pattern in new_content
print(f"Applied: {success}")
''')
    assert result2["success"], f"Application failed: {result2['error']}"
    assert "Applied: True" in result2["stdout"], f"Expected success: {result2['stdout']}"


def test_file_operations(executor, tmpdir: Path):
    """Test reading and writing files."""
    test_file = tmpdir / "test.py"
    test_file.write_text("x = 1\ny = 2\n")
    
    # Read
    result1 = executor.run('''
with open('test.py') as f:
    content = f.read()
print(content)
''')
    assert result1["success"], f"Read failed: {result1['error']}"
    assert "x = 1" in result1["stdout"], f"Expected content: {result1['stdout']}"
    
    # Write
    result2 = executor.run('''
new_content = content.replace('x = 1', 'x = 100')
with open('test.py', 'w') as f:
    f.write(new_content)
print('written')
''')
    assert result2["success"], f"Write failed: {result2['error']}"
    
    # Verify file changed
    actual = test_file.read_text()
    assert "x = 100" in actual, f"File not modified: {actual}"


def test_import_re(executor):
    """Test that 're' module works."""
    result = executor.run('''
import re
match = re.search(r'(\\d+)', 'abc123def')
print(match.group(1))
''')
    assert result["success"], f"Failed: {result['error']}"
    assert "123" in result["stdout"], f"Expected '123': {result['stdout']}"


def test_import_json(executor):
    """Test that 'json' module works."""
    result = executor.run('''
import json
data = json.dumps({"key": "value"})
print(data)
''')
    assert result["success"], f"Failed: {result['error']}"
    assert "key" in result["stdout"], f"Expected key: {result['stdout']}"


def test_error_handling(executor):
    """Test error handling."""
    result = executor.run('x = 1 / 0')
    assert not result["success"], "Should have failed!"
    assert "ZeroDivisionError" in (result["error"] or ""), f"Expected ZeroDivisionError: {result}"


def test_multiline_string(executor):
    """Test handling multiline strings (common in code editing)."""
    result = executor.run('''
code = """
def hello():
    print("Hello, World!")
    
def goodbye():
    print("Goodbye!")
"""
lines = code.strip().split('\\n')
print(f"Lines: {len(lines)}")
''')
    assert result["success"], f"Failed: {result['error']}"
    assert "Lines:" in result["stdout"], f"Expected lines count: {result['stdout']}"


def test_list_comprehension(executor):
    """Test Python features like list comprehensions."""
    result = executor.run('''
numbers = [1, 2, 3, 4, 5]
squares = [n**2 for n in numbers]
print(squares)
''')
    assert result["success"], f"Failed: {result['error']}"
    assert "[1, 4, 9, 16, 25]" in result["stdout"], f"Expected squares: {result['stdout']}"


# =============================================================================
# Test runner
# =============================================================================

def run_comparison_tests():
    """Run all tests against both implementations and compare."""
    
    print("\n" + "=" * 70)
    print("DSPy PythonInterpreter vs rlm_pot PythonExecutor Comparison")
    print("=" * 70)
    
    # Tests that don't need file system
    basic_tests = [
        ("basic_execution", test_basic_execution),
        ("namespace_persistence", test_namespace_persistence),
        ("pattern_extraction", test_pattern_extraction),
        ("import_re", test_import_re),
        ("import_json", test_import_json),
        ("error_handling", test_error_handling),
        ("multiline_string", test_multiline_string),
        ("list_comprehension", test_list_comprehension),
    ]
    
    results = {"rlm_pot": {}, "dspy": {}}
    
    # Test rlm_pot
    print("\n--- Testing rlm_pot PythonExecutor ---\n")
    executor = PythonExecutor()
    for name, test_fn in basic_tests:
        passed, time_ms, error = run_test(name, lambda: test_fn(executor), "rlm_pot")
        results["rlm_pot"][name] = (passed, time_ms, error)
        status = "✓" if passed else "✗"
        print(f"  {status} {name}: {time_ms:.1f}ms" + (f" - {error}" if error else ""))
    
    # Test DSPy
    if DSPY_AVAILABLE:
        print("\n--- Testing DSPy PythonInterpreter ---\n")
        dspy_exec = DspyWrapper()
        try:
            for name, test_fn in basic_tests:
                passed, time_ms, error = run_test(name, lambda: test_fn(dspy_exec), "dspy")
                results["dspy"][name] = (passed, time_ms, error)
                status = "✓" if passed else "✗"
                print(f"  {status} {name}: {time_ms:.1f}ms" + (f" - {error}" if error else ""))
        finally:
            dspy_exec.close()
    
    # File operation tests (need temp directory)
    print("\n--- File Operation Tests ---\n")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        
        # rlm_pot
        executor_with_dir = PythonExecutor(working_dir=tmppath)
        passed, time_ms, error = run_test(
            "file_operations", 
            lambda: test_file_operations(executor_with_dir, tmppath),
            "rlm_pot"
        )
        results["rlm_pot"]["file_operations"] = (passed, time_ms, error)
        status = "✓" if passed else "✗"
        print(f"  rlm_pot: {status} file_operations: {time_ms:.1f}ms" + (f" - {error}" if error else ""))
    
    if DSPY_AVAILABLE:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            
            # DSPy - need to create file before initializing (it mounts at init)
            (tmppath / "test.py").write_text("x = 1\ny = 2\n")
            
            dspy_with_dir = DspyWrapper(working_dir=tmppath)
            try:
                passed, time_ms, error = run_test(
                    "file_operations",
                    lambda: test_file_operations(dspy_with_dir, tmppath),
                    "dspy"
                )
                results["dspy"]["file_operations"] = (passed, time_ms, error)
                status = "✓" if passed else "✗"
                print(f"  dspy:    {status} file_operations: {time_ms:.1f}ms" + (f" - {error}" if error else ""))
            finally:
                dspy_with_dir.close()
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    all_tests = list(results["rlm_pot"].keys())
    
    print(f"\n{'Test':<25} {'rlm_pot':<20} {'DSPy':<20}")
    print("-" * 65)
    
    rlm_passed = 0
    dspy_passed = 0
    rlm_total_time = 0
    dspy_total_time = 0
    
    for test_name in all_tests:
        rlm_result = results["rlm_pot"].get(test_name, (False, 0, "N/A"))
        dspy_result = results["dspy"].get(test_name, (False, 0, "N/A"))
        
        rlm_status = f"{'✓' if rlm_result[0] else '✗'} {rlm_result[1]:.1f}ms"
        dspy_status = f"{'✓' if dspy_result[0] else '✗'} {dspy_result[1]:.1f}ms" if DSPY_AVAILABLE else "N/A"
        
        print(f"{test_name:<25} {rlm_status:<20} {dspy_status:<20}")
        
        if rlm_result[0]:
            rlm_passed += 1
        rlm_total_time += rlm_result[1]
        
        if DSPY_AVAILABLE and dspy_result[0]:
            dspy_passed += 1
        if DSPY_AVAILABLE:
            dspy_total_time += dspy_result[1]
    
    print("-" * 65)
    print(f"{'TOTAL':<25} {rlm_passed}/{len(all_tests)} ({rlm_total_time:.0f}ms)".ljust(45) + 
          (f"{dspy_passed}/{len(all_tests)} ({dspy_total_time:.0f}ms)" if DSPY_AVAILABLE else "N/A"))
    
    print("\n" + "=" * 70)
    print("KEY OBSERVATIONS")
    print("=" * 70)
    print("""
1. API Differences:
   - rlm_pot: executor.run(code) returns dict with success, stdout, result, error
   - DSPy: interp(code) returns stdout directly, raises on error

2. Persistence:
   - Both support variable persistence within a session
   - rlm_pot: Explicit namespace management
   - DSPy: Implicit within context manager

3. Performance:
   - rlm_pot: Pure Python exec(), very fast
   - DSPy: Deno + Pyodide (WebAssembly), slower but more isolated

4. Security:
   - rlm_pot: Import whitelist + path restriction
   - DSPy: Full Deno sandbox + configurable permissions

5. Dependencies:
   - rlm_pot: None (pure Python)
   - DSPy: Requires Deno runtime
""")
    
    return all(r[0] for r in results["rlm_pot"].values())


if __name__ == "__main__":
    import sys
    success = run_comparison_tests()
    sys.exit(0 if success else 1)
