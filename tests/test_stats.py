from pathlib import Path
import pandas as pd
from maxsat_runner.analytics.stats import load_runs, compute_leaderboard, generate_basic_reports

def test_stats_minimal(tmp_path: Path):
    runs = tmp_path / "runs"; runs.mkdir()
    traj = runs / "trajectories.csv"
    summ = runs / "summary.csv"

    # CSV minimaux
    pd.DataFrame([
        {"solver_cmd":"A","instance":"/x/inst1.wcnf","event_idx":0,"elapsed_sec":0.1,"cost":8},
        {"solver_cmd":"A","instance":"/x/inst1.wcnf","event_idx":1,"elapsed_sec":0.2,"cost":6},
        {"solver_cmd":"B","instance":"/x/inst1.wcnf","event_idx":0,"elapsed_sec":0.05,"cost":7},
    ]).to_csv(traj, index=False)

    pd.DataFrame([
        {"solver_cmd":"A","instance":"/x/inst1.wcnf","final_cost":6,"time_to_best_sec":0.2,"optimum_found":False,"exit_code":0},
        {"solver_cmd":"B","instance":"/x/inst1.wcnf","final_cost":7,"time_to_best_sec":0.05,"optimum_found":False,"exit_code":0},
    ]).to_csv(summ, index=False)

    df_traj, df_sum = load_runs(runs)
    lb = compute_leaderboard(df_sum, by="solver_cmd")
    assert "wins" in lb.columns and lb["wins"].sum() == 1

    out = tmp_path / "reports"
    res = generate_basic_reports(runs, out, by="solver_cmd", instance_basename="inst1")
    assert (out / "leaderboard.csv").exists()
    assert (out / "plot_leaderboard_wins.png").exists()
    assert (out / "plot_time_to_best_box.png").exists()
    assert (out / "plot_trajectory_inst1.png").exists()
