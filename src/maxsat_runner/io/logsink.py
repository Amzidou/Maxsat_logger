# /home/amzi/Maxsat_logger/src/maxsat_runner/io/logsink.py
from __future__ import annotations
from pathlib import Path
from typing import Tuple
import re
import csv
import pandas as pd

HEADER_EVENTS = ["solver_tag","solver_alias","solver_cmd","instance","run_id","event_idx","elapsed_sec","cost"]
HEADER_META   = ["solver_tag","solver_alias","solver_cmd","instance","run_id","optimum_found","exit_code"]

def _logs_dir(out_dir: Path) -> Path:
    p = Path(out_dir) / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p

def _basename_noext(inst: Path) -> str:
    b = inst.name
    return b[:-5] if b.lower().endswith(".wcnf") else b

def _next_run_id(logs: Path, alias: str, basename: str) -> int:
    pat = re.compile(rf"^{re.escape(alias)}_{re.escape(basename)}_(\d+)\.csv$")
    mx = -1
    for f in logs.glob(f"{alias}_{basename}_*.csv"):
        m = pat.match(f.name)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1

def open_run_log(out_dir: Path, alias: str, instance_path: Path) -> Tuple[Path, 'TextIO', Path, int]:
    logs = _logs_dir(out_dir)
    basename = _basename_noext(Path(instance_path))
    run_id = _next_run_id(logs, alias, basename)

    events_path = logs / f"{alias}_{basename}_{run_id}.csv"
    events_fp = events_path.open("w", newline="", encoding="utf-8")
    w = csv.writer(events_fp)
    w.writerow(HEADER_EVENTS)  # entête unique

    meta_path = logs / f"{alias}_{basename}_{run_id}_meta.csv"
    return events_path, events_fp, meta_path, run_id

def append_logs_summary(out_dir: Path):
    logs = _logs_dir(out_dir)
    # Charger events
    ev_parts = []
    for f in logs.glob("*_[0-9]*.csv"):
        if f.name.endswith("_meta.csv"):
            continue
        if f.stat().st_size == 0:
            continue
        df = pd.read_csv(f)
        if not set(HEADER_EVENTS).issubset(df.columns):
            continue
        ev_parts.append(df)
    df_traj = pd.concat(ev_parts, ignore_index=True) if ev_parts else pd.DataFrame(columns=HEADER_EVENTS)

    # Charger meta
    meta_parts = []
    for f in logs.glob("*_meta.csv"):
        if f.stat().st_size == 0:
            continue
        df = pd.read_csv(f)
        if not set(HEADER_META).issubset(df.columns):
            continue
        meta_parts.append(df)
    df_meta = pd.concat(meta_parts, ignore_index=True) if meta_parts else pd.DataFrame(columns=HEADER_META)

    # trajectories.csv
    if not df_traj.empty:
        df_traj["basename"] = df_traj["instance"].apply(lambda p: Path(str(p)).name)
    traj_csv = Path(out_dir) / "trajectories.csv"
    df_traj.to_csv(traj_csv, index=False)

    # summary.csv (une ligne par run)
    cols = ["solver_tag","solver_alias","solver_cmd","instance","run_id","final_cost","time_to_best_sec","optimum_found","exit_code"]
    if df_traj.empty and df_meta.empty:
        sum_csv = Path(out_dir) / "summary.csv"
        pd.DataFrame(columns=cols).to_csv(sum_csv, index=False)
        return traj_csv, sum_csv

    # Final cost / time_to_best par (alias, instance, run_id)
    if not df_traj.empty:
        last = df_traj.sort_values(["run_id","event_idx"]).groupby(["solver_alias","instance","run_id"], as_index=False).tail(1)
        last = last.rename(columns={"cost":"final_cost","elapsed_sec":"time_to_best_sec"})
        keep = ["solver_tag","solver_alias","solver_cmd","instance","run_id","final_cost","time_to_best_sec"]
        last = last[keep]
    else:
        last = pd.DataFrame(columns=["solver_tag","solver_alias","solver_cmd","instance","run_id","final_cost","time_to_best_sec"])

    # Fusion avec meta (optimum_found, exit_code)
    df_sum = last.merge(
        df_meta[["solver_tag","solver_alias","solver_cmd","instance","run_id","optimum_found","exit_code"]],
        on=["solver_tag","solver_alias","solver_cmd","instance","run_id"],
        how="outer"
    )

    # Cas run sans évènement -> final_cost/time_to_best None
    if not df_meta.empty:
        for _, row in df_meta.iterrows():
            key = (row.solver_tag, row.solver_alias, row.solver_cmd, row.instance, row.run_id)
            if df_sum[(df_sum.solver_tag==key[0]) &
                      (df_sum.solver_alias==key[1]) &
                      (df_sum.solver_cmd==key[2]) &
                      (df_sum.instance==key[3]) &
                      (df_sum.run_id==key[4])].empty:
                df_sum.loc[len(df_sum)] = [*key, None, None, row.optimum_found, int(row.exit_code)]

    sum_csv = Path(out_dir) / "summary.csv"
    df_sum[cols].to_csv(sum_csv, index=False)
    return traj_csv, sum_csv
