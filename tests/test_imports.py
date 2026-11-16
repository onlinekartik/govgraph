"""Importing govgraph must be cheap and must not reach outside the process."""

from __future__ import annotations

import subprocess
import sys

CHECK = """
import socket, sys

def refuse(*args, **kwargs):
    raise AssertionError("network access during import")

socket.socket.connect = refuse
socket.create_connection = refuse
socket.getaddrinfo = refuse

import govgraph, govgraph.adapters, govgraph.cli, govgraph.engines, govgraph.export
import govgraph.samples, govgraph.viewer
import govgraph.adapters.csv_json, govgraph.adapters.dbt
import govgraph.adapters.openmetadata, govgraph.adapters.datahub

loaded = sorted(name for name in ("psycopg", "mcp", "anyio") if name in sys.modules)
print("optional modules loaded:", loaded)
"""


def test_import_has_no_side_effects() -> None:
    completed = subprocess.run(  # noqa: S603 - runs the test interpreter on a fixed script
        [sys.executable, "-c", CHECK], capture_output=True, text=True, timeout=120, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "optional modules loaded: []"
