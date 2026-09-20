"""Runs the control panel's dependency-free node suite (foundry-module/tests) so
the repo's normal pytest run, and CI, covers it too."""
import shutil
import subprocess
from pathlib import Path

import pytest

SUITE = Path(__file__).resolve().parents[2] / "foundry-module" / "tests" / "aigm-control-panel"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_control_panel_node_suite_passes():
    files = sorted(str(p) for p in SUITE.glob("*.test.mjs"))
    assert files, f"no node tests under {SUITE}"

    result = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1500:]
