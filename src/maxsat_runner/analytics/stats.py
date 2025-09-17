from __future__ import annotations
from pathlib import Path
from typing import Optional, Tuple, List, Dict

import pandas as pd
import matplotlib.pyplot as plt

from .segments import compute_relative_scores_timewindow_for_instance
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

# ===================== Rebuild from logs (dernier run par solver×instance) =====================

_EVENTS_HEADER = ["solver_tag","solver_alias","solver_cmd","instance","run_id","event_idx","elapsed_sec","cost"]
_META_HEADER   = ["solver_tag","solver_alias","solver_cmd","instance","run_id","optimum_found","exit_code"]

def _read_all_events(logs_dir: Path) -> pd.DataFrame:
    parts = []
    for f in logs_dir.glob("*_[0-9]*.csv"):
        if f.name.endswith("_meta.csv") or f.stat().st_size == 0:
            continue
        df = pd.read_csv(f)
        if set(_EVENTS_HEADER).issubset(df.columns):
            parts.append(df[_EVENTS_HEADER].copy())
    if parts:
        out = pd.concat(parts, ignore_index=True)
        out["basename"] = out["instance"].apply(lambda p: Path(str(p)).name)
        return out
    return pd.DataFrame(columns=_EVENTS_HEADER + ["basename"])

def _read_all_meta(logs_dir: Path) -> pd.DataFrame:
    parts = []
    for f in logs_dir.glob("*_meta.csv"):
        if f.stat().st_size == 0:
            continue
        df = pd.read_csv(f)
        if set(_META_HEADER).issubset(df.columns):
            parts.append(df[_META_HEADER].copy())
    if parts:
        return pd.concat(parts, ignore_index=True)
    return pd.DataFrame(columns=_META_HEADER)

def _latest_run_ids(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["solver_alias","instance","run_id"])
    return df.groupby(["solver_alias","instance"])["run_id"].max().reset_index()

def _build_summary_from_events_and_meta(df_ev: pd.DataFrame, df_meta: pd.DataFrame) -> pd.DataFrame:
    cols = ["solver_tag","solver_alias","solver_cmd","instance","run_id",
            "final_cost","time_to_best_sec","optimum_found","exit_code"]
    if df_ev.empty and df_meta.empty:
        return pd.DataFrame(columns=cols)

    if not df_ev.empty:
        last = (df_ev
                .sort_values(["solver_alias","instance","run_id","event_idx"])
                .groupby(["solver_alias","instance","run_id"], as_index=False)
                .tail(1))
        last = last.rename(columns={"cost":"final_cost","elapsed_sec":"time_to_best_sec"})
        keep = ["solver_tag","solver_alias","solver_cmd","instance","run_id","final_cost","time_to_best_sec"]
        last = last[keep]
    else:
        last = pd.DataFrame(columns=["solver_tag","solver_alias","solver_cmd","instance","run_id","final_cost","time_to_best_sec"])

    if df_meta.empty:
        out = last.copy()
        out["optimum_found"] = None
        out["exit_code"] = None
        return out[cols].reset_index(drop=True)

    out = last.merge(
        df_meta[["solver_tag","solver_alias","solver_cmd","instance","run_id","optimum_found","exit_code"]],
        on=["solver_tag","solver_alias","solver_cmd","instance","run_id"],
        how="outer"
    )
    mask_missing = out["final_cost"].isna() & out["time_to_best_sec"].isna()
    if mask_missing.any():
        out.loc[mask_missing, ["final_cost","time_to_best_sec"]] = [None, None]
    return out[cols].reset_index(drop=True)

def load_runs(runs_dir: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Si 'runs_dir/logs/' existe:
      - reconstruit 'trajectories.csv' et 'summary.csv' avec **seulement le dernier run_id**
        pour chaque (solver_alias × instance), puis écrase ces fichiers.
    Sinon: lit les CSV présents (legacy).
    """
    runs_dir = Path(runs_dir)
    logs_dir = runs_dir / "logs"

    if logs_dir.exists() and logs_dir.is_dir():
        df_ev_all = _read_all_events(logs_dir)
        df_meta_all = _read_all_meta(logs_dir)
        if df_ev_all.empty and df_meta_all.empty:
            raise FileNotFoundError(f"Aucun log dans {logs_dir}")

        base_for_latest = df_ev_all if not df_ev_all.empty else df_meta_all
        latest = _latest_run_ids(base_for_latest)

        def _join_latest(df):
            if df.empty:
                return df
            return df.merge(latest, on=["solver_alias","instance","run_id"], how="inner")

        df_ev_latest = _join_latest(df_ev_all)
        df_meta_latest = _join_latest(df_meta_all)

        traj_csv = runs_dir / "trajectories.csv"
        if not df_ev_latest.empty:
            if "basename" not in df_ev_latest.columns:
                df_ev_latest["basename"] = df_ev_latest["instance"].apply(lambda p: Path(str(p)).name)
            df_ev_latest[_EVENTS_HEADER + ["basename"]].to_csv(traj_csv, index=False)
        else:
            pd.DataFrame(columns=_EVENTS_HEADER + ["basename"]).to_csv(traj_csv, index=False)

        df_sum_latest = _build_summary_from_events_and_meta(df_ev_latest, df_meta_latest)
        sum_csv = runs_dir / "summary.csv"
        df_sum_latest.to_csv(sum_csv, index=False)

        return pd.read_csv(traj_csv), pd.read_csv(sum_csv)

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
    plt.boxplot(data, tick_labels=labels, showfliers=False)
    plt.xticks(rotation=45, ha="right")
    plt.ylabel("Time to best (sec)")
    plt.title("Temps au meilleur (distribution)")
    plt.tight_layout()
    _savefig(out_png)

# ===================== Trajectoires coût(t) par instance =====================

def plot_trajectory_for_instance(
    df_traj: pd.DataFrame,
    instance_basename: str,
    out_png: Path,
    by: str = "solver_alias",
) -> None:
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
    df_meta_all = _read_all_meta(logs_dir)  # pas indispensable ici, on se base sur events

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
        xs = range(len(g))
        vals = g["mean_final_cost"].tolist()
        errs = g["std_final_cost"].fillna(0.0).tolist()
        labels = g["instance"].astype(str).tolist()

        plt.figure(figsize=(max(8, 0.5*len(vals)), 4.8), dpi=150)
        plt.bar(xs, vals, yerr=errs, capsize=3)
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
) -> dict:
    """
    - (Re)build trajectories/summary (dernier run par solver×instance)
    - Leaderboards (classique + relatif)
    - Trajectoires coût/temps (par instance)
    - Scores(t) par instance
    - Average scores over time
    - **Réplicas par solveur**: CSV + galerie de PNG (1 par solveur, moy ± écart-type sur final_cost par instance)
    """
    # 1) rebuild
    df_traj, df_sum = load_runs(runs_dir)

    # 2) Leaderboard simple
    lb = compute_leaderboard(df_sum, by=by)
    out_dir.mkdir(parents=True, exist_ok=True)
    lb_csv = out_dir / "leaderboard.csv"
    lb.to_csv(lb_csv, index=False)
    plot_leaderboard_wins(lb, out_dir / "plot_leaderboard_wins.png", by=by)
    plot_time_to_best_box(df_sum, out_dir / "plot_time_to_best_box.png", by=by)

    # 3) Leaderboard relatif (fenêtre temporelle)
    lb_rel = aggregate_relative_leaderboard(df_traj, by=by, t_min=t_min, t_max=t_max, t_at=t_at)
    lb_rel_csv = out_dir / "leaderboard_relative.csv"
    lb_rel.to_csv(lb_rel_csv, index=False)

    # 4) Trajectoires coût/temps
    traj_png = None
    if instance_basename:
        traj_png = out_dir / f"plot_trajectory_{instance_basename}.png"
        plot_trajectory_for_instance(df_traj, instance_basename, traj_png, by=by)
    instance_cost_plots = plot_all_instances(df_traj, out_dir, by=by) if per_instance else []

    # 5) Scores(t) par instance
    instance_score_plots = plot_all_instances_scores(df_traj, out_dir, by=by, t_min=t_min, t_max=t_max)

    # 6) Moyennes temporelles globales
    finals = generate_final_score_summary(df_traj, out_dir=out_dir, by=by, t_min=t_min, t_max=t_max)

    # 7) Réplicas **par solveur**
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
        # Réplicas par solveur
        "replicas_by_solver_csv": str(rep_csv),
        "replicas_by_solver_plots": rep_solver_plots,
    }