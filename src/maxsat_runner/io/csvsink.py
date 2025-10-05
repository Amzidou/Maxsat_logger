from pathlib import Path
from typing import List, Tuple
import re
import pandas as pd
from ..core.types import RunResult

def append_csv(out_dir: Path, results: List[RunResult]) -> Tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    traj_csv = out_dir / "trajectories.csv"
    sum_csv  = out_dir / "summary.csv"

    traj_rows = []
    sum_rows  = []
    for r in results:
        for i, e in enumerate(r.events):
            traj_rows.append({
                "solver_tag": r.solver_tag,
                "solver_alias": r.solver_alias,  # NEW
                "solver_cmd": r.solver_cmd,
                "instance": r.instance,
                "event_idx": i,
                "elapsed_sec": e.t_sec,
                "cost": e.cost,
            })
        sum_rows.append({
            "solver_tag": r.solver_tag,
            "solver_alias": r.solver_alias,     # NEW
            "solver_cmd": r.solver_cmd,
            "instance": r.instance,
            "final_cost": r.final_cost,
            "time_to_best_sec": r.time_to_best_sec,
            "optimum_found": r.optimum_found,
            "exit_code": r.exit_code,
        })

    if traj_rows:
        df_traj = pd.DataFrame(traj_rows,
            columns=["solver_tag","solver_alias","solver_cmd","instance","event_idx","elapsed_sec","cost"])
        df_traj.to_csv(traj_csv, mode="a", header=not traj_csv.exists(), index=False)

    if sum_rows:
        df_sum = pd.DataFrame(sum_rows,
            columns=["solver_tag","solver_alias","solver_cmd","instance","final_cost","time_to_best_sec","optimum_found","exit_code"])
        df_sum.to_csv(sum_csv, mode="a", header=not sum_csv.exists(), index=False)

    return traj_csv, sum_csv


def _clean_instance_basename(name: str) -> str:
    """
    Supprime les suffixes .wcnf, .wcnf.gz, .cnf, .cnf.gz, .xml.wcnf, etc.
    """
    name = re.sub(r"(\.xml)?\.wcnf(\.gz)?$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\.cnf(\.gz)?$", "", name, flags=re.IGNORECASE)
    return name

def write_instance_csv(out_dir: Path, tag: str, r: "RunResult") -> Path:
    """
    Écrit les événements d’un run dans un CSV :
      out_dir / <solver_tag> / <instance_base>.csv
    """
    # Créer dossier pour le solver
    tag_dir = out_dir / tag
    tag_dir.mkdir(parents=True, exist_ok=True)

    base = _clean_instance_basename(Path(r.instance).name)
    inst_csv = tag_dir / f"{base}.csv"

    rows = [{
        "solver_tag": r.solver_tag,
        "solver_alias": r.solver_alias,
        "solver_cmd": r.solver_cmd,
        "instance": r.instance,
        "event_idx": i,
        "elapsed_sec": e.t_sec,
        "cost": e.cost,
    } for i, e in enumerate(r.events)]

    df = pd.DataFrame(rows, columns=["solver_tag","solver_alias","solver_cmd","instance","event_idx","elapsed_sec","cost"])
    df.to_csv(inst_csv, index=False)
    return inst_csv
