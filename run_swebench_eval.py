#!/usr/bin/env python3
"""
SWE-bench Lite evaluation script for the online replay loop.

This script supports two evaluation modes:

1. LOCAL MODE (--evaluation-mode local):
   - Quick local testing without Docker
   - Creates per-instance venvs for isolation
   - May have environment-specific issues (see SWEBENCH_LOCAL_EVAL_FINDINGS.md)

2. DOCKER MODE (--evaluation-mode docker) [GOLD STANDARD]:
   - Uses official SWE-bench harness with Docker
   - Reproducible results matching published benchmarks
   - Requires Docker to be installed and running

The script:
1. Downloads SWE-bench Lite dataset
2. Runs online_replay_loop.py on N instances
3. Extracts patches produced by the agent
4. Evaluates using chosen mode (local tests or Docker harness)
5. Reports success/failure for each instance
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

# Thread-safe print lock for concurrent output
_print_lock = threading.Lock()


def prefixed_print(prefix: str, msg: str) -> None:
    """Thread-safe print with instance prefix."""
    with _print_lock:
        for line in msg.split("\n"):
            print(f"{prefix} {line}")


def extract_failure_info(
    instance_output_dir: Path,
    agent_run_dir: Optional[str],
    max_steps: int,
) -> str:
    """
    Extract useful debugging info from a failed agent run.

    Returns a formatted string with error details, progress, and context.
    """
    lines = []

    # 1. Parse error from agent_stderr.txt
    stderr_file = instance_output_dir / "agent_stderr.txt"
    error_type = ""
    error_msg = ""
    if stderr_file.exists():
        stderr = stderr_file.read_text()
        stderr_lines = stderr.strip().split("\n")

        # Find the last exception - look for lines that look like Python exceptions
        # Pattern: "SomeError: message" or "module.path.SomeError: message"
        exception_pattern = re.compile(
            r"^([a-zA-Z_][\w.]*(?:Error|Exception|Warning)): (.*)$"
        )

        for i in range(len(stderr_lines) - 1, -1, -1):
            line = stderr_lines[i]
            match = exception_pattern.match(line)
            if match:
                full_name = match.group(1)
                error_type = full_name.split(".")[-1]  # Get just the class name
                error_msg = match.group(2)[:200]
                break

    # 2. Count completed steps and get last step info
    steps_completed = 0
    last_task = ""
    last_cmd = ""
    if agent_run_dir:
        steps_dir = Path(agent_run_dir) / "steps"
        if steps_dir.exists():
            step_dirs = sorted([d for d in steps_dir.iterdir() if d.is_dir()])
            steps_completed = len(step_dirs)

            if step_dirs:
                last_step = step_dirs[-1]
                # Read last task
                task_file = last_step / "task.txt"
                if task_file.exists():
                    last_task = task_file.read_text().strip()
                    if len(last_task) > 100:
                        last_task = last_task[:100] + "..."

                # Read last command
                cmd_file = last_step / "command.txt"
                if cmd_file.exists():
                    last_cmd = cmd_file.read_text().strip()
                    if len(last_cmd) > 80:
                        last_cmd = last_cmd[:80] + "..."

    # 3. Read pinned findings
    findings = ""
    if agent_run_dir:
        findings_file = Path(agent_run_dir) / "pinned_findings.md"
        if findings_file.exists():
            findings = findings_file.read_text().strip()
            if len(findings) > 300:
                findings = findings[:300] + "..."

    # 4. Format output
    lines.append("")
    lines.append(
        "       ┌─ Failure Details ─────────────────────────────────────────────────"
    )

    if error_type:
        lines.append(f"       │ Error: {error_type}")
        if error_msg:
            # Wrap long error messages
            for msg_line in error_msg.split("\n")[:3]:
                lines.append(f"       │        {msg_line.strip()}")

    lines.append("       │ ")
    lines.append(f"       │ Progress: {steps_completed}/{max_steps} steps completed")

    if last_task:
        lines.append(f"       │ Last task: {last_task}")
    if last_cmd:
        lines.append(f"       │ Last cmd: {last_cmd}")

    if findings:
        lines.append("       │ ")
        lines.append("       │ Key findings:")
        for finding_line in findings.split("\n")[:4]:
            if finding_line.strip():
                lines.append(f"       │   {finding_line.strip()[:80]}")

    if agent_run_dir:
        lines.append("       │ ")
        lines.append(f"       │ Full trace: {agent_run_dir}/trace.md")

    lines.append(
        "       └───────────────────────────────────────────────────────────────────"
    )

    return "\n".join(lines)


@dataclass
class InstanceResult:
    """Result of evaluating a single SWE-bench instance."""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    agent_success: bool
    tests_passed: bool
    error_message: Optional[str]
    agent_run_dir: Optional[str]
    elapsed_seconds: float


@dataclass
class EvaluationConfig:
    """Configuration for the evaluation run."""

    eval_id: str
    num_instances: int
    model: str
    max_steps: int
    phase: str
    interaction_mode: str
    actor: str
    output_dir: Path
    workspace_root: Path
    started_at: str
    evaluation_mode: Literal["local", "docker"] = "docker"
    max_workers: int = 4  # For Docker parallel evaluation
    repo_cache_dir: Optional[Path] = None  # Cache directory for repositories


@dataclass
class Prediction:
    """A single prediction in SWE-bench format."""

    instance_id: str
    model_patch: str
    model_name_or_path: str = "decision-context-agent"


def setup_workspace(
    instance: dict,
    workspace_root: Path,
    repo_cache_dir: Optional[Path] = None,
    prefix: str = "",
) -> Path:
    """
    Set up a workspace for a SWE-bench instance.

    Uses repository cache if available to avoid re-downloading repositories.
    Clones the repository and checks out the base commit.
    Returns the workspace directory path.
    """
    instance_id = instance["instance_id"]
    repo = instance["repo"]
    base_commit = instance["base_commit"]

    # Create workspace directory
    workspace_dir = workspace_root / instance_id.replace("/", "_")

    # Remove existing workspace if present
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)

    workspace_dir.mkdir(parents=True, exist_ok=True)

    repo_url = f"https://github.com/{repo}.git"
    repo_name = repo.split("/")[-1]
    repo_path = workspace_dir / repo_name

    # Check if repository is cached
    cached_repo_path = None
    if repo_cache_dir:
        repo_cache_dir.mkdir(parents=True, exist_ok=True)
        cached_repo_path = repo_cache_dir / repo.replace("/", "_")

        # Check if cached repo exists and is valid
        if cached_repo_path.exists() and (cached_repo_path / ".git").exists():
            # Update the cached repo to ensure we have the commit
            prefixed_print(prefix, f"Updating cached repository {repo}...")
            subprocess.run(
                ["git", "fetch", "origin", base_commit],
                cwd=cached_repo_path,
                capture_output=True,
                timeout=300,
            )

    if (
        cached_repo_path
        and cached_repo_path.exists()
        and (cached_repo_path / ".git").exists()
    ):
        # Clone from cache (much faster than remote)
        prefixed_print(prefix, f"Cloning {repo} from cache...")
        result = subprocess.run(
            ["git", "clone", str(cached_repo_path), str(repo_path)],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode == 0:
            # Fix remote URL to point to GitHub, not cache
            subprocess.run(
                ["git", "remote", "set-url", "origin", repo_url],
                cwd=repo_path,
                capture_output=True,
                timeout=60,
            )
        else:
            prefixed_print(
                prefix, "Warning: Failed to clone from cache, falling back to remote"
            )
            cached_repo_path = None  # Fall through to remote clone

    if (
        not cached_repo_path
        or not cached_repo_path.exists()
        or not (cached_repo_path / ".git").exists()
    ):
        # Clone from remote (full clone, not shallow, so we can checkout any commit)
        prefixed_print(prefix, f"Cloning {repo} from remote...")
        result = subprocess.run(
            ["git", "clone", "--branch", "main", repo_url, str(repo_path)],
            capture_output=True,
            text=True,
            timeout=300,
        )

        # If main branch doesn't exist, try master
        if result.returncode != 0:
            result = subprocess.run(
                ["git", "clone", "--branch", "master", repo_url, str(repo_path)],
                capture_output=True,
                text=True,
                timeout=300,
            )

        if result.returncode != 0:
            # Fallback: clone without specifying branch
            result = subprocess.run(
                ["git", "clone", repo_url, str(repo_path)],
                capture_output=True,
                text=True,
                timeout=300,
            )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to clone {repo}: {result.stderr}")

        # Cache the repository for future use (only if not already cached)
        if repo_cache_dir and not (cached_repo_path and cached_repo_path.exists()):
            prefixed_print(prefix, f"Caching repository {repo}...")
            repo_cache_dir.mkdir(parents=True, exist_ok=True)
            cached_repo_path = repo_cache_dir / repo.replace("/", "_")
            # Use atomic operation - only create if doesn't exist (race-safe)
            try:
                shutil.copytree(repo_path, cached_repo_path)
            except FileExistsError:
                # Another worker cached it first, that's fine
                pass

    # Fetch and checkout the specific commit
    prefixed_print(prefix, f"Checking out commit {base_commit[:8]}...")
    subprocess.run(
        ["git", "fetch", "origin", base_commit],
        cwd=repo_path,
        capture_output=True,
        timeout=300,
    )

    result = subprocess.run(
        ["git", "checkout", base_commit],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Failed to checkout {base_commit}: {result.stderr}")

    return repo_path


def setup_instance_venv(workspace_dir: Path, prefix: str = "") -> Path:
    """
    Create a dedicated virtual environment for the instance.

    This isolates test dependencies from the main project's venv.
    Uses /usr/bin/python3 to avoid inheriting the project's venv.

    Returns the path to the venv directory (absolute).
    """
    venv_dir = (workspace_dir / ".test_venv").resolve()

    prefixed_print(prefix, "Creating test environment...")
    result = subprocess.run(
        ["/usr/bin/python3", "-m", "venv", str(venv_dir)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Failed to create venv: {result.stderr}")

    return venv_dir


def extract_patch(workspace_dir: Path, base_commit: str, prefix: str = "") -> str:
    """
    Extract the git diff (patch) from the workspace after agent has made changes.

    This is the patch that represents the agent's fix attempt.
    Returns an empty string if no changes were made.
    """
    try:
        # Get diff between base commit and current state
        result = subprocess.run(
            ["git", "diff", base_commit],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            # Fallback: get diff of working directory
            result = subprocess.run(
                ["git", "diff"],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )

        return result.stdout.strip()
    except Exception as e:
        prefixed_print(prefix, f"Warning: Could not extract patch: {e}")
        return ""


def run_agent(
    instance: dict,
    workspace_dir: Path,
    config: EvaluationConfig,
    instance_output_dir: Path,
    prefix: str = "",
) -> tuple[bool, Optional[str], Optional[str], str]:
    """
    Run the online replay loop agent on an instance.

    Returns:
        (success, error_message, agent_run_dir, model_patch)
    """
    problem_statement = instance["problem_statement"]

    # Prepare user request
    user_request = (
        f"Fix the following GitHub issue in this repository:\n\n"
        f"{problem_statement}\n\n"
        f"Instructions:\n"
        f"1. Analyze the issue and understand what needs to be fixed\n"
        f"2. Locate the relevant code files\n"
        f"3. Make the necessary code changes to fix the issue\n"
        f"4. Ensure your changes are minimal and targeted\n"
        f"Do not run tests - just fix the issue."
    )

    agent_run_dir = instance_output_dir / "agent_run"
    agent_run_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "python3",
        "online_replay_loop.py",
        "--user-request",
        user_request,
        "--run-dir",
        str(agent_run_dir),
        "--workspace-dir",
        str(workspace_dir),
        "--model",
        config.model,
        "--max-steps",
        str(config.max_steps),
        "--phase",
        config.phase,
        "--interaction-mode",
        config.interaction_mode,
        "--actor",
        config.actor,
        "--no-snapshot-autodetect",
    ]

    prefixed_print(prefix, f"Running agent (max {config.max_steps} steps)...")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 minute timeout
        )

        # Write agent output for debugging
        (instance_output_dir / "agent_stdout.txt").write_text(result.stdout)
        (instance_output_dir / "agent_stderr.txt").write_text(result.stderr)

        # Extract the patch the agent produced
        model_patch = extract_patch(workspace_dir, instance["base_commit"], prefix)

        # Save the patch
        (instance_output_dir / "model_patch.diff").write_text(model_patch)

        if result.returncode != 0:
            return (
                False,
                f"Agent failed with exit code {result.returncode}",
                str(agent_run_dir),
                model_patch,
            )

        return True, None, str(agent_run_dir), model_patch

    except subprocess.TimeoutExpired:
        # Still try to extract patch even on timeout
        model_patch = extract_patch(workspace_dir, instance["base_commit"], prefix)
        (instance_output_dir / "model_patch.diff").write_text(model_patch)
        return (
            False,
            "Agent execution timed out after 30 minutes",
            str(agent_run_dir),
            model_patch,
        )
    except Exception as e:
        return False, f"Agent execution failed: {str(e)}", str(agent_run_dir), ""


def run_tests(
    instance: dict,
    workspace_dir: Path,
    instance_output_dir: Path,
    venv_dir: Path,
    prefix: str = "",
) -> tuple[bool, Optional[str]]:
    """
    Run tests for the instance using the dedicated test venv.

    Args:
        instance: SWE-bench instance data
        workspace_dir: Path to the cloned repository
        instance_output_dir: Path to store outputs
        venv_dir: Path to the per-instance virtual environment
        prefix: Prefix for output messages

    Returns:
        (tests_passed, error_message)
    """
    test_patch = instance.get("test_patch", "")

    if not test_patch:
        return False, "No test patch provided in instance"

    # Paths to venv executables (use absolute paths, but don't resolve symlinks
    # as that would bypass the venv's site-packages)
    venv_python = venv_dir / "bin" / "python"
    venv_pip = venv_dir / "bin" / "pip"

    # Write test patch to file
    test_patch_path = instance_output_dir / "test.patch"
    test_patch_path.write_text(test_patch)

    # Apply test patch
    prefixed_print(prefix, "Applying test patch...")
    result = subprocess.run(
        ["git", "apply", str(test_patch_path.resolve())],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        return False, f"Failed to apply test patch: {result.stderr}"

    # Install test dependencies into the per-instance venv.
    # This ensures pytest runs in the same environment where it's installed.
    # First upgrade pip as the default version in venv may be outdated.
    prefixed_print(prefix, "Installing test dependencies in instance venv...")
    subprocess.run(
        [str(venv_pip), "install", "--upgrade", "pip", "-q"],
        cwd=workspace_dir,
        capture_output=True,
        timeout=60,
    )

    # Install common scientific Python dependencies and build tools.
    # Note: We use older versions for compatibility with old library code.
    # setuptools<66 is needed because astropy 4.3 uses setuptools.dep_util which was removed.
    result = subprocess.run(
        [
            str(venv_pip),
            "install",
            "pytest",
            "hypothesis",
            "numpy<2",
            "setuptools<66",
            "setuptools_scm",
            "extension-helpers",
            "cython",
            "pyerfa",
            "pyyaml",  # Astronomy dependencies
            "-q",
        ],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        timeout=300,  # dependencies can take a while
    )

    if result.returncode != 0:
        return False, f"Failed to install test dependencies: {result.stderr}"

    # Set up environment for running tests.
    import os as os_module

    env = os_module.environ.copy()

    # Add compiler flags to work around stricter modern clang.
    # Old C code (like astropy 4.3) uses patterns that are now errors.
    existing_cflags = env.get("CFLAGS", "")
    env["CFLAGS"] = (
        f"{existing_cflags} -Wno-error=incompatible-function-pointer-types -Wno-error=incompatible-pointer-types"
    )

    # Try to install the repository using python setup.py develop.
    # We use this instead of pip install -e because:
    # 1. Old setuptools<66 doesn't support PEP 660 editable installs
    # 2. We need to use our pinned setuptools to avoid dep_util errors
    prefixed_print(prefix, "Installing repository (python setup.py develop)...")
    venv_python = venv_dir / "bin" / "python"
    result = subprocess.run(
        [str(venv_python), "setup.py", "develop", "--no-deps"],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        timeout=600,  # Building extensions can take a while
        env=env,
    )

    if result.returncode != 0:
        # If pip install fails, fall back to PYTHONPATH approach
        prefixed_print(
            prefix, "Warning: setup.py develop failed, using PYTHONPATH fallback"
        )
        pythonpath = env.get("PYTHONPATH", "")
        if pythonpath:
            env["PYTHONPATH"] = f"{workspace_dir}:{pythonpath}"
        else:
            env["PYTHONPATH"] = str(workspace_dir)

        # For repos that check version at import time (like astropy), create a stub version.py
        repo_name = instance.get("repo", "").split("/")[-1]
        version_file = workspace_dir / repo_name / "version.py"
        if version_file.parent.exists():
            stub_version = """# Stub version for SWE-bench testing
version = "0.0.dev0"
"""
            try:
                version_file.write_text(stub_version)
                prefixed_print(prefix, f"Created stub version.py at {version_file}")
            except Exception as e:
                prefixed_print(
                    prefix, f"Warning: Could not create stub version.py: {e}"
                )

    # Get specific tests from FAIL_TO_PASS field
    fail_to_pass = instance.get("FAIL_TO_PASS", "")
    specific_tests = []
    if fail_to_pass:
        # Parse the test list - it's a JSON string of test names
        try:
            import ast

            tests = ast.literal_eval(fail_to_pass)
            if isinstance(tests, list):
                specific_tests = tests
        except (ValueError, SyntaxError):
            pass

    # Run tests using the venv's Python - ensures pytest is found
    prefixed_print(prefix, "Running tests...")

    # Build test command with specific tests if available
    # Use -p no:doctest to avoid issues with repos that have doctest plugins configured
    # Use --override-ini to clear any addopts from setup.cfg that might cause issues
    base_pytest_args = [
        str(venv_python),
        "-m",
        "pytest",
        "-xvs",
        "-p",
        "no:doctest",
        "--override-ini=addopts=",
    ]
    if specific_tests:
        # Run only the specific failing tests
        cmd = base_pytest_args + specific_tests
    else:
        cmd = base_pytest_args

    try:
        result = subprocess.run(
            cmd,
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout for tests
            env=env,  # Use modified environment with PYTHONPATH
        )
        test_output = result
    except subprocess.TimeoutExpired:
        return False, "Tests timed out after 10 minutes"
    except FileNotFoundError:
        return False, f"Could not find venv python at {venv_python}"

    # Write test output
    (instance_output_dir / "test_stdout.txt").write_text(test_output.stdout)
    (instance_output_dir / "test_stderr.txt").write_text(test_output.stderr)

    # Check if tests passed
    tests_passed = test_output.returncode == 0
    error_message = (
        None
        if tests_passed
        else f"Tests failed with exit code {test_output.returncode}"
    )

    return tests_passed, error_message


def evaluate_instance(
    instance: dict,
    config: EvaluationConfig,
    instance_idx: int,
) -> tuple[InstanceResult, Optional[Prediction]]:
    """Evaluate a single SWE-bench instance."""
    instance_id = instance["instance_id"]
    prefix = f"[{instance_idx}/{config.num_instances}]"

    prefixed_print(prefix, f"Evaluating {instance_id}")
    prefixed_print(prefix, "=" * 60)

    start_time = datetime.now()
    instance_output_dir = (
        config.output_dir / "instances" / instance_id.replace("/", "_")
    )
    instance_output_dir.mkdir(parents=True, exist_ok=True)

    # Write instance data
    (instance_output_dir / "instance.json").write_text(json.dumps(instance, indent=2))

    try:
        # Set up workspace
        workspace_dir = setup_workspace(
            instance, config.workspace_root, config.repo_cache_dir, prefix
        )

        # Run agent
        agent_success, agent_error, agent_run_dir, model_patch = run_agent(
            instance, workspace_dir, config, instance_output_dir, prefix
        )

        # Create prediction object for SWE-bench format
        prediction = (
            Prediction(
                instance_id=instance_id,
                model_patch=model_patch,
                model_name_or_path=f"decision-context-agent-{config.model}",
            )
            if model_patch
            else None
        )

        if not agent_success:
            prefixed_print(prefix, f"Agent failed: {agent_error}")
            # Extract and display detailed failure info
            failure_info = extract_failure_info(
                instance_output_dir, agent_run_dir, config.max_steps
            )
            prefixed_print(prefix, failure_info)
            elapsed = (datetime.now() - start_time).total_seconds()
            return InstanceResult(
                instance_id=instance_id,
                repo=instance["repo"],
                base_commit=instance["base_commit"],
                problem_statement=instance["problem_statement"][:200] + "...",
                agent_success=False,
                tests_passed=False,
                error_message=agent_error,
                agent_run_dir=agent_run_dir,
                elapsed_seconds=elapsed,
            ), prediction

        # For Docker mode, skip local tests - we'll use the official harness later
        if config.evaluation_mode == "docker":
            elapsed = (datetime.now() - start_time).total_seconds()
            patch_status = "with patch" if model_patch else "NO PATCH"
            prefixed_print(prefix, f"Agent completed ({patch_status}) ({elapsed:.1f}s)")
            return InstanceResult(
                instance_id=instance_id,
                repo=instance["repo"],
                base_commit=instance["base_commit"],
                problem_statement=instance["problem_statement"][:200] + "...",
                agent_success=True,
                tests_passed=False,  # Will be determined by Docker harness
                error_message=None,
                agent_run_dir=agent_run_dir,
                elapsed_seconds=elapsed,
            ), prediction

        # LOCAL MODE: Set up isolated test environment and run tests
        venv_dir = setup_instance_venv(workspace_dir, prefix)
        tests_passed, test_error = run_tests(
            instance, workspace_dir, instance_output_dir, venv_dir, prefix
        )

        elapsed = (datetime.now() - start_time).total_seconds()

        if tests_passed:
            prefixed_print(prefix, f"Tests PASSED! ({elapsed:.1f}s)")
        else:
            prefixed_print(prefix, f"Tests FAILED: {test_error} ({elapsed:.1f}s)")

        return InstanceResult(
            instance_id=instance_id,
            repo=instance["repo"],
            base_commit=instance["base_commit"],
            problem_statement=instance["problem_statement"][:200] + "...",
            agent_success=agent_success,
            tests_passed=tests_passed,
            error_message=test_error,
            agent_run_dir=agent_run_dir,
            elapsed_seconds=elapsed,
        ), prediction

    except Exception as e:
        elapsed = (datetime.now() - start_time).total_seconds()
        prefixed_print(prefix, f"Error: {str(e)}")
        return InstanceResult(
            instance_id=instance_id,
            repo=instance["repo"],
            base_commit=instance["base_commit"],
            problem_statement=instance["problem_statement"][:200] + "...",
            agent_success=False,
            tests_passed=False,
            error_message=f"Evaluation error: {str(e)}",
            agent_run_dir=None,
            elapsed_seconds=elapsed,
        ), None


def load_swebench_lite(cache_dir: Path, num_instances: int) -> list[dict[str, Any]]:
    """Load SWE-bench Lite dataset."""
    print(f"Loading SWE-bench Lite dataset (first {num_instances} instances)...")

    try:
        from datasets import load_dataset
    except ImportError:
        print("ERROR: 'datasets' package not found. Installing...")
        subprocess.run(
            ["pip3", "install", "datasets"],
            check=True,
        )
        from datasets import load_dataset

    # Load dataset
    dataset = load_dataset(
        "princeton-nlp/SWE-bench_Lite",
        split="test",
        cache_dir=str(cache_dir),
    )

    # Convert to list and take first N instances
    instances: list[dict[str, Any]] = [
        dict(item) for item in list(dataset)[:num_instances]
    ]

    print(f"Loaded {len(instances)} instances")
    return instances


def run_docker_evaluation(
    predictions: list[Prediction],
    config: EvaluationConfig,
) -> dict[str, Any]:
    """
    Run evaluation using the official SWE-bench Docker harness.

    This is the gold standard for reproducible evaluation.
    Returns a dict mapping instance_id to resolved status.
    """
    from swebench.harness.run_evaluation import main as run_harness

    # Write predictions to file in SWE-bench format
    predictions_path = config.output_dir / "predictions.json"
    predictions_data = [asdict(p) for p in predictions if p.model_patch]
    predictions_path.write_text(json.dumps(predictions_data, indent=2))

    print("\n" + "=" * 80)
    print("RUNNING DOCKER-BASED EVALUATION (SWE-bench Official Harness)")
    print("=" * 80)
    print(f"Predictions file: {predictions_path}")
    print(f"Number of predictions: {len(predictions_data)}")
    print(f"Max workers: {config.max_workers}")
    print("\nThis may take a while as Docker images are pulled and tests run...")
    print("=" * 80)

    if not predictions_data:
        print("WARNING: No predictions with patches to evaluate!")
        return {}

    # Get list of instance IDs we have predictions for
    instance_ids = [p["instance_id"] for p in predictions_data]

    # Run the official harness using the main function
    report_dir = config.output_dir / "swebench_reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    try:
        run_harness(
            dataset_name="princeton-nlp/SWE-bench_Lite",
            split="test",
            instance_ids=instance_ids,
            predictions_path=str(predictions_path),
            max_workers=config.max_workers,
            force_rebuild=False,
            cache_level="env",  # Cache base + environment images
            clean=False,  # Keep artifacts for debugging
            open_file_limit=4096,
            run_id=config.eval_id,
            timeout=1800,  # 30 minute timeout per instance
            namespace="swebench",
            rewrite_reports=False,
            modal=False,  # Use local Docker, not Modal
            report_dir=str(report_dir),
        )
    except Exception as e:
        print(f"Docker evaluation error: {e}")
        print("\nMake sure Docker is running and you have sufficient resources:")
        print("  - At least 120GB free disk space")
        print("  - At least 16GB RAM allocated to Docker")
        print("  - 8+ CPU cores recommended")
        return {}

    # Parse results from the harness output
    # The harness writes a report file to the current directory with pattern:
    # {model_name}.{run_id}.json
    results: dict[str, Any] = {}

    # Look for report file in current directory (where script was run from)
    # The model name comes from predictions, run_id from config
    model_name = (
        predictions_data[0].get("model_name_or_path", "model")
        if predictions_data
        else "model"
    )
    report_filename = f"{model_name}.{config.eval_id}.json"
    report_file = Path(report_filename)

    # Also check in the report_dir in case it was written there
    possible_locations = [
        report_file,  # Current directory
        report_dir / report_filename,  # Specified report dir
    ]

    # Also search for any matching report files
    for f in Path(".").glob(f"*{config.eval_id}*.json"):
        if f not in possible_locations:
            possible_locations.append(f)

    for loc in possible_locations:
        if loc.exists():
            try:
                data = json.loads(loc.read_text())
                if isinstance(data, dict):
                    # New format: resolved_ids is a list of instance IDs that passed
                    if "resolved_ids" in data:
                        for instance_id in data.get("resolved_ids", []):
                            results[instance_id] = {"resolved": True}
                        # Mark unresolved ones
                        for instance_id in data.get("unresolved_ids", []):
                            if instance_id not in results:
                                results[instance_id] = {"resolved": False}
                        print(f"Parsed harness report from: {loc}")
                        break
                    # Old format: dict per instance
                    for instance_id, instance_data in data.items():
                        if (
                            isinstance(instance_data, dict)
                            and "resolved" in instance_data
                        ):
                            results[instance_id] = instance_data
                        elif isinstance(instance_data, bool):
                            results[instance_id] = {"resolved": instance_data}
            except (json.JSONDecodeError, OSError) as e:
                print(f"Warning: Could not parse {loc}: {e}")
                continue

    if not results:
        print(
            f"Warning: Could not find or parse harness report. Looked in: {possible_locations}"
        )

    return results


def print_summary(
    results: list[InstanceResult],
    config: EvaluationConfig,
    harness_results: Optional[dict] = None,
) -> None:
    """Print evaluation summary."""
    print("\n" + "=" * 80)
    print("EVALUATION SUMMARY")
    print("=" * 80)
    print(f"Evaluation mode: {config.evaluation_mode.upper()}")

    total = len(results)
    agent_succeeded = sum(1 for r in results if r.agent_success)
    total_time = sum(r.elapsed_seconds for r in results)

    # For Docker mode, get test results from harness
    if config.evaluation_mode == "docker" and harness_results:
        tests_passed = sum(
            1
            for r in results
            if harness_results.get(r.instance_id, {}).get("resolved", False)
        )
    else:
        tests_passed = sum(1 for r in results if r.tests_passed)

    print(f"\nTotal instances: {total}")
    print(
        f"Agent completed: {agent_succeeded}/{total} ({100*agent_succeeded/total:.1f}%)"
    )
    print(
        f"Tests passed (resolved): {tests_passed}/{total} ({100*tests_passed/total:.1f}%)"
    )
    print(f"Total time: {total_time:.1f}s ({total_time/60:.1f}m)")
    print(f"Average time per instance: {total_time/total:.1f}s")

    print("\nDetailed results:")
    print("-" * 80)
    for r in results:
        # Check Docker harness results if available
        if config.evaluation_mode == "docker" and harness_results:
            resolved = harness_results.get(r.instance_id, {}).get("resolved", False)
            status = "PASS" if resolved else "FAIL"
        else:
            status = "PASS" if r.tests_passed else "FAIL"

        print(f"{status:6} {r.instance_id:40} ({r.elapsed_seconds:.1f}s)")
        if r.error_message:
            print(f"       Error: {r.error_message}")

    print("\nResults saved to:", config.output_dir / "results.json")
    if config.evaluation_mode == "docker":
        print("Predictions saved to:", config.output_dir / "predictions.json")


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate online replay loop on SWE-bench Lite"
    )
    parser.add_argument(
        "--num-instances",
        type=int,
        default=10,
        help="Number of instances to evaluate (default: 10)",
    )
    parser.add_argument(
        "--model",
        default="gemini-2.5-flash",
        help="Model to use for agent (default: gemini-2.5-flash)",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=25,
        help="Maximum steps for agent (default: 25)",
    )
    parser.add_argument(
        "--phase",
        default="autopilot",
        help="Phase for agent (default: autopilot)",
    )
    parser.add_argument(
        "--interaction-mode",
        type=str,
        choices=["human", "none"],
        default="none",
        help="Interaction mode: 'human' for interactive, 'none' for autonomous",
    )
    parser.add_argument(
        "--actor",
        type=str,
        choices=["bash", "opencode-acp"],
        default="opencode-acp",
        help="Execution actor: 'bash' (local) or 'opencode-acp'",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/swebench_eval"),
        help="Output directory (default: data/swebench_eval)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/swebench_cache"),
        help="Cache directory for dataset (default: data/swebench_cache)",
    )
    parser.add_argument(
        "--repo-cache-dir",
        type=Path,
        default=Path("data/swebench_repo_cache"),
        help="Cache directory for git repositories (default: data/swebench_repo_cache)",
    )
    parser.add_argument(
        "--evaluation-mode",
        choices=["local", "docker"],
        default="docker",
        help="Evaluation mode: 'docker' (gold standard, requires Docker) or 'local' (quick, may have env issues)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Number of parallel Docker workers (default: 4, only used in docker mode)",
    )

    args = parser.parse_args()

    # Create evaluation config
    eval_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    eval_dir = args.output_dir / eval_id
    workspace_root = eval_dir / "workspaces"

    eval_dir.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)

    # Create repo cache directory
    repo_cache_dir = args.repo_cache_dir
    repo_cache_dir.mkdir(parents=True, exist_ok=True)

    config = EvaluationConfig(
        eval_id=eval_id,
        num_instances=args.num_instances,
        model=args.model,
        max_steps=args.max_steps,
        phase=args.phase,
        interaction_mode=args.interaction_mode,
        actor=args.actor,
        output_dir=eval_dir,
        workspace_root=workspace_root,
        started_at=datetime.now().isoformat(),
        evaluation_mode=args.evaluation_mode,
        max_workers=args.max_workers,
        repo_cache_dir=repo_cache_dir,
    )

    # Save config
    (eval_dir / "config.json").write_text(
        json.dumps(asdict(config), indent=2, default=str)
    )

    print("=" * 80)
    print("SWE-BENCH LITE EVALUATION")
    print("=" * 80)
    print(f"Evaluation ID: {eval_id}")
    print(f"Model: {config.model}")
    print(f"Max steps: {config.max_steps}")
    print(f"Num instances: {config.num_instances}")
    print(f"Actor: {config.actor}")
    print(f"Evaluation mode: {config.evaluation_mode.upper()}")
    if config.evaluation_mode == "docker":
        print(f"Max workers: {config.max_workers}")
    print(f"Output dir: {eval_dir}")
    print("=" * 80)

    # Load dataset
    instances = load_swebench_lite(args.cache_dir, args.num_instances)

    # Evaluate each instance (run agent, collect patches) - CONCURRENT
    results = []
    predictions = []

    def run_single_instance(
        idx_instance_tuple: tuple[int, dict],
    ) -> tuple[InstanceResult, Optional[Prediction]]:
        idx, instance = idx_instance_tuple
        return evaluate_instance(instance, config, idx)

    # Run agent evaluations concurrently
    print(
        f"\nStarting {len(instances)} agent runs with {config.max_workers} workers..."
    )
    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        futures = {
            executor.submit(run_single_instance, (idx, inst)): idx
            for idx, inst in enumerate(instances, 1)
        }
        for future in as_completed(futures):
            result, prediction = future.result()
            results.append(result)
            if prediction:
                predictions.append(prediction)

    print(f"\nAll {len(instances)} agent runs completed.")

    # Save agent results
    results_path = eval_dir / "results.json"
    results_path.write_text(json.dumps([asdict(r) for r in results], indent=2))

    # For Docker mode, run the official harness
    harness_results = {}
    if config.evaluation_mode == "docker":
        harness_results = run_docker_evaluation(predictions, config)

        # Update results with harness outcomes
        for result in results:
            instance_harness = harness_results.get(result.instance_id, {})
            if isinstance(instance_harness, dict):
                result.tests_passed = instance_harness.get("resolved", False)
            elif isinstance(instance_harness, bool):
                result.tests_passed = instance_harness

        # Re-save results with harness outcomes
        results_path.write_text(json.dumps([asdict(r) for r in results], indent=2))

    # Print summary
    print_summary(results, config, harness_results)

    # Exit with appropriate code
    num_passed = sum(1 for r in results if r.tests_passed)
    sys.exit(0 if num_passed == len(results) else 1)


if __name__ == "__main__":
    main()
