# /home/amzi/Maxsat_logger/src/maxsat_runner/io/logsink.py
from __future__ import annotations
from pathlib import Path
from typing import Tuple, TextIO
import re
import csv
import pandas as pd

HEADER_EVENTS = [
    "solver_tag","solver_alias","solver_cmd","instance",
    "run_id","event_idx","elapsed_sec","cost"
]
HEADER_META = [
    "solver_tag","solver_alias","solver_cmd","instance",
    "run_id","optimum_found","exit_code"
]

# ---------- Helpers de chemins ----------

def _logs_root(out_dir: Path) -> Path:
    """Racine des logs."""
    p = Path(out_dir) / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p

def _basename_noext(inst: Path) -> str:
    b = inst.name
    return b[:-5] if b.lower().endswith(".wcnf") else b

def _logs_dir(out_dir: Path, alias: str, instance: Path) -> Path:
    """
    Dossier hiérarchique: logs/<alias>/<basename>/
    ex: logs/nuwls/Inst1/
    """
    base = _basename_noext(Path(instance))
    p = _logs_root(out_dir) / alias / base
    p.mkdir(parents=True, exist_ok=True)
    return p

def _next_run_id(dir_for_pair: Path, alias: str, basename: str) -> int:
    """
    Calcule le prochain run_id à partir des fichiers présents dans
    logs/<alias>/<basename>/ alias_basename_<id>.csv
    """
    pat = re.compile(rf"^{re.escape(alias)}_{re.escape(basename)}_(\d+)\.csv$")
    mx = -1
    for f in dir_for_pair.glob(f"{alias}_{basename}_*.csv"):
        m = pat.match(f.name)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1

# ---------- API ----------

def open_run_log(out_dir: Path, alias: str, instance_path: Path) -> Tuple[Path, TextIO, Path, int]:
    """
    Crée les fichiers d’un nouveau run pour (alias, instance_basename) en écrivant
    l’en-tête du fichier d’événements. Renvoie:
      (events_path, events_fp, meta_path, run_id)
    """
    pair_dir = _logs_dir(out_dir, alias, instance_path)
    basename = _basename_noext(Path(instance_path))
    run_id = _next_run_id(pair_dir, alias, basename)

    events_path = pair_dir / f"{alias}_{basename}_{run_id}.csv"
    events_fp = events_path.open("w", newline="", encoding="utf-8")
    w = csv.writer(events_fp)
    w.writerow(HEADER_EVENTS)  # en-tête unique (CSV pur)

    meta_path = pair_dir / f"{alias}_{basename}_{run_id}_meta.csv"
    return events_path, events_fp, meta_path, run_id

def append_logs_summary(out_dir: Path):
    """
    Agrège TOUT ce qui est sous logs/** (récursif) pour produire:
      - trajectories.csv  (concat des évènements, + 'basename')
      - summary.csv       (une ligne par run en fusionnant events + meta)
    """
    logs_root = _logs_root(out_dir)

    # ---- Charger events (récursif)
    ev_parts = []
    for f in logs_root.rglob("*_[0-9]*.csv"):
        if f.name.endswith("_meta.csv"):
            continue
        if f.stat().st_size == 0:
            continue
        try:
            df = pd.read_csv(f)
        except Exception:
            continue
        if not set(HEADER_EVENTS).issubset(df.columns):
            continue
        ev_parts.append(df[HEADER_EVENTS].copy())

    df_traj = (
        pd.concat(ev_parts, ignore_index=True)
        if ev_parts else
        pd.DataFrame(columns=HEADER_EVENTS)
    )

    # ---- Charger meta (récursif)
    meta_parts = []
    for f in logs_root.rglob("*_meta.csv"):
        if f.stat().st_size == 0:
            continue
        try:
            df = pd.read_csv(f)
        except Exception:
            continue
        if not set(HEADER_META).issubset(df.columns):
            continue
        meta_parts.append(df[HEADER_META].copy())

    df_meta = (
        pd.concat(meta_parts, ignore_index=True)
        if meta_parts else
        pd.DataFrame(columns=HEADER_META)
    )

    # ---- trajectories.csv
    traj_csv = Path(out_dir) / "trajectories.csv"
    if not df_traj.empty:
        df_traj = df_traj.copy()
        df_traj["basename"] = df_traj["instance"].apply(lambda p: Path(str(p)).name)
        df_traj[HEADER_EVENTS + ["basename"]].to_csv(traj_csv, index=False)
    else:
        pd.DataFrame(columns=HEADER_EVENTS + ["basename"]).to_csv(traj_csv, index=False)

    # ---- summary.csv (une ligne par run)
    cols = [
        "solver_tag","solver_alias","solver_cmd","instance","run_id",
        "final_cost","time_to_best_sec","optimum_found","exit_code"
    ]
    sum_csv = Path(out_dir) / "summary.csv"

    if df_traj.empty and df_meta.empty:
        pd.DataFrame(columns=cols).to_csv(sum_csv, index=False)
        return traj_csv, sum_csv

    # Final cost / time_to_best par (alias, instance, run_id)
    if not df_traj.empty:
        last = (
            df_traj.sort_values(["solver_alias","instance","run_id","event_idx"])
                  .groupby(["solver_alias","instance","run_id"], as_index=False)
                  .tail(1)
        )
        last = last.rename(columns={"cost": "final_cost", "elapsed_sec": "time_to_best_sec"})
        keep = ["solver_tag","solver_alias","solver_cmd","instance","run_id","final_cost","time_to_best_sec"]
        last = last[keep]
    else:
        last = pd.DataFrame(columns=["solver_tag","solver_alias","solver_cmd","instance","run_id","final_cost","time_to_best_sec"])

    # Fusion avec meta (optimum_found, exit_code), outer pour garder les runs sans events
    df_sum = last.merge(
        df_meta[["solver_tag","solver_alias","solver_cmd","instance","run_id","optimum_found","exit_code"]],
        on=["solver_tag","solver_alias","solver_cmd","instance","run_id"],
        how="outer"
    ).copy()

    # Runs sans events -> final_cost/time_to_best None
    if not df_sum.empty:
        mask_no_events = df_sum["final_cost"].isna() & df_sum["time_to_best_sec"].isna()
        if mask_no_events.any():
            df_sum.loc[mask_no_events, ["final_cost","time_to_best_sec"]] = [None, None]

    df_sum[cols].to_csv(sum_csv, index=False)
    return traj_csv, sum_csv
