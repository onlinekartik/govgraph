"""The example script, in the mode that needs no services."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "online_store"


def test_demo_runs_in_memory(tmp_path: Path) -> None:
    completed = subprocess.run(  # noqa: S603 - runs our own script with the test interpreter
        [sys.executable, str(EXAMPLE / "run_demo.py"), "--memory"],
        capture_output=True,
        text=True,
        timeout=120,
        env={"DEMO_OUTPUT_DIR": str(tmp_path), "PATH": ""},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    out = completed.stdout
    assert "wrote 48 nodes and 61 edges" in out
    assert "=== explain_failure(F-1002) ===" in out
    assert "Possible cause: DQFailure 'F-1001'" in out
    assert "refused: invalid_input: depth must be at most 3" in out
    graph = json.loads((tmp_path / "graph.json").read_text())
    assert len(graph["nodes"]) == 19
    assert (tmp_path / "graph.html").read_text().startswith("<!doctype html>")


def test_compose_file_stores_no_credentials() -> None:
    import yaml

    compose = yaml.safe_load((EXAMPLE / "docker-compose.yml").read_text())
    for name, service in compose["services"].items():
        environment = service.get("environment", {})
        for key, value in environment.items():
            if "PASSWORD" in key.upper():
                assert key.endswith("_FILE"), f"{name}.{key} must point at a file"
                assert str(value).startswith("/run/"), f"{name}.{key}"
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port} beyond localhost"
    assert "ports" not in compose["services"]["postgres"]
