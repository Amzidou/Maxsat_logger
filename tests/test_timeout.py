import sys, asyncio
from pathlib import Path
from maxsat_runner.core.runner import run_one

# Fake solver: dort 1.5s et n'imprime rien
SLOW_PY = """#!/usr/bin/env python3
import time; time.sleep(1.5)
"""

def test_timeout(tmp_path: Path):
    slow = tmp_path / "slow.py"
    slow.write_text(SLOW_PY)
    inst = tmp_path / "a.wcnf"
    inst.write_text("")

    cmd = f"\"{sys.executable}\" \"{slow}\" {{inst}}"
    r = asyncio.run(run_one(cmd, inst, timeout_sec=1))
    assert r.exit_code == 124  # timeout
    # Pas d'événements, final_cost None
    assert r.final_cost is None
