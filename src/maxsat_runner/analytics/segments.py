from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pandas as pd
import math

# ---------- Helpers temps / segments ----------

def _match_instance_name(df: pd.DataFrame, instance_basename: str) -> pd.DataFrame:
    """
    Sélectionne les lignes correspondant à une instance donnée,
    en tolérant les variantes comme *.wcnf.gz, *.xml.wcnf, etc.
    """
    # normaliser en minuscule
    name = instance_basename.lower()
    # Si l'utilisateur a donné "BrazilInstance1", on matche tout ce qui contient ce nom
    # et se termine par une extension MaxSAT connue
    possible_suffixes = [".wcnf", ".wcnf.gz", ".cnf", ".cnf.gz"]
    mask = df["basename"].str.lower().apply(
        lambda x: any(x.endswith(suf) and name in x for suf in possible_suffixes)
    )
    return df[mask]

def _instance_window(df_traj: pd.DataFrame, instance_basename: str) -> Tuple[float, float]:
    df = df_traj.copy()
    df["basename"] = df["instance"].apply(lambda p: Path(str(p)).name)
    sub = _match_instance_name(df, instance_basename)
    if sub.empty:
        return (0.0, 0.0)
    tmin = float(sub["elapsed_sec"].min())
    tmax = float(sub["elapsed_sec"].max())
    return (tmin, tmax)

def _timeline_union(sub: pd.DataFrame, by: str, t_min: Optional[float], t_max: Optional[float]) -> List[float]:
    times = sorted(set(float(x) for x in sub["elapsed_sec"].tolist()))
    if not times:
        return []
    lo = times[0] if t_min is None else max(t_min, times[0])
    hi = times[-1] if t_max is None else min(t_max, times[-1])
    T = [t for t in times if lo <= t <= hi]
    if not T:
        return []
    if T[0] > lo:
        T.insert(0, lo)
    if T[-1] < hi:
        T.append(hi)
    eps = 1e-12
    U = [T[0]]
    for v in T[1:]:
        if v - U[-1] > eps:
            U.append(v)
    return U

def _cost_at_time(series_times: List[float], series_costs: List[int], t: float, idx_hint: int) -> Tuple[Optional[int], int]:
    i = idx_hint
    n = len(series_times)
    while i + 1 < n and series_times[i + 1] <= t:
        i += 1
    if n == 0 or series_times[0] > t:
        return (None, i)
    return (series_costs[i], i)

def _scores_segment_costs(costs: Dict[str, Optional[int]]) -> Dict[str, float]:
    """best/cost ∈[0,1]; 'None' => 0.0 ; best==0 => 1 si cost==0 sinon 0."""
    finite = [c for c in costs.values() if c is not None]
    if not finite:
        return {k: 0.0 for k in costs}
    best = min(finite)
    out: Dict[str, float] = {}
    for k, c in costs.items():
        if c is None:
            out[k] = 0.0
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
    Retourne un DF long de segments pour une instance :
      ['instance','t_start','t_end','duration','solver','score','cost','best_cost']
    - score=0 avant 1er coût du solver
    - pas d’ancrage artificiel
    - ajoute un snapshot final à t_last (durée 0)
    """
    df = df_traj.copy()
    df["basename"] = df["instance"].apply(lambda p: Path(str(p)).name)
    cand = instance_basename
    
    sub = df[df["basename"] == cand]
    if sub.empty:
        return pd.DataFrame(columns=["instance","t_start","t_end","duration","solver","score","cost","best_cost"])

    t_lo_nat, t_hi_nat = _instance_window(df_traj, instance_basename)
    lo = t_lo_nat if t_min is None else max(t_min, t_lo_nat)
    hi = t_hi_nat if t_max is None else min(t_max, t_hi_nat)
    if hi <= lo:
        return pd.DataFrame(columns=["instance","t_start","t_end","duration","solver","score","cost","best_cost"])

    per_solver: Dict[str, Dict[str, List]] = {}
    for key, grp in sub.groupby(by):
        g = grp.sort_values("elapsed_sec")
        per_solver[str(key)] = {"t": g["elapsed_sec"].astype(float).tolist(),
                                "c": g["cost"].astype(int).tolist()}

    T = _timeline_union(sub, by, lo, hi)
    rows: List[Dict] = []
    hints = {k: 0 for k in per_solver}

    # segments [t_i, t_{i+1})
    for i in range(max(0, len(T) - 1)):
        t0, t1 = T[i], T[i + 1]
        if t1 <= t0:
            continue
        costs_now: Dict[str, Optional[int]] = {}
        for k, ser in per_solver.items():
            c, h = _cost_at_time(ser["t"], ser["c"], t0, hints[k])
            costs_now[k] = c
            hints[k] = h
        scores = _scores_segment_costs(costs_now)
        best_now = min([v for v in costs_now.values() if v is not None], default=None)
        for k in per_solver.keys():
            rows.append({
                "instance": cand,
                "t_start": t0,
                "t_end": t1,
                "duration": t1 - t0,
                "solver": k,
                "score": scores[k],
                "cost": costs_now[k],
                "best_cost": best_now,
            })

    # snapshot final à t_last
    if T:
        t_final = T[-1]
        costs_final: Dict[str, Optional[int]] = {}
        for k, ser in per_solver.items():
            c, _ = _cost_at_time(ser["t"], ser["c"], t_final, hints.get(k, 0))
            costs_final[k] = c
        scores_final = _scores_segment_costs(costs_final)
        best_final = min([v for v in costs_final.values() if v is not None], default=None)
        for k in per_solver.keys():
            rows.append({
                "instance": cand,
                "t_start": t_final,
                "t_end": t_final,
                "duration": 0.0,
                "solver": k,
                "score": scores_final[k],
                "cost": costs_final[k],
                "best_cost": best_final,
            })

    return pd.DataFrame(rows)
