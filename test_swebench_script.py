#!/usr/bin/env python3
"""Quick test to verify the SWE-bench eval script imports and basic structure."""

import sys
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

def test_imports():
    """Test that all imports work."""
    print("Testing imports...")
    try:
        import run_swebench_eval as swe
        print("✓ Script imports successfully")
        
        # Check main functions exist
        assert hasattr(swe, 'main')
        assert hasattr(swe, 'setup_workspace')
        assert hasattr(swe, 'run_agent')
        assert hasattr(swe, 'run_tests')
        assert hasattr(swe, 'evaluate_instance')
        assert hasattr(swe, 'load_swebench_lite')
        assert hasattr(swe, 'print_summary')
        print("✓ All required functions exist")
        
        # Check dataclasses
        assert hasattr(swe, 'InstanceResult')
        assert hasattr(swe, 'EvaluationConfig')
        print("✓ All dataclasses defined")
        
        return True
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False

def test_help():
    """Test that help works."""
    print("\nTesting --help...")
    import subprocess
    result = subprocess.run(
        ["python3", "run_swebench_eval.py", "--help"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and "SWE-bench" in result.stdout:
        print("✓ Help output works")
        return True
    else:
        print(f"✗ Help failed: {result.stderr}")
        return False

if __name__ == "__main__":
    print("=" * 80)
    print("SWE-bench Evaluation Script Test")
    print("=" * 80)
    
    all_passed = True
    all_passed &= test_imports()
    all_passed &= test_help()
    
    print("\n" + "=" * 80)
    if all_passed:
        print("All tests PASSED ✓")
        sys.exit(0)
    else:
        print("Some tests FAILED ✗")
        sys.exit(1)
