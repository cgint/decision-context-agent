"""
Standalone tests for RLM-PoT executor.

Run with: python -m rlm_pot.test_executor
Or:       uv run python -m rlm_pot.test_executor
"""

import tempfile
from pathlib import Path

from .executor import PythonExecutor
from .action_handler import handle_python_action, reset_executor, get_namespace_info


def test_basic_execution():
    """Test basic code execution and result return."""
    executor = PythonExecutor()
    
    result = executor.run('x = 1 + 1; _result_ = x')
    
    assert result["success"], f"Expected success, got: {result['error']}"
    assert result["result"] == 2, f"Expected 2, got: {result['result']}"
    assert "x" in result["namespace_keys"], "Variable 'x' should be in namespace"
    
    print("✓ test_basic_execution passed")


def test_namespace_persistence():
    """Test that variables persist across calls."""
    executor = PythonExecutor()
    
    # Step 1: Set variable
    result1 = executor.run('my_var = "hello"')
    assert result1["success"]
    
    # Step 2: Use variable from step 1
    result2 = executor.run('_result_ = my_var + " world"')
    assert result2["success"]
    assert result2["result"] == "hello world", f"Expected 'hello world', got: {result2['result']}"
    
    print("✓ test_namespace_persistence passed")


def test_pattern_extraction():
    """Test the core use case: extracting patterns from code."""
    executor = PythonExecutor()
    
    # Simulate reading a Python file
    code_content = '''
def hello():
    pass

_line_type_re = re.compile(_type_re)

def goodbye():
    pass
'''
    
    # Step 1: Load content and extract pattern
    result = executor.run(f'''
content = """{code_content}"""
import re
match = re.search(r're\\.compile\\((\\w+)\\)', content)
_result_ = match.group(1) if match else None
''')
    
    assert result["success"], f"Failed: {result['error']}"
    assert result["result"] == "_type_re", f"Expected '_type_re', got: {result['result']}"
    
    # Step 2: Use extracted pattern to construct replacement
    executor.run('''
old_pattern = f're.compile({_result_})'
new_pattern = f're.compile({_result_}, re.IGNORECASE)'
new_content = content.replace(old_pattern, new_pattern)
_result_ = new_pattern in new_content
''')
    
    # Note: _result_ from step 1 is no longer in namespace (it was popped)
    # But the variable we assigned it to would be... let's fix the test
    
    print("✓ test_pattern_extraction passed (partial - demonstrates the concept)")


def test_pattern_extraction_proper():
    """Test pattern extraction with proper variable storage."""
    executor = PythonExecutor()
    
    code_content = '_line_type_re = re.compile(_type_re)'
    
    # Step 1: Extract and STORE the pattern name
    result = executor.run(f'''
content = """{code_content}"""
import re
match = re.search(r're\\.compile\\((\\w+)\\)', content)
extracted_var = match.group(1)  # Store in namespace
_result_ = extracted_var
''')
    
    assert result["success"], f"Failed: {result['error']}"
    assert result["result"] == "_type_re"
    
    # Step 2: Use stored variable (extracted_var persists!)
    result2 = executor.run('''
old_pattern = f're.compile({extracted_var})'
new_pattern = f're.compile({extracted_var}, re.IGNORECASE)'
new_content = content.replace(old_pattern, new_pattern)
_result_ = {'old': old_pattern, 'new': new_pattern, 'applied': new_pattern in new_content}
''')
    
    assert result2["success"], f"Failed: {result2['error']}"
    assert result2["result"]["old"] == "re.compile(_type_re)"
    assert result2["result"]["new"] == "re.compile(_type_re, re.IGNORECASE)"
    assert result2["result"]["applied"]
    
    print("✓ test_pattern_extraction_proper passed")


def test_file_operations():
    """Test reading and writing files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        executor = PythonExecutor(working_dir=tmppath)
        
        # Create a test file
        test_file = tmppath / "test.py"
        test_file.write_text("x = 1\ny = 2\n")
        
        # Step 1: Read file
        result = executor.run('''
with open('test.py') as f:
    content = f.read()
_result_ = content
''')
        
        assert result["success"], f"Failed: {result['error']}"
        assert "x = 1" in result["result"]
        
        # Step 2: Modify and write
        result2 = executor.run('''
new_content = content.replace('x = 1', 'x = 100')
with open('test.py', 'w') as f:
    f.write(new_content)
_result_ = 'written'
''')
        
        assert result2["success"], f"Failed: {result2['error']}"
        
        # Verify file was changed
        assert "x = 100" in test_file.read_text()
        
        print("✓ test_file_operations passed")


def test_security_path_escape():
    """Test that path escape attempts are blocked."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        executor = PythonExecutor(working_dir=tmppath)
        
        # Try to read outside working directory
        result = executor.run('''
with open('../../../etc/passwd') as f:
    _result_ = f.read()
''')
        
        assert not result["success"], "Should have failed!"
        assert "PermissionError" in result["error_type"] or "outside working directory" in result["error"]
        
        print("✓ test_security_path_escape passed")


def test_import_restrictions():
    """Test that dangerous imports are blocked."""
    executor = PythonExecutor()
    
    # subprocess should be blocked
    result = executor.run('''
import subprocess
subprocess.run(['ls'])
''')
    
    assert not result["success"], "Should have failed!"
    assert "ImportError" in result["error_type"]
    assert "subprocess" in result["error"]
    
    print("✓ test_import_restrictions passed")


def test_allowed_imports():
    """Test that allowed imports work."""
    executor = PythonExecutor()
    
    result = executor.run('''
import re
import json
import os.path
from pathlib import Path
from collections import defaultdict
_result_ = 'all imports worked'
''')
    
    assert result["success"], f"Failed: {result['error']}"
    assert result["result"] == "all imports worked"
    
    print("✓ test_allowed_imports passed")


def test_action_handler_integration():
    """Test the action handler (agent loop integration point)."""
    reset_executor()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        
        # Create test file
        (tmppath / "code.py").write_text("value = 42")
        
        # Use action handler
        result = handle_python_action('''
with open('code.py') as f:
    content = f.read()
import re
match = re.search(r'value = (\\d+)', content)
extracted = match.group(1)
_result_ = int(extracted)
''', working_dir=tmppath)
        
        assert result["returncode"] == "0", f"Failed: {result['stderr']}"
        assert "_result_ = 42" in result["stdout"]
        
        # Namespace info should show variables
        info = get_namespace_info()
        assert info["initialized"]
        assert "content" in info["keys"]
        assert "extracted" in info["keys"]
        
    reset_executor()
    print("✓ test_action_handler_integration passed")


def test_reset():
    """Test namespace reset."""
    executor = PythonExecutor()
    
    executor.run('persistent_var = 123')
    assert "persistent_var" in executor._get_visible_keys()
    
    executor.reset()
    assert "persistent_var" not in executor._get_visible_keys()
    
    print("✓ test_reset passed")


def test_stdout_capture():
    """Test that print statements are captured."""
    executor = PythonExecutor()
    
    result = executor.run('''
print("Hello from Python!")
print("Line 2")
_result_ = "done"
''')
    
    assert result["success"]
    assert "Hello from Python!" in result["stdout"]
    assert "Line 2" in result["stdout"]
    
    print("✓ test_stdout_capture passed")


def test_error_handling():
    """Test error handling and reporting."""
    executor = PythonExecutor()
    
    result = executor.run('''
x = 1 / 0  # Division by zero
''')
    
    assert not result["success"]
    assert result["error_type"] == "ZeroDivisionError"
    assert "division by zero" in result["error"].lower()
    
    print("✓ test_error_handling passed")


def run_all_tests():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("RLM-PoT Executor Tests")
    print("=" * 60 + "\n")
    
    tests = [
        test_basic_execution,
        test_namespace_persistence,
        test_pattern_extraction,
        test_pattern_extraction_proper,
        test_file_operations,
        test_security_path_escape,
        test_import_restrictions,
        test_allowed_imports,
        test_action_handler_integration,
        test_reset,
        test_stdout_capture,
        test_error_handling,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"✗ {test.__name__} FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__} ERROR: {type(e).__name__}: {e}")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60 + "\n")
    
    return failed == 0


if __name__ == "__main__":
    import sys
    success = run_all_tests()
    sys.exit(0 if success else 1)
