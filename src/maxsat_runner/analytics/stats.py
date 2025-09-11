from __future__ import annotations
from pathlib import Path
from typing import Optional, Tuple, List, Dict

import pandas as pd
import matplotlib.pyplot as plt

from .segments import compute_relative_scores_timewindow_for_instance
from .final_stats import generate_final_score_summary


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
    plt.subplots_adjust(bottom=0.25)

def _savefig(png: Path) -> None:
    png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(png, bbox_inches="tight")
    plt.close()


# ============ IO ============

def load_runs(runs_dir: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
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


# ============ Leaderboards simples ============

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
    plt.bar(lb[by].astype(str), lb["wins"])
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
    data = [grp["time_to_best_sec"].values for _, grp in df.groupby(by)]
    labels = [str(k) for k, _ in df.groupby(by)]
    plt.boxplot(data, labels=labels, showfliers=False)
    plt.xticks(rotation=45, ha="right")
    plt.ylabel("Time to best (sec)")
    plt.title("Temps au meilleur (distribution)")
    plt.tight_layout()
    _savefig(out_png)


# ============ Trajectoires coût(t) par instance ============

def plot_trajectory_for_instance(
    df_traj: pd.DataFrame,
    instance_basename: str,
    out_png: Path,
    by: str = "solver_alias",
) -> None:
    """Coût vs temps par solver (step-post + marqueurs). Légende en bas."""
    df = df_traj.copy()
    df["basename"] = df["instance"].apply(lambda p: Path(str(p)).name)
    candidate = instance_basename if instance_basename.endswith(".wcnf") else (instance_basename + ".wcnf")
    sub = df[df["basename"] == candidate]
    if sub.empty:
        return

    plt.figure(figsize=(8, 4.5), dpi=150)
    tmin_g = tmax_g = ymin_g = ymax_g = None

    for key, grp in sub.groupby(by):
        g = grp.sort_values("elapsed_sec")
        xs = g["elapsed_sec"].to_list()
        ys = g["cost"].to_list()
        if not xs:
            continue
        # bornes
        ltmin, ltmax = min(xs), max(xs)
        lymin, lymax = min(ys), max(ys)
        tmin_g = ltmin if tmin_g is None else min(tmin_g, ltmin)
        tmax_g = ltmax if tmax_g is None else max(tmax_g, ltmax)
        ymin_g = lymin if ymin_g is None else min(ymin_g, lymin)
        ymax_g = lymax if ymax_g is None else max(ymax_g, lymax)

        if len(xs) >= 2:
            plt.step(xs, ys, where="post", label=str(key), linewidth=1.0)
            plt.plot(xs, ys, ".", linewidth=0)
        else:
            plt.scatter(xs, ys, label=str(key), marker="o")

    # axes
    if tmin_g is not None and tmax_g is not None:
        span = max(1e-9, tmax_g - tmin_g)
        pad = max(0.05 * span, 1e-3)
        left = max(0.0, tmin_g - pad)
        right = tmax_g + pad
        plt.xlim(left, right)
    if ymin_g is not None and ymax_g is not None and ymin_g == ymax_g:
        base = float(ymin_g)
        pad_y = max(1.0, 0.02 * (abs(base) + 1.0))
        plt.ylim(base - pad_y, base + pad_y)

    plt.xlabel("Elapsed (sec)")
    plt.ylabel("Cost")
    plt.title(f"Trajectoires – {instance_basename}")
    _legend_bottom()
    _savefig(out_png)

def plot_all_instances(df_traj: pd.DataFrame, out_dir: Path, by: str = "solver_alias") -> List[Dict[str, str]]:
    df = df_traj.copy()
    df["basename"] = df["instance"].apply(lambda p: Path(str(p)).name)
    out_instances = out_dir / "instances"
    out_instances.mkdir(parents=True, exist_ok=True)

    produced: List[Dict[str, str]] = []
    for base in sorted(df["basename"].unique()):
        png = out_instances / f"plot_{base}.png"
        plot_trajectory_for_instance(df_traj, base, png, by=by)
        if png.exists():
            produced.append({"instance": base, "png": str(png)})
    return produced


# ============ Leaderboard relatif (fenêtre temporelle) ============

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
        m = seg.groupby("solver").apply(lambda g: g["score"].fillna(0.0).mean())
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


# ============ Scores(t) par instance ============

def plot_scores_for_instance(
    df_traj: pd.DataFrame,
    instance_basename: str,
    out_png: Path,
    by: str = "solver_alias",
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
) -> None:
    """score(t) ∈[0,1] par solver, step-post + marqueurs ; légende en bas."""
    seg = compute_relative_scores_timewindow_for_instance(
        df_traj, instance_basename, by=by, t_min=t_min, t_max=t_max
    )
    if seg.empty:
        return

    plt.figure(figsize=(8, 4.5), dpi=150)
    x_min = x_max = None
    y_min = y_max = None

    for key, g in seg.groupby("solver"):
        g = g.sort_values(["t_start", "t_end"]).reset_index(drop=True)
        if g.empty:
            continue

        xs = g["t_start"].astype(float).tolist() + [float(g["t_end"].iloc[-1])]
        ys = g["score"].fillna(0.0).astype(float).tolist() + [float(g["score"].fillna(0.0).iloc[-1])]

        lxmin, lxmax = min(xs), max(xs)
        lymin, lymax = min(ys), max(ys)
        x_min = lxmin if x_min is None else min(x_min, lxmin)
        x_max = lxmax if x_max is None else max(x_max, lxmax)
        y_min = lymin if y_min is None else min(y_min, lymin)
        y_max = lymax if y_max is None else max(y_max, lymax)

        plt.step(xs, ys, where="post", label=str(key), linewidth=1.0)
        plt.plot(xs, ys, ".", linewidth=0)

    if x_min is not None and x_max is not None:
        span = max(1e-12, x_max - x_min)
        pad  = max(0.03 * span, 1e-3)
        left = max(0.0, x_min - pad)
        right = x_max + pad
        if right <= left:
            right = left + max(pad, 1e-3)
        plt.xlim(left, right)
    if y_min is not None and y_max is not None:
        span = max(1e-12, y_max - y_min)
        pad  = max(0.03 * span, 0.02)
        bottom = y_min - pad
        top    = y_max + pad
        if top <= bottom:
            top = bottom + max(pad, 0.02)
        plt.ylim(bottom, top)
    else:
        plt.ylim(-0.05, 1.05)

    plt.xlabel("Elapsed (sec)")
    plt.ylabel("Relative score (best/cost)")
    title = f"Scores relatifs – {instance_basename}"
    plt.title(title)
    _legend_bottom()
    _savefig(out_png)

def plot_all_instances_scores(
    df_traj: pd.DataFrame,
    out_dir: Path,
    by: str = "solver_alias",
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
) -> List[Dict[str, str]]:
    df = df_traj.copy()
    df["basename"] = df["instance"].apply(lambda p: Path(str(p)).name)
    out_scores = out_dir / "instances_scores"
    out_scores.mkdir(parents=True, exist_ok=True)

    produced: List[Dict[str, str]] = []
    for base in sorted(df["basename"].unique()):
        png = out_scores / f"scores_{base}.png"
        plot_scores_for_instance(df_traj, base, png, by=by, t_min=t_min, t_max=t_max)
        if png.exists():
            produced.append({"instance": base, "png": str(png)})
    return produced


# ============ Driver global ============

def generate_basic_reports(
    runs_dir: Path,
    out_dir: Path,
    by: str = "solver_alias",
    instance_basename: Optional[str] = None,
    per_instance: bool = True,
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
    t_at: Optional[float] = None,
) -> dict:
    """Pipeline: leaderboards + plots coûts + scores + stat finale."""
    df_traj, df_sum = load_runs(runs_dir)

    # Leaderboard simple
    lb = compute_leaderboard(df_sum, by=by)
    out_dir.mkdir(parents=True, exist_ok=True)
    lb_csv = out_dir / "leaderboard.csv"
    lb.to_csv(lb_csv, index=False)
    plot_leaderboard_wins(lb, out_dir / "plot_leaderboard_wins.png", by=by)
    plot_time_to_best_box(df_sum, out_dir / "plot_time_to_best_box.png", by=by)

    # Leaderboard relatif (fenêtre temporelle)
    lb_rel = aggregate_relative_leaderboard(df_traj, by=by, t_min=t_min, t_max=t_max, t_at=t_at)
    lb_rel_csv = out_dir / "leaderboard_relative.csv"
    lb_rel.to_csv(lb_rel_csv, index=False)

    # Trajectoires coût/temps
    traj_png = None
    if instance_basename:
        traj_png = out_dir / f"plot_trajectory_{instance_basename}.png"
        plot_trajectory_for_instance(df_traj, instance_basename, traj_png, by=by)

    instance_cost_plots = plot_all_instances(df_traj, out_dir, by=by) if per_instance else []

    # Scores(t) par instance
    instance_score_plots = plot_all_instances_scores(df_traj, out_dir, by=by, t_min=t_min, t_max=t_max)

    # Stat finale (moyennes dans le temps)
    finals = generate_final_score_summary(df_traj, out_dir=out_dir, by=by, t_min=t_min, t_max=t_max)

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
    }
