from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pandas as pd


# ---------- Helpers temps / segments ----------

def _match_instance_name(df: pd.DataFrame, instance_basename: str) -> pd.DataFrame:
    """
    Sélectionne les lignes correspondant à une instance donnée,
    en tolérant les variantes comme *.wcnf.gz, *.xml.wcnf, etc.
    """
    name = str(instance_basename).lower().strip()

    # 1) essai exact sur basename
    exact = df["basename"].astype(str).str.lower().eq(name)
    if exact.any():
        return df[exact]

    # 2) essai tolérant sur suffixes MaxSAT connus
    possible_suffixes = (".wcnf", ".wcnf.gz", ".cnf", ".cnf.gz")
    mask = df["basename"].astype(str).str.lower().apply(
        lambda x: any(x.endswith(suf) and name in x for suf in possible_suffixes)
    )
    return df[mask]


def _instance_window(df_traj: pd.DataFrame, instance_basename: str) -> Tuple[float, float]:
    df = df_traj.copy()
    df["basename"] = df["instance"].apply(lambda p: Path(str(p)).name)
    sub = _match_instance_name(df, instance_basename)
    if sub.empty:
        return (0.0, 0.0)

    tmin = float(pd.to_numeric(sub["elapsed_sec"], errors="coerce").min())
    tmax = float(pd.to_numeric(sub["elapsed_sec"], errors="coerce").max())
    return (tmin, tmax)


def _timeline_union(
    sub: pd.DataFrame,
    by: str,
    t_min: Optional[float],
    t_max: Optional[float],
) -> List[float]:
    """
    Construit la grille temporelle locale pour l'instance.

    Important :
      - si t_max est fourni et dépasse le dernier événement observé,
        on prolonge explicitement la timeline jusqu'à t_max ;
      - on garde toujours les bornes lo et hi dans la timeline.
    """
    times = pd.to_numeric(sub["elapsed_sec"], errors="coerce")
    times = sorted(set(float(x) for x in times.dropna().tolist()))

    if not times and (t_min is None or t_max is None):
        return []

    lo = float(times[0]) if t_min is None else float(t_min)
    hi = float(times[-1]) if t_max is None else float(t_max)

    if hi <= lo:
        return []

    inside = [t for t in times if lo <= t <= hi]
    T = [lo] + inside + [hi]

    T = sorted(T)
    eps = 1e-12
    U: List[float] = []
    for t in T:
        if not U or (t - U[-1]) > eps:
            U.append(t)

    return U


def _cost_at_time(
    series_times: List[float],
    series_costs: List[int],
    t: float,
    idx_hint: int,
) -> Tuple[Optional[int], int]:
    """
    Retourne le coût best-so-far à l'instant t pour un solveur donné.
    Si aucun coût n'est encore observé à t, retourne None.
    """
    i = idx_hint
    n = len(series_times)

    while i + 1 < n and series_times[i + 1] <= t:
        i += 1

    if n == 0 or series_times[0] > t:
        return (None, i)

    return (series_costs[i], i)


def _scores_segment_costs(costs: Dict[str, Optional[int]]) -> Dict[str, float]:
    """
    Score relatif de type best/cost, borné dans [0,1].

    Règles :
      - cost = None  -> score = NaN (pas encore de valeur)
      - best = 0     -> score = 1 si cost = 0, sinon 0
      - sinon        -> score = best / cost
    """
    finite = [c for c in costs.values() if c is not None]
    if not finite:
        return {k: float("nan") for k in costs}

    best = min(finite)
    out: Dict[str, float] = {}

    for k, c in costs.items():
        if c is None:
            out[k] = float("nan")
        else:
            if best == 0:
                out[k] = 1.0 if c == 0 else 0.0
            else:
                out[k] = max(0.0, min(1.0, best / c))

    return out


# ---------- API : segments de score(t) par instance ----------

def compute_relative_scores_timewindow_for_instance(
    df_traj: pd.DataFrame,
    instance_basename: str,
    by: str = "solver_alias",
    t_min: Optional[float] = None,
    t_max: Optional[float] = None,
) -> pd.DataFrame:
    """
    Retourne un DataFrame long de segments pour une instance :
      ['instance', 't_start', 't_end', 'duration', 'solver', 'score', 'cost', 'best_cost']

    Sémantique :
      - score = NaN avant le premier coût observé d'un solveur ;
      - entre deux événements, le coût est conservé (best-so-far) ;
      - si t_max est fourni, la trajectoire est prolongée jusqu'à t_max ;
      - aucun snapshot final de durée nulle n'est ajouté.
    """
    df = df_traj.copy()
    df["basename"] = df["instance"].apply(lambda p: Path(str(p)).name)

    sub = _match_instance_name(df, instance_basename).copy()
    if sub.empty:
        return pd.DataFrame(
            columns=["instance", "t_start", "t_end", "duration", "solver", "score", "cost", "best_cost"]
        )

    label_col = by if by in sub.columns else ("solver" if "solver" in sub.columns else None)
    if label_col is None:
        raise KeyError(f"Aucune colonne '{by}' ni 'solver' trouvée dans df_traj.")

    sub["elapsed_sec"] = pd.to_numeric(sub["elapsed_sec"], errors="coerce")
    sub["cost"] = pd.to_numeric(sub["cost"], errors="coerce")
    sub = sub[sub["elapsed_sec"].notna() & sub["cost"].notna()].copy()

    if sub.empty:
        return pd.DataFrame(
            columns=["instance", "t_start", "t_end", "duration", "solver", "score", "cost", "best_cost"]
        )

    sub["cost"] = sub["cost"].astype(int)

    t_lo_nat, t_hi_nat = _instance_window(df_traj, instance_basename)

    # Si t_min/t_max sont fournis, on respecte la fenêtre demandée.
    # Cela permet notamment de prolonger jusqu'à un horizon global commun.
    lo = t_lo_nat if t_min is None else float(t_min)
    hi = t_hi_nat if t_max is None else float(t_max)

    if hi <= lo:
        return pd.DataFrame(
            columns=["instance", "t_start", "t_end", "duration", "solver", "score", "cost", "best_cost"]
        )

    per_solver: Dict[str, Dict[str, List]] = {}
    for key, grp in sub.groupby(label_col, sort=True):
        g = grp.sort_values("elapsed_sec", kind="stable")
        per_solver[str(key)] = {
            "t": g["elapsed_sec"].astype(float).tolist(),
            "c": g["cost"].astype(int).tolist(),
        }

    T = _timeline_union(sub, label_col, lo, hi)
    if len(T) < 2:
        return pd.DataFrame(
            columns=["instance", "t_start", "t_end", "duration", "solver", "score", "cost", "best_cost"]
        )

    rows: List[Dict] = []
    hints = {k: 0 for k in per_solver}

    # Segments [t_i, t_{i+1})
    for i in range(len(T) - 1):
        t0, t1 = float(T[i]), float(T[i + 1])
        if t1 <= t0:
            continue

        costs_now: Dict[str, Optional[int]] = {}
        for k, ser in per_solver.items():
            c, h = _cost_at_time(ser["t"], ser["c"], t0, hints[k])
            costs_now[k] = c
            hints[k] = h

        scores = _scores_segment_costs(costs_now)
        finite_costs = [v for v in costs_now.values() if v is not None]
        best_now = min(finite_costs) if finite_costs else None

        for k in per_solver.keys():
            rows.append({
                "instance": str(instance_basename),
                "t_start": t0,
                "t_end": t1,
                "duration": t1 - t0,
                "solver": k,
                "score": scores[k],
                "cost": costs_now[k],
                "best_cost": best_now,
            })

    return pd.DataFrame(rows)
