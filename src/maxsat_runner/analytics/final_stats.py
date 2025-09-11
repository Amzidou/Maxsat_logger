from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import math

import pandas as pd
import matplotlib.pyplot as plt

from .segments import compute_relative_scores_timewindow_for_instance


# ============ Utils ============

def _legend_bottom(ncol: Optional[int] = None) -> None:
    """Place la légende en bas, multi-colonnes si demandé."""
    if ncol is None:
        ncol = 3
    plt.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.12),
        ncol=ncol,
        frameon=False,
    )
    plt.subplots_adjust(bottom=0.25)  # laisse de la place pour la légende

def _savefig(png: Path) -> None:
    png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(png, bbox_inches="tight")
    plt.close()


# ============ 1) Segments par instance ============

def collect_instance_segments(
    df_traj: pd.DataFrame,
    by: str = "solver_alias",
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
) -> pd.DataFrame:
    """Concatène les segments score(t) de toutes les instances."""
    df = df_traj.copy()
    df["basename"] = df["instance"].apply(lambda p: Path(str(p)).name)
    instances = sorted(df["basename"].unique())

    parts: List[pd.DataFrame] = []
    for inst in instances:
        seg = compute_relative_scores_timewindow_for_instance(
            df_traj, inst, by=by, t_min=t_min, t_max=t_max
        )
        if not seg.empty:
            parts.append(seg)

    if not parts:
        return pd.DataFrame(columns=["instance","t_start","t_end","duration","solver","score","cost","best_cost"])

    all_seg = pd.concat(parts, ignore_index=True)
    all_seg["t_start"] = all_seg["t_start"].astype(float)
    all_seg["t_end"]   = all_seg["t_end"].astype(float)
    all_seg["score"]   = all_seg["score"].astype(float)
    return all_seg


# ============ 2) Grille temporelle globale ============

def build_time_grid(segments_df: pd.DataFrame) -> List[float]:
    """Union triée des bornes t_start/t_end sur toutes instances (dé-doublonnée)."""
    if segments_df.empty:
        return []
    times = pd.concat([segments_df["t_start"], segments_df["t_end"]], ignore_index=True)
    vals = sorted(float(x) for x in times.to_list())
    if not vals:
        return []
    eps = 1e-12
    grid = [vals[0]]
    for v in vals[1:]:
        if v - grid[-1] > eps:
            grid.append(v)
    return grid


# ============ 3) Moyenne des scores dans le temps ============

@dataclass
class _Cursor:
    idx: int = 0  # index segment courant pour une (instance, solver)

def _score_at_left_of_interval(g: pd.DataFrame, t: float, cur: _Cursor) -> Optional[float]:
    """Score valable sur [t, ..) pour un (instance, solver). None si hors fenêtre."""
    i = cur.idx
    n = len(g)
    while i < n and float(g.iloc[i]["t_end"]) <= t:
        i += 1
    cur.idx = i
    if i >= n:
        return None
    t0 = float(g.iloc[i]["t_start"])
    t1 = float(g.iloc[i]["t_end"])
    if t0 <= t < t1:
        s = float(g.iloc[i]["score"])
        if not math.isfinite(s):
            return 0.0
        return 0.0 if s < 0.0 else (1.0 if s > 1.0 else s)
    return None

def compute_avg_scores_over_time(
    segments_df: pd.DataFrame,
    by: str = "solver_alias",
) -> pd.DataFrame:
    """
    DataFrame long: colonnes ['t', by, 'avg_score', 'n_instances'].
    À chaque t de la grille globale, moyenne du score par solver sur les instances couvrantes.
    """
    if segments_df.empty:
        return pd.DataFrame(columns=["t", by, "avg_score", "n_instances"])

    seg = segments_df.copy()
    seg["basename"] = seg["instance"].apply(lambda p: Path(str(p)).name)

    solvers = sorted(seg["solver"].astype(str).unique())
    instances = sorted(seg["basename"].unique())
    T = build_time_grid(seg)
    if not T:
        return pd.DataFrame(columns=["t", by, "avg_score", "n_instances"])

    # Préparation groupes (solver -> instance -> DF)
    per_solver: Dict[str, Dict[str, pd.DataFrame]] = {}
    for s in solvers:
        g_s = seg[seg["solver"].astype(str) == s]
        per_solver[s] = {inst: g_si.sort_values(["t_start","t_end"]).reset_index(drop=True)
                         for inst, g_si in g_s.groupby("basename")}

    rows: List[Dict] = []
    for s in solvers:
        cursors = {inst: _Cursor(0) for inst in instances}
        last_val: Optional[float] = None

        for k in range(len(T) - 1):
            t0 = T[k]
            acc = 0.0
            cnt = 0
            for inst in instances:
                g_si = per_solver[s].get(inst)
                if g_si is None or g_si.empty:
                    continue
                sc = _score_at_left_of_interval(g_si, t0, cursors[inst])
                if sc is None:
                    continue
                acc += sc
                cnt += 1
            avg = (acc / cnt) if cnt > 0 else float("nan")
            last_val = avg if math.isfinite(avg) else last_val
            rows.append({"t": t0, by: s, "avg_score": avg, "n_instances": cnt})

        # snapshot final t_N
        tN = T[-1]
        rows.append({"t": tN, by: s, "avg_score": (last_val if last_val is not None else float("nan")), "n_instances": None})

    return pd.DataFrame(rows).sort_values(["t", by]).reset_index(drop=True)


# ============ 4) Plot + CSV + Driver ============

def plot_avg_scores_over_time(
    ts_df: pd.DataFrame,
    out_png: Path,
    by: str = "solver_alias",
) -> None:
    """Courbe step-post + marqueurs ; légende en bas par défaut."""
    if ts_df.empty:
        return

    plt.figure(figsize=(8.5, 4.8), dpi=150)
    x_min = x_max = None

    for s, g in ts_df.groupby(by):
        g = g.sort_values("t")
        g = g[~g["avg_score"].isna()]
        if g.empty:
            continue
        xs = g["t"].astype(float).tolist()
        ys = g["avg_score"].astype(float).tolist()

        # bornes X
        lxmin, lxmax = min(xs), max(xs)
        x_min = lxmin if x_min is None else min(x_min, lxmin)
        x_max = lxmax if x_max is None else max(x_max, lxmax)

        plt.step(xs, ys, where="post", linewidth=1.0, label=str(s))
        plt.plot(xs, ys, ".", linewidth=0)

    # axes
    if x_min is not None and x_max is not None:
        span = max(1e-12, x_max - x_min)
        pad = max(0.03 * span, 1e-3)
        left = max(0.0, x_min - pad)
        right = x_max + pad
        if right <= left:
            right = left + max(pad, 1e-3)
        plt.xlim(left, right)
    plt.ylim(-0.05, 1.05)

    plt.xlabel("Elapsed (sec)")
    plt.ylabel("Average relative score (best/cost)")
    plt.title("Average scores over time (per solver)")
    _legend_bottom()  # légende en bas par défaut
    _savefig(out_png)

def save_avg_scores_csv(ts_df: pd.DataFrame, out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    ts_df.to_csv(out_csv, index=False)

def generate_final_score_summary(
    df_traj: pd.DataFrame,
    out_dir: Path,
    by: str = "solver_alias",
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
) -> Dict[str, str]:
    """Pipeline: segments → moyennes → CSV+PNG."""
    seg = collect_instance_segments(df_traj, by=by, t_min=t_min, t_max=t_max)
    ts  = compute_avg_scores_over_time(seg, by=by)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "average_scores_over_time.csv"
    out_png = out_dir / "average_scores_over_time.png"

    save_avg_scores_csv(ts, out_csv)
    plot_avg_scores_over_time(ts, out_png, by=by)

    return {"avg_scores_csv": str(out_csv), "avg_scores_png": str(out_png)}
