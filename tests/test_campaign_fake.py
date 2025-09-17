import os, sys, asyncio, tempfile
from pathlib import Path
import pandas as pd

from maxsat_runner.core.campaign import run_campaign_sequential

def test_campaign_with_fake_solver(tmp_path: Path):
    # Créer 2 fausses instances
    inst_dir = tmp_path / "instances"
    inst_dir.mkdir()
    (inst_dir / "a.wcnf").write_text("")
    (inst_dir / "b.wcnf").write_text("")

    # Fake solver
    fake = Path(__file__).with_name("assets").joinpath("fake_solver.py").absolute()
    assert fake.exists()

    solver_cmds = [f"\"{sys.executable}\" \"{fake}\" {{inst}}"]

    out_dir = tmp_path / "runs"
    payload = asyncio.run(run_campaign_sequential(
        solver_cmds=solver_cmds,
        instances_dir=inst_dir,
        pattern=".wcnf",
        out_dir=out_dir
    ))

    # CSV existent
    traj_csv = Path(payload["trajectories_csv"])
    sum_csv  = Path(payload["summary_csv"])
    assert traj_csv.exists() and sum_csv.exists()

    # DataFrame valides
    df_traj = pd.read_csv(traj_csv)
    df_sum  = pd.read_csv(sum_csv)

    # 2 instances * ~3 events >= 4 (robuste avec random)
    assert df_traj.shape[0] >= 4
    # 2 lignes summary
    assert df_sum.shape[0] == 2
    # Colonnes attendues
    assert set(["solver_tag","solver_cmd","instance","event_idx","elapsed_sec","cost"]).issubset(df_traj.columns)
    assert set(["final_cost","optimum_found","exit_code"]).issubset(df_sum.columns)
