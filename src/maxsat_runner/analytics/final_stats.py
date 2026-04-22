from __future__ import annotations
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
    if pd.isna(x):
        return float("nan")
    if not math.isfinite(x):
        return float("nan")
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)


def _nice_upper_bound(x_max: float, factor: float = 4.0 / 3.0) -> float:
    """
    Calcule une borne droite 'propre' et lisible pour l'axe du temps.
    """
    if not math.isfinite(x_max):
        return 1.0
    if x_max <= 0.0:
        return 1.0

    target = x_max * factor
    exp = math.floor(math.log10(target))
    base = 10.0 ** exp
    mant = target / base

    nice_mantissas = [1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]
    for m in nice_mantissas:
        if mant <= m + 1e-12:
            return m * base

    return 10.0 * base


def _apply_time_axis_with_right_margin(
    ax: plt.Axes,
    raw_x_min: Optional[float],
    raw_x_max: Optional[float],
    use_log: bool,
    use_log1p: bool,
) -> None:
    """
    Applique une borne gauche et droite plus lisible sur l'axe du temps.
    """
    if raw_x_min is None or raw_x_max is None:
        return

    right_raw = _nice_upper_bound(raw_x_max, factor=4.0 / 3.0)

    if use_log:
        left_raw = max(1e-12, raw_x_min / 1.25)
        right_raw = max(right_raw, left_raw * 1.1)

        ax.set_xscale("log", base=10)
        ax.set_xlim(left_raw, right_raw)
        ax.xaxis.set_major_locator(mtick.LogLocator(base=10))
        ax.xaxis.set_minor_locator(mtick.LogLocator(base=10, subs=np.arange(2, 10) * 0.1))
        ax.xaxis.set_minor_formatter(mtick.NullFormatter())
        ax.set_xlabel("Elapsed (sec, log)")
        return

    span = max(1e-12, raw_x_max - raw_x_min)
    left_pad = max(0.03 * max(raw_x_max, span), 1e-3)
    left_raw = raw_x_min - left_pad

    if use_log1p:
        left_raw = max(left_raw, -0.5)
        ax.set_xlim(np.log1p(left_raw), np.log1p(right_raw))
        ax.set_xlabel("log1p(Elapsed sec)")
    else:
        ax.set_xlim(left_raw, right_raw)
        ax.set_xlabel("Elapsed (sec)")


def _short_solver_name(name: str) -> str:
    """
    Alias courts pour les tableaux / figures récapitulatives.
    """
    name = str(name)
    mapping = {
        "EvalMaxSAT_anytime": "EvalMaxSAT",
        "EvalMaxSAT_anytime_withoutring": "EvalMaxSAT-noRing",
        "SPB_Maxsat": "SPB",
        "SPB_Maxsat_BAND": "SPB-Band",
        "SPB_Maxsat_FPS": "SPB-FPS",
        "tt-open-wbo-inc": "tt-open-wbo",
        "tt_open_wbo": "tt-open-wbo",
        "nuwls_2024": "NuWLS-2024",
    }
    return mapping.get(name, name)


def _resolve_common_time_window(
    df_traj: pd.DataFrame,
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
) -> Tuple[float, float]:
    """
    Résout une fenêtre temporelle commune à tout le dataset.
    Si t_min / t_max sont fournis, ils priment.
    Sinon on utilise min/max globaux de elapsed_sec.
    """
    if "elapsed_sec" not in df_traj.columns:
        raise KeyError("La colonne 'elapsed_sec' est requise dans df_traj.")

    times = pd.to_numeric(df_traj["elapsed_sec"], errors="coerce")
    times = times[np.isfinite(times.to_numpy(dtype=float))]
    if times.empty:
        raise ValueError("Impossible de résoudre l'horizon global: aucun elapsed_sec valide.")

    global_min = float(times.min())
    global_max = float(times.max())

    lo = global_min if t_min is None else float(t_min)
    hi = global_max if t_max is None else float(t_max)

    if hi <= lo:
        raise ValueError(f"Fenêtre temporelle invalide: t_min={lo}, t_max={hi}.")

    return lo, hi


# ============ 1) Segments par instance ============

def collect_instance_segments(
    df_traj: pd.DataFrame,
    by: str = "solver_alias",
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
) -> pd.DataFrame:
    """
    Concatène les segments score(t) de toutes les instances sur une
    fenêtre temporelle commune [t_min, t_max].
    """
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
    """Union triée des bornes t_start/t_end sur toutes les instances."""
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
    min_n_instances: Optional[int] = None,
) -> pd.DataFrame:
    """
    Retourne un DataFrame long avec colonnes :
    ['t', by, 'mean', 'min', 'max', 'n_instances'].
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
    seg["score"] = pd.to_numeric(seg["score"], errors="coerce").map(_clip_score)

    seg = seg[
        seg["t_start"].notna()
        & seg["t_end"].notna()
        & (seg["t_end"] > seg["t_start"])
        & seg["score"].notna()
    ].copy()

    if seg.empty:
        return pd.DataFrame(columns=["t", by, "mean", "min", "max", "n_instances"])

    T = np.asarray(build_time_grid(seg), dtype=float)
    if T.size == 0:
        return pd.DataFrame(columns=["t", by, "mean", "min", "max", "n_instances"])

    n_intervals = T.size - 1
    if n_intervals <= 0:
        return pd.DataFrame(columns=["t", by, "mean", "min", "max", "n_instances"])

    pieces: List[pd.DataFrame] = []

    for label, g in seg.groupby(label_col, sort=True):
        starts = g["t_start"].to_numpy(dtype=float, copy=False)
        ends = g["t_end"].to_numpy(dtype=float, copy=False)
        instances = g["basename"].astype(str).to_numpy(copy=False)
        scores = g["score"].to_numpy(dtype=float, copy=False)

        i0 = np.searchsorted(T, starts, side="left")
        i1 = np.searchsorted(T, ends, side="left")

        valid = (i0 < i1) & (i0 < n_intervals)
        if not np.any(valid):
            continue

        i0 = i0[valid]
        i1 = i1[valid]
        instances = instances[valid]
        scores = scores[valid]

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

        if min_n_instances is not None:
            mask_low = cnt_arr < int(min_n_instances)
            mean_arr[mask_low] = np.nan
            min_arr[mask_low] = np.nan
            max_arr[mask_low] = np.nan

        finite_mean = np.isfinite(mean_arr)
        finite_min = np.isfinite(min_arr)
        finite_max = np.isfinite(max_arr)
        positive_cnt = cnt_arr > 0

        last_mean = mean_arr[finite_mean][-1] if finite_mean.any() else float("nan")
        last_min = min_arr[finite_min][-1] if finite_min.any() else float("nan")
        last_max = max_arr[finite_max][-1] if finite_max.any() else float("nan")
        last_cnt = int(cnt_arr[positive_cnt][-1]) if positive_cnt.any() else 0

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
            "min": [last_min],
            "max": [last_max],
            "n_instances": [last_cnt],
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
    min_n_instances: Optional[int] = None,
) -> pd.DataFrame:
    """
    DataFrame long : ['t', by, 'avg_score', 'n_instances'].
    """
    stats = _compute_time_stats_over_time(
        segments_df,
        by=by,
        min_n_instances=min_n_instances,
    )
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
    min_n_instances: Optional[int] = None,
) -> pd.DataFrame:
    """
    DataFrame long : ['t', by, 'mean', 'min', 'max', 'n_instances'].
    """
    return _compute_time_stats_over_time(
        segments_df,
        by=by,
        min_n_instances=min_n_instances,
    )


# ============ 4) Métriques globales type AUC ============

def _prepare_auc_curve(
    ts_df: pd.DataFrame,
    solver_value,
    by: str = "solver_alias",
) -> pd.DataFrame:
    """Prépare une courbe propre, triée, sans doublons, pour un solveur."""
    g = ts_df[ts_df[by] == solver_value].copy()
    if g.empty:
        return pd.DataFrame(columns=["t", "avg_score"])

    g["t"] = pd.to_numeric(g["t"], errors="coerce")
    g["avg_score"] = pd.to_numeric(g["avg_score"], errors="coerce")
    g = g.dropna(subset=["t", "avg_score"]).sort_values("t")
    g = g.drop_duplicates(subset=["t"], keep="last")

    if g.empty:
        return pd.DataFrame(columns=["t", "avg_score"])

    g["avg_score"] = g["avg_score"].map(_clip_score)
    return g[["t", "avg_score"]].reset_index(drop=True)


def _step_auc_linear(t: np.ndarray, y: np.ndarray) -> float:
    """
    Aire exacte pour une courbe step-post :
    y[i] est la valeur sur [t[i], t[i+1]).
    """
    if t.size < 2:
        return float("nan")

    dt = np.diff(t)
    if np.any(dt < 0):
        return float("nan")

    return float(np.sum(y[:-1] * dt))


def _step_auc_log(t: np.ndarray, y: np.ndarray) -> float:
    """
    Aire exacte pour une courbe step-post intégrée par rapport à ln(t).
    """
    mask = t > 0.0
    t_pos = t[mask]
    y_pos = y[mask]

    if t_pos.size < 2:
        return float("nan")

    u = np.log(t_pos)
    du = np.diff(u)
    if np.any(du < 0):
        return float("nan")

    return float(np.sum(y_pos[:-1] * du))


def compute_auc_scores(
    ts_df: pd.DataFrame,
    by: str = "solver_alias",
) -> pd.DataFrame:
    """
    Calcule, pour chaque solveur :
      - auc_linear
      - sdt
      - auc_log
      - sdt_log
    """
    if ts_df.empty:
        return pd.DataFrame(columns=[by, "auc_linear", "sdt", "auc_log", "sdt_log"])

    solver_values = sorted(ts_df[by].dropna().unique(), key=lambda x: str(x))
    rows: List[Dict[str, object]] = []

    for solver_value in solver_values:
        g = _prepare_auc_curve(ts_df, solver_value, by=by)
        if g.empty:
            continue

        t = g["t"].to_numpy(dtype=float)
        y = g["avg_score"].to_numpy(dtype=float)

        auc_linear = float("nan")
        sdt = float("nan")
        auc_log = float("nan")
        sdt_log = float("nan")

        if t.size >= 2 and t[-1] > t[0]:
            auc_linear = _step_auc_linear(t, y)
            if math.isfinite(auc_linear):
                sdt = float(auc_linear / (t[-1] - t[0]))

        mask = t > 0.0
        if np.count_nonzero(mask) >= 2:
            t_pos = t[mask]
            auc_log = _step_auc_log(t, y)
            if math.isfinite(auc_log):
                log_span = float(np.log(t_pos[-1]) - np.log(t_pos[0]))
                if log_span > 0:
                    sdt_log = float(auc_log / log_span)

        rows.append({
            by: solver_value,
            "auc_linear": auc_linear,
            "sdt": sdt,
            "auc_log": auc_log,
            "sdt_log": sdt_log,
        })

    out = pd.DataFrame(rows, columns=[by, "auc_linear", "sdt", "auc_log", "sdt_log"])
    if out.empty:
        return out

    return out.sort_values(["sdt", "sdt_log"], ascending=[False, False]).reset_index(drop=True)


def save_auc_scores_csv(auc_df: pd.DataFrame, out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    auc_df.to_csv(out_csv, index=False)


def plot_auc_scores_table(
    auc_df: pd.DataFrame,
    out_png: Path,
    by: str = "solver_alias",
) -> None:
    """
    Figure séparée contenant uniquement le tableau des métriques AUC / SDT.
    """
    if auc_df.empty:
        return

    disp = auc_df.copy()
    disp[by] = disp[by].map(lambda x: _short_solver_name(str(x)))

    disp = disp.rename(columns={
        by: "Solver",
        "auc_linear": "AUC",
        "sdt": "SDT",
        "auc_log": "AUC-log",
        "sdt_log": "SDT-log",
    })

    for col in ["AUC", "SDT", "AUC-log", "SDT-log"]:
        disp[col] = disp[col].map(lambda x: f"{x:.5f}" if pd.notna(x) else "NA")

    n_rows = len(disp)
    n_cols = len(disp.columns)

    # largeur un peu plus grande si les noms de solveurs sont longs
    max_solver_len = disp["Solver"].astype(str).map(len).max() if not disp.empty else 10
    fig_width = max(10.5, min(16, 8 + 0.12 * max_solver_len))
    fig_height = max(2.8, 0.55 * (n_rows + 1))

    fig, ax = plt.subplots(figsize=(fig_width, fig_height), dpi=150)
    ax.axis("off")
    ax.set_title("Temporal domination metrics (AUC / SDT)", fontsize=13, pad=12)

    table = ax.table(
        cellText=disp.values.tolist(),
        colLabels=list(disp.columns),
        cellLoc="center",
        loc="center",
        bbox=[0.02, 0.00, 0.96, 0.90],
    )

    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.0, 1.25)

    # Ajustement automatique de la largeur des colonnes
    try:
        table.auto_set_column_width(col=list(range(n_cols)))
    except Exception:
        pass

    # Aligner la colonne Solver à gauche pour améliorer la lisibilité
    solver_col_idx = list(disp.columns).index("Solver")
    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_text_props(weight="bold")
        if col == solver_col_idx:
            cell.get_text().set_ha("left")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_png, bbox_inches="tight")
    plt.close(fig)

# ============ 5) Plot + CSV + Driver ============

def plot_avg_scores_over_time(
    ts_df: pd.DataFrame,
    out_png: Path,
    by: str = "solver_alias",
    log_time: bool = False,
) -> None:
    if ts_df.empty:
        return

    plt.figure(figsize=(16, 10), dpi=150)
    ax = plt.gca()

    xs_all = ts_df.loc[~ts_df["avg_score"].isna(), "t"].astype(float).to_numpy()
    use_log = False
    use_log1p = False
    if log_time:
        if np.any(xs_all <= 0.0):
            use_log1p = True
        else:
            use_log = True

    raw_x_min = None
    raw_x_max = None

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

        raw_x_min = float(xs.min()) if raw_x_min is None else min(raw_x_min, float(xs.min()))
        raw_x_max = float(xs.max()) if raw_x_max is None else max(raw_x_max, float(xs.max()))

        xs_plot = xmap(xs)
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
        out[mask_high] = 1.0 + (z[mask_high] - 1.0) * (0.1 / MARGIN)

        return out

    ax.set_yscale("function", functions=(y_forward, y_inverse))
    ax.set_ylim(-MARGIN, 1.0 + MARGIN)

    ax.yaxis.set_major_locator(FixedLocator([0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 0.98, 0.99, 1.0]))
    ax.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))
    ax.grid(True, which="both", alpha=0.25)
    ax.set_ylabel("Average relative score (best/cost)")

    _apply_time_axis_with_right_margin(
        ax=ax,
        raw_x_min=raw_x_min,
        raw_x_max=raw_x_max,
        use_log=use_log,
        use_log1p=use_log1p,
    )

    ax.set_title("Average scores over time (per solver)")
    _legend_bottom()
    _savefig(out_png)


def save_avg_scores_csv(ts_df: pd.DataFrame, out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    ts_df.to_csv(out_csv, index=False)


# ============ 6) Plot distribution (mean + min–max) ============

def plot_score_distribution_over_time(
    dist_df: pd.DataFrame,
    out_png: Path,
    by: str = "solver_alias",
    log_time: bool = False,
) -> None:
    if dist_df.empty:
        return

    plt.figure(figsize=(16, 10), dpi=150)
    ax = plt.gca()

    xs_all = dist_df.loc[~dist_df["mean"].isna(), "t"].astype(float).to_numpy()
    use_log = False
    use_log1p = False
    if log_time:
        if np.any(xs_all <= 0.0):
            use_log1p = True
        else:
            use_log = True

    raw_x_min = None
    raw_x_max = None

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

        raw_x_min = float(xs.min()) if raw_x_min is None else min(raw_x_min, float(xs.min()))
        raw_x_max = float(xs.max()) if raw_x_max is None else max(raw_x_max, float(xs.max()))

        xs_plot = xmap(xs)
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
        out[mask_high] = 1.0 + (z[mask_high] - 1.0) * (0.1 / MARGIN)

        return out

    ax.set_yscale("function", functions=(y_forward, y_inverse))
    ax.set_ylim(-MARGIN, 1.0 + MARGIN)
    ax.yaxis.set_major_locator(FixedLocator([0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 0.98, 0.99, 1.0]))
    ax.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))
    ax.grid(True, which="both", alpha=0.25)
    ax.set_ylabel("Relative score (mean with min–max band)")

    _apply_time_axis_with_right_margin(
        ax=ax,
        raw_x_min=raw_x_min,
        raw_x_max=raw_x_max,
        use_log=use_log,
        use_log1p=use_log1p,
    )

    ax.set_title("Score range over time (per solver) — mean ± [min,max]")
    _legend_bottom()
    _savefig(out_png)


def save_score_distribution_csv(dist_df: pd.DataFrame, out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    dist_df.to_csv(out_csv, index=False)


# ============ 7) Driver ============

def generate_final_score_summary(
    df_traj: pd.DataFrame,
    out_dir: Path,
    by: str = "solver_alias",
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
    log_time: bool = False,
    min_n_instances: Optional[int] = None,
) -> Dict[str, str]:
    """
    Pipeline : segments → stats → CSV + PNG + métriques AUC/SDT.

    Important :
      - un horizon global commun [t_min, t_max] est résolu ici ;
      - si t_max n'est pas fourni, on prend le max global de df_traj.
    """
    common_t_min, common_t_max = _resolve_common_time_window(
        df_traj,
        t_min=t_min,
        t_max=t_max,
    )

    seg = collect_instance_segments(
        df_traj,
        by=by,
        t_min=common_t_min,
        t_max=common_t_max,
    )
    print(f"...instance segments collected on common horizon [{common_t_min}, {common_t_max}]...")

    out_dir.mkdir(parents=True, exist_ok=True)

    dist = compute_score_distribution_over_time(
        seg,
        by=by,
        min_n_instances=min_n_instances,
    )
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

    auc_df = compute_auc_scores(ts, by=by)
    auc_csv = out_dir / "auc_scores_over_time.csv"
    auc_png = out_dir / "auc_scores_over_time.png"
    save_auc_scores_csv(auc_df, auc_csv)
    plot_auc_scores_table(auc_df, auc_png, by=by)

    dist_csv = out_dir / "score_distribution_over_time.csv"
    dist_png = out_dir / "score_distribution_over_time.png"
    save_score_distribution_csv(dist, dist_csv)
    plot_score_distribution_over_time(dist, dist_png, by=by, log_time=log_time)

    return {
        "avg_scores_csv": str(out_csv),
        "avg_scores_png": str(out_png),
        "auc_scores_csv": str(auc_csv),
        "auc_scores_png": str(auc_png),
        "score_dist_csv": str(dist_csv),
        "score_dist_png": str(dist_png),
    }
