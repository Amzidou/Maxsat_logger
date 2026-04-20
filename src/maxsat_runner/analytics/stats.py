from __future__ import annotations
from pathlib import Path
from typing import Optional, Tuple, List, Dict
import math
import statistics

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from matplotlib.scale import FuncScale
from matplotlib.ticker import FixedLocator, FormatStrFormatter

from .segments import compute_relative_scores_timewindow_for_instance, _match_instance_name
from .final_stats import generate_final_score_summary

# ===================== Utils =====================

def _legend_bottom(ncol: Optional[int] = None) -> None:
    if ncol is None:
        ncol = 3
    plt.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.12),
        ncol=ncol,
        frameon=False,
    )
    plt.subplots_adjust(bottom=0.25)

def _savefig(png: Path) -> None:
    png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(png, bbox_inches="tight")
    plt.close()

# ===================== Rebuild from logs: MOYENNES sur les runs =====================

_EVENTS_HEADER = ["solver_tag","solver_alias","solver_cmd","instance","run_id","event_idx","elapsed_sec","cost"]
_META_HEADER   = ["solver_tag","solver_alias","solver_cmd","instance","run_id","optimum_found","exit_code"]

def _read_all_events(logs_dir: Path) -> pd.DataFrame:
    """Lit tous les CSV d'événements (structure imbriquée supportée)."""
    parts = []
    for f in logs_dir.rglob("*_[0-9]*.csv"):
        if f.name.endswith("_meta.csv") or f.stat().st_size == 0:
            continue
        try:
            df = pd.read_csv(f)
        except Exception as e:
            raise RuntimeError(f"Erreur lecture CSV dans {f}: {e}") from e
        if set(_EVENTS_HEADER).issubset(df.columns):
            # types utiles
            if "run_id" in df.columns:
                df["run_id"] = pd.to_numeric(df["run_id"], errors="coerce").astype("Int64")
            if "event_idx" in df.columns:
                df["event_idx"] = pd.to_numeric(df["event_idx"], errors="coerce").astype("Int64")
            df["elapsed_sec"] = pd.to_numeric(df["elapsed_sec"], errors="coerce")
            df["cost"] = pd.to_numeric(df["cost"], errors="coerce")
            parts.append(df[_EVENTS_HEADER].copy())
    if parts:
        out = pd.concat(parts, ignore_index=True)
        out["basename"] = out["instance"].apply(lambda p: Path(str(p)).name)
        return out
    return pd.DataFrame(columns=_EVENTS_HEADER + ["basename"])

def _read_all_meta(logs_dir: Path) -> pd.DataFrame:
    """Lit tous les CSV meta (structure imbriquée supportée)."""
    parts = []
    for f in logs_dir.rglob("*_meta.csv"):
        if f.stat().st_size == 0:
            continue
        df = pd.read_csv(f)
        if set(_META_HEADER).issubset(df.columns):
            if "run_id" in df.columns:
                df["run_id"] = pd.to_numeric(df["run_id"], errors="coerce").astype("Int64")
            parts.append(df[_META_HEADER].copy())
    if parts:
        return pd.concat(parts, ignore_index=True)
    return pd.DataFrame(columns=_META_HEADER)

def _step_cost_at_t(times: List[float], costs: List[float], t: float, idx_hint: int) -> tuple[Optional[float], int]:
    """Retourne le coût (step) à l'instant t pour une série (times,costs)."""
    i = idx_hint
    n = len(times)
    while i + 1 < n and times[i + 1] <= t:
        i += 1
    if n == 0 or times[0] > t:
        return (None, i)
    return (float(costs[i]), i)

def _mean_trajectory_for_group(g_runs: Dict[int, pd.DataFrame]) -> pd.DataFrame:
    """
    Construit la trajectoire MOYENNE pour un groupe (solver_alias, solver_cmd, instance)
    à partir des runs => DataFrame avec colonnes:
      solver_tag, solver_alias, solver_cmd, instance, run_id=-1, event_idx, elapsed_sec, cost(=mean)
    """
    # Union des temps sur l'ensemble des runs
    union_times: List[float] = []
    for df in g_runs.values():
        union_times.extend(df["elapsed_sec"].astype(float).tolist())
    if not union_times:
        return pd.DataFrame(columns=_EVENTS_HEADER)

    T = sorted(set(float(x) for x in union_times))
    # grille croissante (tolérance)
    eps = 1e-12
    grid = [T[0]]
    for v in T[1:]:
        if v - grid[-1] > eps:
            grid.append(v)

    # Prépare les séries step par run
    series = {}
    for rid, df in g_runs.items():
        d = df.sort_values("elapsed_sec")
        series[rid] = {
            "t": d["elapsed_sec"].astype(float).tolist(),
            "c": d["cost"].astype(float).tolist(),
            "idx": 0,
        }

    mean_rows: List[Dict] = []
    prev_mean = None
    event_idx = 0
    # moyenne à gauche de chaque t_k
    for t0 in grid:
        vals: List[float] = []
        for rid, ser in series.items():
            c, new_idx = _step_cost_at_t(ser["t"], ser["c"], t0, ser["idx"])
            ser["idx"] = new_idx
            if c is not None and math.isfinite(c):
                vals.append(c)
        if not vals:
            continue
        m = sum(vals) / len(vals)
        if prev_mean is None or m < prev_mean - 1e-12:
            any_df = next(iter(g_runs.values()))
            mean_rows.append({
                "solver_tag":   any_df["solver_tag"].iloc[0],
                "solver_alias": any_df["solver_alias"].iloc[0],
                "solver_cmd":   any_df["solver_cmd"].iloc[0],
                "instance":     any_df["instance"].iloc[0],
                "run_id": -1,
                "event_idx": event_idx,
                "elapsed_sec": t0,
                "cost": float(m),
            })
            event_idx += 1
            prev_mean = m

    return pd.DataFrame(mean_rows, columns=_EVENTS_HEADER)

def _mean_summary_for_group(g_runs: Dict[int, pd.DataFrame], g_meta: pd.DataFrame) -> Dict:
    """
    Stats moyennes pour un (solver×instance) sur tous les run_id :
      final_cost_mean, time_to_best_sec_mean, optimum_found_rate, exit_code_mode
    """
    finals: List[float] = []
    tbest: List[float] = []

    for rid, df in g_runs.items():
        d = df.sort_values("elapsed_sec")
        if not d.empty:
            finals.append(float(d["cost"].iloc[-1]))
            tbest.append(float(d["elapsed_sec"].iloc[-1]))

    opt_rate = None
    exit_mode = None
    if not g_meta.empty:
        if "optimum_found" in g_meta.columns:
            opt_rate = float(pd.to_numeric(g_meta["optimum_found"], errors="coerce").fillna(0).astype(float).mean())
        if "exit_code" in g_meta.columns and not g_meta["exit_code"].isna().all():
            try:
                exit_mode = int(g_meta["exit_code"].mode(dropna=True).iloc[0])
            except Exception:
                exit_mode = None

    any_df = next(iter(g_runs.values()))
    return {
        "solver_tag": any_df["solver_tag"].iloc[0],
        "solver_alias": any_df["solver_alias"].iloc[0],
        "solver_cmd": any_df["solver_cmd"].iloc[0],
        "instance": any_df["instance"].iloc[0],
        "run_id": -1,  # moyenne
        "final_cost": (sum(finals)/len(finals) if finals else None),
        "time_to_best_sec": (sum(tbest)/len(tbest) if tbest else None),
        "optimum_found": opt_rate,
        "exit_code": exit_mode,
    }

def _build_mean_from_logs(logs_dir: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Construit (df_traj_mean, df_sum_mean) à partir de tous les runs :
    - df_traj_mean : trajectoires MOYENNES (un run_id=-1 par solver×instance)
    - df_sum_mean  : summary MOYEN (une ligne par solver×instance)
    """
    ev = _read_all_events(logs_dir)
    meta = _read_all_meta(logs_dir)
    if ev.empty and meta.empty:
        raise FileNotFoundError(f"Aucun log dans {logs_dir}")

    traj_rows: List[pd.DataFrame] = []
    sum_rows: List[Dict] = []

    keys = ["solver_tag","solver_alias","solver_cmd","instance"]
    for (st, sa, sc, inst), g in ev.groupby(keys):
        # runs -> dict[run_id] -> DF
        g_runs: Dict[int, pd.DataFrame] = {}
        for rid, g_r in g.groupby("run_id"):
            if pd.isna(rid):
                continue
            g_runs[int(rid)] = g_r[["solver_tag","solver_alias","solver_cmd","instance",
                                    "run_id","event_idx","elapsed_sec","cost"]].copy()

        if not g_runs:
            continue

        # trajectoire MOYENNE
        traj_mean = _mean_trajectory_for_group(g_runs)
        if not traj_mean.empty:
            traj_rows.append(traj_mean)

        # summary MOYEN
        g_meta = pd.DataFrame(columns=_META_HEADER)
        if not meta.empty:
            g_meta = meta[(meta["solver_alias"]==sa) &
                          (meta["solver_cmd"]==sc) &
                          (meta["instance"]==inst)]
        sum_rows.append(_mean_summary_for_group(g_runs, g_meta))

    df_traj_mean = pd.concat(traj_rows, ignore_index=True) if traj_rows else pd.DataFrame(columns=_EVENTS_HEADER)
    if not df_traj_mean.empty:
        df_traj_mean["basename"] = df_traj_mean["instance"].apply(lambda p: Path(str(p)).name)

    df_sum_mean = pd.DataFrame(sum_rows, columns=[
        "solver_tag","solver_alias","solver_cmd","instance",
        "run_id","final_cost","time_to_best_sec","optimum_found","exit_code"
    ])
    return df_traj_mean, df_sum_mean

def load_runs(runs_dir: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Si 'runs_dir/logs/' existe:
      - (Re)construit **des MOYENNES** par (solver×instance) et ÉCRASE:
        runs_dir/trajectories.csv, runs_dir/summary.csv
    Sinon:
      - lit les CSV existants (legacy).
    """
    runs_dir = Path(runs_dir)
    logs_dir = runs_dir / "logs"

    if logs_dir.exists() and logs_dir.is_dir():
        df_traj_mean, df_sum_mean = _build_mean_from_logs(logs_dir)

        traj_csv = runs_dir / "trajectories.csv"
        sum_csv  = runs_dir / "summary.csv"
        df_traj_mean.to_csv(traj_csv, index=False)
        df_sum_mean.to_csv(sum_csv, index=False)

        return df_traj_mean, df_sum_mean

    # Fallback: CSV pré-existants
    traj_csv = runs_dir / "trajectories.csv"
    sum_csv  = runs_dir / "summary.csv"
    if not traj_csv.exists() or not sum_csv.exists():
        raise FileNotFoundError(f"trajectories.csv ou summary.csv introuvable dans {runs_dir}")
    df_traj = pd.read_csv(traj_csv)
    df_sum  = pd.read_csv(sum_csv)
    for col in ["solver_cmd", "instance"]:
        if col not in df_sum.columns or col not in df_traj.columns:
            raise ValueError("Colonnes requises manquantes (solver_cmd / instance).")
    return df_traj, df_sum

# ===================== Leaderboards simples =====================

def compute_leaderboard(df_sum: pd.DataFrame, by: str = "solver_alias") -> pd.DataFrame:
    if "final_cost" not in df_sum.columns:
        raise ValueError("summary.csv doit contenir final_cost")
    df = df_sum.copy()
    df["basename"] = df["instance"].apply(lambda p: Path(str(p)).name)
    mins = df.groupby("basename")["final_cost"].min().rename("min_cost")
    j = df.merge(mins, on="basename", how="left")
    j["win"] = (j["final_cost"] == j["min_cost"]).astype(int)
    agg = j.groupby(by).agg(
        wins=("win", "sum"),
        n_instances=("basename", "nunique"),
        avg_final_cost=("final_cost", "mean"),
        avg_time_to_best=("time_to_best_sec", "mean"),
        optimum_rate=("optimum_found", "mean"),
    ).reset_index()
    if "optimum_rate" in agg.columns:
        agg["optimum_rate"] = (agg["optimum_rate"] * 100.0).round(2)
    return agg.sort_values(["wins", "avg_final_cost"], ascending=[False, True])

def plot_leaderboard_wins(leaderboard: pd.DataFrame, out_png: Path, by: str = "solver_alias") -> None:
    if leaderboard.empty:
        return
    lb = leaderboard.sort_values("wins", ascending=False)
    plt.figure(figsize=(8, 4.5), dpi=150)
    bars = plt.bar(lb[by].astype(str), lb["wins"])
    # légère amélioration de lisibilité
    plt.xticks(rotation=45, ha="right")
    plt.ylabel("Wins (final_cost minimal)")
    plt.title("Leaderboard (wins par solver)")
    plt.tight_layout()
    _savefig(out_png)

def plot_time_to_best_box(df_sum: pd.DataFrame, out_png: Path, by: str = "solver_alias") -> None:
    df = df_sum.copy().dropna(subset=["time_to_best_sec"])
    if df.empty:
        return
    plt.figure(figsize=(8, 4.5), dpi=150)
    # données par groupe
    grouped = list(df.groupby(by))
    data = [grp["time_to_best_sec"].values for _, grp in grouped]
    labels = [str(k) for k, _ in grouped]
    b = plt.boxplot(data, tick_labels=labels, showfliers=False)
    plt.xticks(rotation=45, ha="right")
    plt.ylabel("Time to best (sec)")
    plt.title("Temps au meilleur (distribution)")
    plt.tight_layout()
    _savefig(out_png)

# ===================== Trajectoires coût(t) par instance =====================

def _decide_time_scale(xs: np.ndarray, log_time: bool) -> tuple[bool, bool]:
    """Renvoie (use_log, use_log1p) selon les données et le toggle."""
    if not log_time:
        return (False, False)
    if np.any(xs <= 0.0):
        return (False, True)  # log1p fallback
    return (True, False)      # log base10

def _apply_x_mapping(ax: plt.Axes, x_min: float, x_max: float, use_log: bool, use_log1p: bool) -> None:
    """Applique l’échelle X + limites cohérentes."""
    span = max(1e-12, x_max - x_min)
    pad = max(0.03 * span, 1e-3)
    if use_log:
        left = max(1e-12, x_min)
        right = max(left * 1.1, x_max)
        ax.set_xscale("log", base=10)
        ax.set_xlim(left - pad, right + pad + 1)
        ax.xaxis.set_major_locator(mtick.LogLocator(base=10))
        ax.xaxis.set_minor_locator(mtick.LogLocator(base=10, subs=np.arange(2, 10) * 0.1))
        ax.xaxis.set_minor_formatter(mtick.NullFormatter())
        ax.set_xlabel("Elapsed (sec, log)")
    else:
        ax.set_xlim(x_min - pad, x_max + pad)
        ax.set_xlabel("log1p(Elapsed sec)" if use_log1p else "Elapsed (sec)")

def plot_trajectory_for_instance(
    df_traj: pd.DataFrame,
    instance_basename: str,
    out_png: Path,
    by: str = "solver_alias",
    log_time: bool = False,
) -> None:
    df = df_traj.copy()
    df["basename"] = df["instance"].apply(lambda p: Path(str(p)).name)
    sub = _match_instance_name(df, instance_basename)
    
    if sub.empty:
        return

    plt.figure(figsize=(8, 4.5), dpi=150)
    ax = plt.gca()

    # décidez log/log1p
    xs_all = sub["elapsed_sec"].astype(float).to_numpy()
    use_log, use_log1p = _decide_time_scale(xs_all, log_time)
    x_min = x_max = None

    def xmap(x: np.ndarray) -> np.ndarray:
        return np.log1p(x) if use_log1p else x

    ymin_g = ymax_g = None

    for key, grp in sub.groupby(by):
        g = grp.sort_values("elapsed_sec")
        xs = g["elapsed_sec"].astype(float).to_numpy()
        ys = g["cost"].astype(float).to_numpy()
        if xs.size == 0:
            continue

        xs_plot = xmap(xs)
        lxmin, lxmax = float(xs_plot.min()), float(xs_plot.max())
        x_min = lxmin if x_min is None else min(x_min, lxmin)
        x_max = lxmax if x_max is None else max(x_max, lxmax)

        # bornes Y
        lymin, lymax = float(np.min(ys)), float(np.max(ys))
        ymin_g = lymin if ymin_g is None else min(ymin_g, lymin)
        ymax_g = lymax if ymax_g is None else max(ymax_g, lymax)

        # tracer ligne + points avec même couleur
        line, = ax.step(xs_plot, ys, where="post", linewidth=1.0, label=str(key))
        color = line.get_color()
        ax.plot(xs_plot, ys, ".", linewidth=0, color=color)

    # X
    if x_min is not None and x_max is not None:
        _apply_x_mapping(ax, x_min, x_max, use_log, use_log1p)

    # Y
    if ymin_g is not None and ymax_g is not None and ymin_g == ymax_g:
        base = float(ymin_g)
        pad_y = max(1.0, 0.02 * (abs(base) + 1.0))
        ax.set_ylim(base - pad_y, base + pad_y)

    ax.set_ylabel("Cost")
    ax.set_title(f"Trajectoires – {instance_basename}")
    _legend_bottom()
    _savefig(out_png)

def plot_all_instances(df_traj: pd.DataFrame, out_dir: Path, by: str = "solver_alias", log_time: bool=False) -> List[Dict[str, str]]:
    df = df_traj.copy()
    df["basename"] = df["instance"].apply(lambda p: Path(str(p)).name)
    out_instances = out_dir / "instances"
    out_instances.mkdir(parents=True, exist_ok=True)

    produced: List[Dict[str, str]] = []
    for base in sorted(df["basename"].unique()):
        png = out_instances / f"plot_{base}.png"
        plot_trajectory_for_instance(df_traj, base, png, by=by, log_time=log_time)
        if png.exists():
            produced.append({"instance": base, "png": str(png)})
    return produced

# ============ Scores(t) par instance ============

def plot_scores_for_instance(
    df_traj: pd.DataFrame,
    instance_basename: str,
    out_png: Path,
    by: str = "solver_alias",
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
    log_time: bool = False
) -> None:
    """score(t) ∈[0,1] par solver, step-post + points (même couleur), X=lin/log/log1p, Y étiré vers 1."""
    seg = compute_relative_scores_timewindow_for_instance(
        df_traj, instance_basename, by=by, t_min=t_min, t_max=t_max
    )
    if seg.empty:
        return

    plt.figure(figsize=(8, 4.5), dpi=150)
    ax = plt.gca()

    # Décision d’échelle temps à partir de toutes les abscisses disponibles
    xs_all = np.concatenate([
        g.sort_values(["t_start", "t_end"])[["t_start", "t_end"]].to_numpy().ravel()
        for _, g in seg.groupby("solver")
    ]) if not seg.empty else np.array([])
    xs_all = xs_all.astype(float) if xs_all.size else xs_all
    use_log, use_log1p = _decide_time_scale(xs_all, log_time)

    def xmap(x: np.ndarray) -> np.ndarray:
        if use_log1p:
            return np.log1p(x)
        return x  # si log base10: on laisse brut et on met ax en log

    # Tracé
    x_min = x_max = None
    eps = 1e-12
    for key, g in seg.groupby("solver"):
        g = g.sort_values(["t_start", "t_end"]).reset_index(drop=True)
        if g.empty:
            continue

        xs = g["t_start"].astype(float).to_numpy()
        xs = np.append(xs, float(g["t_end"].iloc[-1]))
        ys = g["score"].fillna(0.0).astype(float).to_numpy()
        ys = np.append(ys, float(g["score"].fillna(0.0).iloc[-1]))

        # Si log base10 → garder brut (mais pas <=0), si log1p → transformer
        if use_log:
            xs = np.maximum(xs, eps)  # éviter 0 sur un axe log
            xs_plot = xs
        else:
            xs_plot = xmap(xs)

        # borne x globale (dans l’espace PLOT, i.e., après mapping pour log1p)
        lxmin, lxmax = float(xs_plot.min()), float(xs_plot.max())
        x_min = lxmin if x_min is None else min(x_min, lxmin)
        x_max = lxmax if x_max is None else max(x_max, lxmax)

        line, = ax.step(xs_plot, ys, where="post", linewidth=1.0, label=str(key))
        ax.plot(xs_plot, ys, ".", color=line.get_color(), linewidth=0)

    # ---- Axe Y étiré vers 1 (avec marge) ----
    BETA = 0.4
    MARGIN = 0.05

    def y_forward(y):
        y = np.asarray(y, dtype=float)
        out = np.empty_like(y)

        mask_mid = (y >= 0.0) & (y <= 1.0)
        out[mask_mid] = 1.0 - (1.0 - y[mask_mid])**BETA

        mask_low = (y < 0.0)
        out[mask_low] = y[mask_low] * (0.1 / MARGIN)

        mask_high = (y > 1.0)
        out[mask_high] = 1.0 + (y[mask_high] - 1.0) * (0.1 / MARGIN)
        return out

    def y_inverse(z):
        z = np.asarray(z, dtype=float)
        out = np.empty_like(z)

        mask_mid = (z >= 0.0) & (z <= 1.0)
        out[mask_mid] = 1.0 - (1.0 - z[mask_mid])**(1.0/BETA)

        mask_low = (z < 0.0)
        out[mask_low] = z[mask_low] * (MARGIN / 0.1)

        mask_high = (z > 1.0)
        out[mask_high] = 1.0 + (z[mask_high] - 1.0) * (MARGIN / 0.1)
        return out

    ax.set_yscale(FuncScale(ax, (y_forward, y_inverse)))
    ax.set_ylim(-MARGIN, 1.0 + MARGIN)
    ax.yaxis.set_major_locator(FixedLocator([0.0,0.2,0.4,0.6,0.8,0.9,0.95,0.98,0.99,1.0]))
    ax.yaxis.set_major_formatter(FormatStrFormatter('%.2f'))
    ax.set_ylabel("Relative score (best/cost)")
    ax.grid(True, which="both", alpha=0.25)

    # ---- Axe X selon la décision ----
    if x_min is not None and x_max is not None:
        _apply_x_mapping(ax, x_min, x_max, use_log=use_log, use_log1p=use_log1p)

    ax.set_title(f"Scores relatifs – {instance_basename}")
    _legend_bottom()
    _savefig(out_png)

def plot_all_instances_scores(
    df_traj: pd.DataFrame,
    out_dir: Path,
    by: str = "solver_alias",
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
    log_time: bool=False
) -> List[Dict[str, str]]:
    df = df_traj.copy()
    df["basename"] = df["instance"].apply(lambda p: Path(str(p)).name)
    out_scores = out_dir / "instances_scores"
    out_scores.mkdir(parents=True, exist_ok=True)

    produced: List[Dict[str, str]] = []
    
    for base in sorted(df["basename"].unique()):
        png = out_scores / f"scores_{base}.png"
        plot_scores_for_instance(df_traj, base, png, by=by, t_min=t_min, t_max=t_max, log_time=log_time)
        if png.exists():
            produced.append({"instance": base, "png": str(png)})
    return produced

# ===================== Leaderboard relatif (fenêtre temporelle) =====================

def aggregate_relative_leaderboard(
    df_traj: pd.DataFrame,
    by: str = "solver_alias",
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
    t_at: Optional[float] = None,
) -> pd.DataFrame:
    df = df_traj.copy()
    df["basename"] = df["instance"].apply(lambda p: Path(str(p)).name)
    instances = sorted(df["basename"].unique())

    per_solver_scores: Dict[str, List[float]] = {}
    wins_snapshot: Dict[str, int] = {}

    for inst in instances:
        seg = compute_relative_scores_timewindow_for_instance(df_traj, inst, by=by, t_min=t_min, t_max=t_max)
        if seg.empty:
            continue
        g2 = seg.copy()
        g2["score"] = g2["score"].fillna(0.0)
        m = g2.groupby("solver", observed=True)["score"].mean()
        for k, v in m.items():
            per_solver_scores.setdefault(k, []).append(float(v))

        if t_at is not None:
            S = seg[["solver","t_start","t_end","cost"]]
            snap = S[(S["t_start"] <= t_at) & (t_at < S["t_end"])]
            if snap.empty:
                snap = S.sort_values(["solver","t_end"]).groupby("solver").tail(1)
            costs_map = {row.solver: (None if pd.isna(row.cost) else int(row.cost)) for row in snap.itertuples()}
            finite = [c for c in costs_map.values() if c is not None]
            if finite:
                b = min(finite)
                for s, c in costs_map.items():
                    if c is not None and c == b:
                        wins_snapshot[s] = wins_snapshot.get(s, 0) + 1

    rows = [{
        by: s,
        "instances_covered": len(vals),
        "score_time_weighted": sum(vals)/len(vals) if vals else 0.0,
        "wins_snapshot": wins_snapshot.get(s, 0) if t_at is not None else None
    } for s, vals in per_solver_scores.items()]
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["score_time_weighted"], ascending=False)
    return out

# ===================== Réplicas **par solveur** =====================

def compute_replicas_by_solver_stats(runs_dir: Path, by: str = "solver_alias") -> pd.DataFrame:
    """
    Pour chaque (solver × instance), on agrège **tous les run_id**:
      n_runs, mean_final_cost, std_final_cost.
    Retourne un DF long avec colonnes: [by, instance, n_runs, mean_final_cost, std_final_cost]
    """
    logs_dir = Path(runs_dir) / "logs"
    if not logs_dir.exists():
        return pd.DataFrame(columns=[by,"instance","n_runs","mean_final_cost","std_final_cost"])

    df_ev_all = _read_all_events(logs_dir)
    _ = _read_all_meta(logs_dir)  # non utilisé pour l’instant

    if df_ev_all.empty:
        return pd.DataFrame(columns=[by,"instance","n_runs","mean_final_cost","std_final_cost"])

    # dernière ligne (final_cost) par run_id
    last = (df_ev_all
            .sort_values(["solver_alias","instance","run_id","event_idx"])
            .groupby(["solver_alias","instance","run_id"], as_index=False)
            .tail(1))
    last = last.rename(columns={"cost":"final_cost"})
    last["basename"] = last["instance"].apply(lambda p: Path(str(p)).name)

    agg = (last
           .groupby([by, "basename"], dropna=False)
           .agg(n_runs=("run_id","nunique"),
                mean_final_cost=("final_cost","mean"),
                std_final_cost=("final_cost","std"))
           .reset_index()
           .rename(columns={"basename":"instance"}))
    return agg

def plot_replicas_by_solver_gallery(
    df_rep: pd.DataFrame,
    out_dir: Path,
    by: str = "solver_alias",
) -> List[Dict[str, str]]:
    """
    Crée **un PNG par solveur**:
      X = instances, Y = mean_final_cost, erreur = ± std_final_cost.
    Renvoie [{solver, png}].
    """
    out_dir = Path(out_dir) / "replicas_by_solver"
    out_dir.mkdir(parents=True, exist_ok=True)
    produced: List[Dict[str, str]] = []
    if df_rep.empty:
        return produced

    for solver, g in df_rep.groupby(by):
        g = g.sort_values("instance")
        xs = np.arange(len(g))
        vals = g["mean_final_cost"].to_numpy(dtype=float)
        errs = g["std_final_cost"].fillna(0.0).to_numpy(dtype=float)
        labels = g["instance"].astype(str).tolist()

        plt.figure(figsize=(max(8, 0.5*len(vals)), 4.8), dpi=150)
        bars = plt.bar(xs, vals, yerr=errs, capsize=3)
        plt.xticks(xs, labels, rotation=45, ha="right")
        plt.ylabel("Final cost (mean ± std)")
        plt.title(f"Replicas – {by}={solver}")
        plt.tight_layout()
        png = out_dir / f"replicas_{by}_{str(solver).replace('/', '_').replace(' ', '_')}.png"
        _savefig(png)
        produced.append({"solver": str(solver), "png": str(png)})

    return produced

# ===================== Driver global =====================

def generate_basic_reports(
    runs_dir: Path,
    out_dir: Path,
    by: str = "solver_alias",
    instance_basename: Optional[str] = None,
    per_instance: bool = True,
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
    t_at: Optional[float] = None,
    log_time: bool = False
) -> dict:
    """
    - (Re)build trajectories/summary (moyenne par solver×instance)
    - Leaderboards (classique + relatif)
    - Trajectoires coût/temps (par instance) — couleurs cohérentes et log/log1p auto
    - Scores(t) par instance — idem
    - Average scores over time — géré dans final_stats avec étirement Y près de 1
    - Réplicas par solveur (CSV + PNG)
    """
    print(f"=== Génération des rapports basiques dans {out_dir} ===")
    # 1) rebuild (moyennes)
    print("Chargement des runs et (re)construction des moyennes...")
    df_traj, df_sum = load_runs(runs_dir)

    # 2) Leaderboard simple
    print("Génération du leaderboard simple...")
    lb = compute_leaderboard(df_sum, by=by)
    out_dir.mkdir(parents=True, exist_ok=True)
    lb_csv = out_dir / "leaderboard.csv"
    lb.to_csv(lb_csv, index=False)
    plot_leaderboard_wins(lb, out_dir / "plot_leaderboard_wins.png", by=by)
    plot_time_to_best_box(df_sum, out_dir / "plot_time_to_best_box.png", by=by)

    # 3) Leaderboard relatif (fenêtre temporelle)
    print("Génération du leaderboard relatif...")
    lb_rel = aggregate_relative_leaderboard(df_traj, by=by, t_min=t_min, t_max=t_max, t_at=t_at)
    lb_rel_csv = out_dir / "leaderboard_relative.csv"
    lb_rel.to_csv(lb_rel_csv, index=False)

    # 4) Trajectoires coût/temps
    print("Génération des trajectoires coût/temps par instance...")
    traj_png = None
    if instance_basename:
        traj_png = out_dir / f"plot_trajectory_{instance_basename}.png"
        plot_trajectory_for_instance(df_traj, instance_basename, traj_png, by=by, log_time=log_time)
    instance_cost_plots = plot_all_instances(df_traj, out_dir, by=by, log_time=log_time) if per_instance else []

    # 5) Scores(t) par instance
    print("Génération des scores(t) par instance...")
    instance_score_plots = plot_all_instances_scores(df_traj, out_dir, by=by, t_min=t_min, t_max=t_max, log_time=log_time)

    # 6) Moyennes temporelles globales (dans final_stats) — inclut la logique d’échelle X et Y
    print("Génération des statistiques finales temporelles...")
    finals = generate_final_score_summary(df_traj, out_dir=out_dir, by=by, t_min=t_min, t_max=t_max, log_time=log_time)

    # 7) Réplicas **par solveur**
    print("Génération des statistiques de réplicas par solveur...")
    df_rep_solver = compute_replicas_by_solver_stats(runs_dir, by=by)
    rep_csv = out_dir / "replicas_by_solver.csv"
    df_rep_solver.to_csv(rep_csv, index=False)
    rep_solver_plots = plot_replicas_by_solver_gallery(df_rep_solver, out_dir, by=by)

    return {
        "leaderboard_csv": str(lb_csv),
        "plot_leaderboard_wins": str(out_dir / "plot_leaderboard_wins.png"),
        "plot_time_to_best_box": str(out_dir / "plot_time_to_best_box.png"),
        "plot_trajectory": (str(traj_png) if traj_png else None),
        "instance_plots": instance_cost_plots,
        "leaderboard_relative_csv": str(lb_rel_csv),
        "instance_score_plots": instance_score_plots,
        "t_min": t_min, "t_max": t_max, "t_at": t_at,
        **finals,
        "replicas_by_solver_csv": str(rep_csv),
        "replicas_by_solver_plots": rep_solver_plots,
    }