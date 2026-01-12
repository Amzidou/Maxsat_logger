from __future__ import annotations
import shlex
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .runner import run_one_streaming as run_one, list_instances
from .types import RunResult
from ..io.logsink import open_run_log, append_logs_summary

def _derive_alias_from_cmd(cmd: str) -> str:
    s = cmd.strip()
    if s.startswith("[cwd="):
        r = s.find("]")
        if r != -1:
            s = s[r+1:].lstrip()
    try:
        first = shlex.split(s)[0]
    except Exception:
        toks = s.split()
        first = toks[0] if toks else "solver"
    return Path(first).name

def _normalize_solvers(
    *, solver_pairs: Optional[List[Dict[str, str]]] = None,
       solver_cmds: Optional[List[str]] = None,
) -> List[Tuple[str, str]]:
    """
    Retourne [(alias, cmd)] à partir de:
      - solver_pairs: [{alias?, cmd}]
      - solver_cmds:  ["alias=CMD {inst}" | "CMD {inst}"]
    """
    out: List[Tuple[str, str]] = []
    if solver_pairs:
        for item in solver_pairs:
            cmd = str(item["cmd"]).strip()
            alias = (item.get("alias") or "").strip() or _derive_alias_from_cmd(cmd)
            out.append((alias, cmd))
        return out

    if solver_cmds:
        for s in solver_cmds:
            s = s.strip()
            alias: Optional[str] = None
            if "=" in s and not s.startswith("[cwd="):
                a, c = s.split("=", 1)
                if "{inst}" in c:
                    alias = a.strip()
                    s = c.strip()
            if not alias:
                alias = _derive_alias_from_cmd(s)
            out.append((alias, s))
    return out


async def run_campaign_sequential(
    *,
    solver_pairs: Optional[List[Dict[str, str]]] = None,
    solver_cmds: Optional[List[str]] = None,
    instances_dir: Path,
    pattern: str,
    out_dir: Path,
    timeout_sec: Optional[int] = None,
) -> Dict[str, Any]:
    instances_dir = Path(instances_dir)
    out_dir = Path(out_dir)

    insts = list_instances(instances_dir, pattern)
    if not insts:
        raise RuntimeError(f"Aucune instance trouvée dans {instances_dir} avec pattern '{pattern}'.")

    defs = _normalize_solvers(solver_pairs=solver_pairs, solver_cmds=solver_cmds)
    if not defs:
        raise RuntimeError("Aucun solveur fourni (solver_pairs ou solver_cmds).")

    all_results: List[RunResult] = []

    for alias, cmd in defs:
        print(f"=== Campaign: running solver '{alias}' ===")
        for inst in insts:
            # ouvrir logs du run
            events_path, events_fp, meta_path, run_id = open_run_log(out_dir, alias, inst)
            try:
                print(f"--- Instance: {inst.name} ---")
                r = await run_one(
                    cmd_template=cmd,
                    inst_path=inst,
                    solver_alias=alias,
                    solver_tag=alias,   # ou autre logique
                    events_fp=events_fp,
                    meta_path=meta_path,
                    run_id=run_id,
                    timeout_sec=timeout_sec,
                )
                print(f"--- Instance done: {inst.name} (status={r. exit_code}, cost={r.final_cost}, time to best ={r.time_to_best_sec}s) ---")
                all_results.append(r)
            finally:
                events_fp.close()

    # Agrégation globale
    traj_csv, sum_csv = append_logs_summary(out_dir)

    payload: Dict[str, Any] = {
        "trajectories_csv": str(traj_csv),
        "summary_csv": str(sum_csv),
        "results": [
            {
                **{k: v for k, v in asdict(r).items() if k != "events"},
                "events": [{"t_sec": e.t_sec, "cost": e.cost} for e in r.events],
            }
            for r in all_results
        ],
        "results_count": len(all_results),
    }
    return payload