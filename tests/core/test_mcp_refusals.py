import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_real_mcp_clients_receive_business_refusal_reasons(transport):
    script = Path(__file__).resolve().parents[2] / "scripts/smoke_mcp.py"
    result = subprocess.run([sys.executable, str(script), "--transport", transport], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{transport}:" in result.stdout and "readable refusal reasons OK" in result.stdout
