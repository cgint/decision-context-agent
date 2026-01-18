from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from pi_rpc_client import run_pi_rpc_prompt


def test_pi_rpc_client_fake_pi() -> None:
    with tempfile.TemporaryDirectory(prefix="dca-pi-rpc-test-") as tmp:
        tmp_path = Path(tmp)
        fake_pi = tmp_path / "fake_pi.py"

        fake_pi.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "for line in sys.stdin:",
                    "  req = json.loads(line)",
                    "  if req.get('type') == 'prompt':",
                    "    sys.stdout.write(json.dumps({'id': req.get('id'), 'type':'response','command':'prompt','success': True}) + '\\n')",
                    "    sys.stdout.write(json.dumps({'type':'agent_start'}) + '\\n')",
                    "    sys.stdout.write(json.dumps({'type':'agent_end','messages':[{'role':'assistant','content':'ok'}]}) + '\\n')",
                    "    sys.stdout.flush()",
                    "    break",
                ]
            ),
            encoding="utf-8",
        )

        transcript = tmp_path / "transcript.jsonl"
        result = run_pi_rpc_prompt(
            message="hello",
            cwd=tmp_path,
            transcript_path=transcript,
            pi_executable=sys.executable,
            pi_args=[str(fake_pi)],
            rpc_args=[],
            env={"PYTHONUNBUFFERED": "1"},
            timeout_seconds=10,
        )

        assert result.ok is True
        assert result.agent_end_event is not None
        assert result.agent_end_event.get("type") == "agent_end"

        lines = transcript.read_text(encoding="utf-8").strip().splitlines()
        assert any('"stream": "stdin"' in line for line in lines)
        assert any('"type": "agent_end"' in line for line in lines)


if __name__ == "__main__":
    test_pi_rpc_client_fake_pi()
    print("ok")
