from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import math
import heapq

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import matplotlib.ticker as mtick
from matplotlib.ticker import FixedLocator, FormatStrFormatter

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
    plt.subplots_adjust(bottom=0.25)


def _savefig(png: Path) -> None:
    png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(png, bbox_inches="tight")
    plt.close()


def _basename_series(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
         .str.replace("\\", "/", regex=False)
         .str.rsplit("/", n=1)
         .str[-1]
    )


def _clip_score(x: float) -> float:
    if not math.isfinite(x):
        return 0.0
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)


# ============ 1) Segments par instance ============

def collect_instance_segments(
    df_traj: pd.DataFrame,
    by: str = "solver_alias",
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
) -> pd.DataFrame:
    """Concatène les segments score(t) de toutes les instances."""
    df = df_traj.copy()
    df["basename"] = _basename_series(df["instance"])
    instances = sorted(df["basename"].unique())

    parts: List[pd.DataFrame] = []
    for inst in instances:
        seg = compute_relative_scores_timewindow_for_instance(
            df_traj, inst, by=by, t_min=t_min, t_max=t_max
        )
        if not seg.empty:
            parts.append(seg)

    if not parts:
        return pd.DataFrame(
            columns=["instance", "t_start", "t_end", "duration", "solver", "score", "cost", "best_cost"]
        )

    all_seg = pd.concat(parts, ignore_index=True)
    all_seg["t_start"] = pd.to_numeric(all_seg["t_start"], errors="coerce").astype(float)
    all_seg["t_end"] = pd.to_numeric(all_seg["t_end"], errors="coerce").astype(float)
    all_seg["score"] = pd.to_numeric(all_seg["score"], errors="coerce").astype(float)
    return all_seg


# ============ 2) Grille temporelle globale ============

def build_time_grid(segments_df: pd.DataFrame) -> List[float]:
    """Union triée des bornes t_start/t_end sur toutes instances (dé-doublonnée)."""
    if segments_df.empty:
        return []

    arr = np.concatenate([
        pd.to_numeric(segments_df["t_start"], errors="coerce").to_numpy(dtype=float),
        pd.to_numeric(segments_df["t_end"], errors="coerce").to_numpy(dtype=float),
    ])

    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return []

    arr.sort()
    eps = 1e-12

    keep = np.empty(arr.size, dtype=bool)
    keep[0] = True
    keep[1:] = np.diff(arr) > eps

    return arr[keep].tolist()


# ============ 3) Noyau rapide partagé ============

def _compute_time_stats_over_time(
    segments_df: pd.DataFrame,
    by: str = "solver_alias",
) -> pd.DataFrame:
    """
    Retourne un DataFrame long avec colonnes:
    ['t', by, 'mean', 'min', 'max', 'n_instances'].

    Une seule passe par solver avec sweep d'événements.
    """
    if segments_df.empty:
        return pd.DataFrame(columns=["t", by, "mean", "min", "max", "n_instances"])

    seg = segments_df.copy()

    required = {"instance", "t_start", "t_end", "score"}
    missing = required - set(seg.columns)
    if missing:
        raise KeyError(f"Colonnes manquantes: {sorted(missing)}")

    seg["basename"] = _basename_series(seg["instance"])

    label_col = by if by in seg.columns else ("solver" if "solver" in seg.columns else None)
    if label_col is None:
        raise KeyError(f"Aucune colonne '{by}' ni 'solver' trouvée dans segments_df.")

    seg["t_start"] = pd.to_numeric(seg["t_start"], errors="coerce")
    seg["t_end"] = pd.to_numeric(seg["t_end"], errors="coerce")
    seg["score"] = pd.to_numeric(seg["score"], errors="coerce")

    seg = seg[
        seg["t_start"].notna()
        & seg["t_end"].notna()
        & (seg["t_end"] > seg["t_start"])
    ].copy()

    if seg.empty:
        return pd.DataFrame(columns=["t", by, "mean", "min", "max", "n_instances"])

    seg["score"] = seg["score"].map(_clip_score)

    T = np.asarray(build_time_grid(seg), dtype=float)
    if T.size == 0:
        return pd.DataFrame(columns=["t", by, "mean", "min", "max", "n_instances"])

    n_intervals = T.size - 1
    if n_intervals < 0:
        return pd.DataFrame(columns=["t", by, "mean", "min", "max", "n_instances"])

    pieces: List[pd.DataFrame] = []

    for label, g in seg.groupby(label_col, sort=True):
        starts = g["t_start"].to_numpy(dtype=float, copy=False)
        ends = g["t_end"].to_numpy(dtype=float, copy=False)
        instances = g["basename"].astype(str).to_numpy(copy=False)
        scores = g["score"].to_numpy(dtype=float, copy=False)

        i0 = np.searchsorted(T, starts, side="left")
        i1 = np.searchsorted(T, ends, side="left")

        # On ne garde que les segments couvrant au moins un intervalle [T[k], T[k+1])
        valid = (i0 < i1) & (i0 < n_intervals)
        if not np.any(valid):
            # Même comportement que l'ancien code: snapshot final uniquement
            if T.size > 0:
                pieces.append(pd.DataFrame({
                    "t": [T[-1]],
                    by: [label],
                    "mean": [float("nan")],
                    "min": [float("nan")],
                    "max": [float("nan")],
                    "n_instances": [None],
                }))
            continue

        i0 = i0[valid]
        i1 = i1[valid]
        instances = instances[valid]
        scores = scores[valid]

        # events[idx] = list[(kind, instance, score)]
        # kind: 0 = remove, 1 = add
        events: List[List[Tuple[int, str, float]]] = [[] for _ in range(n_intervals)]
        for start_idx, end_idx, inst, sc in zip(i0, i1, instances, scores):
            if 0 <= start_idx < n_intervals:
                events[start_idx].append((1, inst, float(sc)))
            if 0 <= end_idx < n_intervals:
                events[end_idx].append((0, inst, 0.0))

        mean_arr = np.full(n_intervals, np.nan, dtype=float)
        min_arr = np.full(n_intervals, np.nan, dtype=float)
        max_arr = np.full(n_intervals, np.nan, dtype=float)
        cnt_arr = np.zeros(n_intervals, dtype=np.int64)

        # Active set
        active: Dict[str, Tuple[float, int]] = {}
        versions: Dict[str, int] = {}
        min_heap: List[Tuple[float, str, int]] = []
        max_heap: List[Tuple[float, str, int]] = []

        sum_active = 0.0
        count_active = 0

        def _push_active(inst: str, sc: float) -> None:
            nonlocal sum_active, count_active

            old = active.pop(inst, None)
            if old is not None:
                sum_active -= old[0]
                count_active -= 1

            ver = versions.get(inst, 0) + 1
            versions[inst] = ver
            active[inst] = (sc, ver)

            sum_active += sc
            count_active += 1

            heapq.heappush(min_heap, (sc, inst, ver))
            heapq.heappush(max_heap, (-sc, inst, ver))

        def _remove_active(inst: str) -> None:
            nonlocal sum_active, count_active

            old = active.pop(inst, None)
            if old is not None:
                sum_active -= old[0]
                count_active -= 1

            versions[inst] = versions.get(inst, 0) + 1

        def _clean_min_heap() -> None:
            while min_heap:
                sc, inst, ver = min_heap[0]
                cur = active.get(inst)
                if cur is not None and cur == (sc, ver):
                    break
                heapq.heappop(min_heap)

        def _clean_max_heap() -> None:
            while max_heap:
                neg_sc, inst, ver = max_heap[0]
                cur = active.get(inst)
                if cur is not None and cur == (-neg_sc, ver):
                    break
                heapq.heappop(max_heap)

        for k in range(n_intervals):
            # remove avant add pour respecter [t_start, t_end)
            if events[k]:
                for kind, inst, sc in events[k]:
                    if kind == 0:
                        _remove_active(inst)
                for kind, inst, sc in events[k]:
                    if kind == 1:
                        _push_active(inst, sc)

            if count_active > 0:
                mean_arr[k] = sum_active / count_active
                cnt_arr[k] = count_active

                _clean_min_heap()
                _clean_max_heap()

                if min_heap:
                    min_arr[k] = min_heap[0][0]
                if max_heap:
                    max_arr[k] = -max_heap[0][0]

        last_mean = mean_arr[np.isfinite(mean_arr)][-1] if np.isfinite(mean_arr).any() else float("nan")

        if n_intervals > 0:
            pieces.append(pd.DataFrame({
                "t": T[:-1],
                by: label,
                "mean": mean_arr,
                "min": min_arr,
                "max": max_arr,
                "n_instances": cnt_arr,
            }))

        pieces.append(pd.DataFrame({
            "t": [T[-1]],
            by: [label],
            "mean": [last_mean],
            "min": [float("nan")],
            "max": [float("nan")],
            "n_instances": [None],
        }))

    if not pieces:
        return pd.DataFrame(columns=["t", by, "mean", "min", "max", "n_instances"])

    return (
        pd.concat(pieces, ignore_index=True)
        .sort_values(["t", by], kind="stable")
        .reset_index(drop=True)
    )


def compute_avg_scores_over_time(
    segments_df: pd.DataFrame,
    by: str = "solver_alias",
) -> pd.DataFrame:
    """
    DataFrame long: colonnes ['t', by, 'avg_score', 'n_instances'].
    À chaque t de la grille globale, moyenne du score par solver sur les instances couvrantes.
    """
    stats = _compute_time_stats_over_time(segments_df, by=by)
    if stats.empty:
        return pd.DataFrame(columns=["t", by, "avg_score", "n_instances"])

    return (
        stats[["t", by, "mean", "n_instances"]]
        .rename(columns={"mean": "avg_score"})
        .reset_index(drop=True)
    )


def compute_score_distribution_over_time(
    segments_df: pd.DataFrame,
    by: str = "solver_alias",
) -> pd.DataFrame:
    """
    DataFrame long: colonnes
    ['t', by, 'mean', 'min', 'max', 'n_instances'].
    """
    return _compute_time_stats_over_time(segments_df, by=by)


# ============ 4) Plot + CSV + Driver ============

def plot_avg_scores_over_time(
    ts_df: pd.DataFrame,
    out_png: Path,
    by: str = "solver_alias",
    log_time: bool = False,
) -> None:
    if ts_df.empty:
        return

    plt.figure(figsize=(12, 10), dpi=150)
    ax = plt.gca()

    xs_all = ts_df.loc[~ts_df["avg_score"].isna(), "t"].astype(float).to_numpy()
    use_log = False
    use_log1p = False
    if log_time:
        if np.any(xs_all <= 0.0):
            use_log1p = True
        else:
            use_log = True

    x_min = x_max = None

    def xmap(x: np.ndarray) -> np.ndarray:
        if use_log1p:
            return np.log1p(x)
        return x

    for s, g in ts_df.groupby(by):
        g = g.sort_values("t")
        g = g[~g["avg_score"].isna()]
        if g.empty:
            continue
        xs = g["t"].astype(float).to_numpy()
        ys = g["avg_score"].astype(float).to_numpy()

        xs_plot = xmap(xs)
        lxmin, lxmax = float(xs_plot.min()), float(xs_plot.max())
        x_min = lxmin if x_min is None else min(x_min, lxmin)
        x_max = lxmax if x_max is None else max(x_max, lxmax)

        line, = ax.step(xs_plot, ys, where="post", linewidth=1.0, label=str(s))
        color = line.get_color()
        ax.plot(xs_plot, ys, ".", linewidth=0, color=color)

    BETA = 0.4
    MARGIN = 0.05

    def y_forward(y):
        y = np.asarray(y, dtype=float)
        out = np.empty_like(y)

        mask_mid = (y >= 0.0) & (y <= 1.0)
        out[mask_mid] = 1.0 - (1.0 - y[mask_mid]) ** BETA

        mask_low = (y < 0.0)
        out[mask_low] = y[mask_low] * (0.1 / MARGIN)

        mask_high = (y > 1.0)
        out[mask_high] = 1.0 + (y[mask_high] - 1.0) * (0.1 / MARGIN)

        return out

    def y_inverse(z):
        z = np.asarray(z, dtype=float)
        out = np.empty_like(z)

        mask_mid = (z >= 0.0) & (z <= 1.0)
        out[mask_mid] = 1.0 - (1.0 - z[mask_mid]) ** (1.0 / BETA)

        mask_low = (z < 0.0)
        out[mask_low] = z[mask_low] * (MARGIN / 0.1)

        mask_high = (z > 1.0)
        out[mask_high] = 1.0 + (z[mask_high] - 1.0) * (MARGIN / 0.1)

        return out

    ax.set_yscale("function", functions=(y_forward, y_inverse))
    ax.set_ylim(-MARGIN, 1.0 + MARGIN)

    ax.yaxis.set_major_locator(FixedLocator([0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 0.98, 0.99, 1.0]))
    ax.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))
    ax.grid(True, which="both", alpha=0.25)
    ax.set_ylabel("Average relative score (best/cost)")

    if x_min is not None and x_max is not None:
        span = max(1e-12, x_max - x_min)
        pad = max(0.03 * span, 1e-3)
        if use_log:
            left = max(1e-12, x_min)
            right = max(left * 1.1, x_max)
            ax.set_xscale("log", base=10)
            ax.set_xlim(left - pad, right + pad + 10)
            ax.xaxis.set_major_locator(mtick.LogLocator(base=10))
            ax.xaxis.set_minor_locator(mtick.LogLocator(base=10, subs=np.arange(2, 10) * 0.1))
            ax.xaxis.set_minor_formatter(mtick.NullFormatter())
            ax.set_xlabel("Elapsed (sec, log)")
        else:
            ax.set_xlim(x_min - pad, x_max + pad)
            ax.set_xlabel("log1p(Elapsed sec)" if use_log1p else "Elapsed (sec)")

    ax.set_title("Average scores over time (per solver)")
    _legend_bottom()
    _savefig(out_png)


def save_avg_scores_csv(ts_df: pd.DataFrame, out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    ts_df.to_csv(out_csv, index=False)


# ============ 5) Plot distribution (mean + min–max) ============

def plot_score_distribution_over_time(
    dist_df: pd.DataFrame,
    out_png: Path,
    by: str = "solver_alias",
    log_time: bool = False,
) -> None:
    if dist_df.empty:
        return

    plt.figure(figsize=(12, 10), dpi=150)
    ax = plt.gca()

    xs_all = dist_df.loc[~dist_df["mean"].isna(), "t"].astype(float).to_numpy()
    use_log = False
    use_log1p = False
    if log_time:
        if np.any(xs_all <= 0.0):
            use_log1p = True
        else:
            use_log = True

    x_min = x_max = None

    def xmap(x: np.ndarray) -> np.ndarray:
        return np.log1p(x) if use_log1p else x

    for s, g in dist_df.groupby(by):
        g = g.sort_values("t")
        g = g[~g["mean"].isna()]
        if g.empty:
            continue

        xs = g["t"].astype(float).to_numpy()
        mean = g["mean"].astype(float).to_numpy()
        vmin = g["min"].astype(float).to_numpy()
        vmax = g["max"].astype(float).to_numpy()

        xs_plot = xmap(xs)
        lxmin, lxmax = float(xs_plot.min()), float(xs_plot.max())
        x_min = lxmin if x_min is None else min(x_min, lxmin)
        x_max = lxmax if x_max is None else max(x_max, lxmax)

        valid_band = ~(np.isnan(vmin) | np.isnan(vmax))

        line, = ax.step(xs_plot, mean, where="post", linewidth=1.0, label=str(s))
        color = line.get_color()
        ax.plot(xs_plot, mean, ".", linewidth=0, color=color)

        if np.any(valid_band):
            ax.fill_between(
                xs_plot[valid_band],
                vmin[valid_band],
                vmax[valid_band],
                step="post",
                alpha=0.12,
                color=color,
            )

            ax.step(
                xs_plot[valid_band], vmin[valid_band],
                where="post", linewidth=0.6, linestyle="--", color=color, alpha=0.7
            )
            ax.step(
                xs_plot[valid_band], vmax[valid_band],
                where="post", linewidth=0.6, linestyle="--", color=color, alpha=0.7
            )

    BETA = 0.4
    MARGIN = 0.05

    def y_forward(y):
        y = np.asarray(y, dtype=float)
        out = np.empty_like(y)
        mask_mid = (y >= 0.0) & (y <= 1.0)
        out[mask_mid] = 1.0 - (1.0 - y[mask_mid]) ** BETA
        mask_low = (y < 0.0)
        out[mask_low] = y[mask_low] * (0.1 / MARGIN)
        mask_high = (y > 1.0)
        out[mask_high] = 1.0 + (y[mask_high] - 1.0) * (0.1 / MARGIN)
        return out

    def y_inverse(z):
        z = np.asarray(z, dtype=float)
        out = np.empty_like(z)
        mask_mid = (z >= 0.0) & (z <= 1.0)
        out[mask_mid] = 1.0 - (1.0 - z[mask_mid]) ** (1.0 / BETA)
        mask_low = (z < 0.0)
        out[mask_low] = z[mask_low] * (MARGIN / 0.1)
        mask_high = (z > 1.0)
        out[mask_high] = 1.0 + (z[mask_high] - 1.0) * (MARGIN / 0.1)
        return out

    ax.set_yscale("function", functions=(y_forward, y_inverse))
    ax.set_ylim(-MARGIN, 1.0 + MARGIN)
    ax.yaxis.set_major_locator(FixedLocator([0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 0.98, 0.99, 1.0]))
    ax.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))
    ax.grid(True, which="both", alpha=0.25)
    ax.set_ylabel("Relative score (mean with min–max band)")

    if x_min is not None and x_max is not None:
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

    ax.set_title("Score range over time (per solver) — mean ± [min,max]")
    _legend_bottom()
    _savefig(out_png)


def save_score_distribution_csv(dist_df: pd.DataFrame, out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    dist_df.to_csv(out_csv, index=False)


# ============ 6) Driver ============

def generate_final_score_summary(
    df_traj: pd.DataFrame,
    out_dir: Path,
    by: str = "solver_alias",
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
    log_time: bool = False,
) -> Dict[str, str]:
    """
    Pipeline: segments → stats → CSV+PNG.
    """
    seg = collect_instance_segments(df_traj, by=by, t_min=t_min, t_max=t_max)
    print("...instance segments collected...")

    out_dir.mkdir(parents=True, exist_ok=True)

    # Une seule passe lourde
    dist = compute_score_distribution_over_time(seg, by=by)
    print("...compute score distribution over time...")
    

    ts = (
        dist[["t", by, "mean", "n_instances"]]
        .rename(columns={"mean": "avg_score"})
        .reset_index(drop=True)
    )

    out_csv = out_dir / "average_scores_over_time.csv"
    out_png = out_dir / "average_scores_over_time.png"
    save_avg_scores_csv(ts, out_csv)
    plot_avg_scores_over_time(ts, out_png, by=by, log_time=log_time)

    dist_csv = out_dir / "score_distribution_over_time.csv"
    dist_png = out_dir / "score_distribution_over_time.png"
    save_score_distribution_csv(dist, dist_csv)
    plot_score_distribution_over_time(dist, dist_png, by=by, log_time=log_time)

    return {
        "avg_scores_csv": str(out_csv),
        "avg_scores_png": str(out_png),
        "score_dist_csv": str(dist_csv),
        "score_dist_png": str(dist_png),
    }