import asyncio, shlex
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from .runner import run_one, list_instances
from .types import RunResult
from ..io.csvsink import append_csv, write_instance_csv

def _derive_alias_from_cmd(cmd: str) -> str:
    # retire [cwd=...] si présent
    s = cmd.strip()
    if s.startswith("[cwd="):
        r = s.find("]")
        if r != -1:
            s = s[r+1:].lstrip()
    # premier token -> basename
    try:
        first = shlex.split(s)[0]
    except Exception:
        first = s.split()[0] if s.split() else "solver"
    return Path(first).name

def _normalize_solvers(
    solver_pairs: Optional[List[Dict]] = None,
    solver_cmds: Optional[List[str]] = None
) -> List[Tuple[str, str]]:
    """
    Retourne une liste de (alias, cmd).
    - Si solver_pairs est fourni: prend {alias?, cmd} et dérive alias si vide.
    - Sinon: parse solver_cmds, accepte forme 'alias=CMD' ou juste 'CMD'.
    """
    out: List[Tuple[str,str]] = []
    if solver_pairs:
        for item in solver_pairs:
            cmd = str(item["cmd"]).strip()
            alias = str(item.get("alias") or "").strip() or _derive_alias_from_cmd(cmd)
            out.append((alias, cmd))
        return out
    if solver_cmds:
        for s in solver_cmds:
            s = s.strip()
            alias = None
            # syntaxe "alias=CMD"
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
    solver_pairs: Optional[List[Dict]],
    solver_cmds: Optional[List[str]],
    instances_dir: Path,
    pattern: str,
    out_dir: Path,
    tag: str,
    timeout_sec: Optional[int] = None,
) -> Dict:
    insts = list_instances(instances_dir, pattern)
    if not insts:
        raise RuntimeError("Aucune instance trouvée.")

    defs = _normalize_solvers(solver_pairs=solver_pairs, solver_cmds=solver_cmds)
    if not defs:
        raise RuntimeError("Aucun solveur fourni.")

    all_results: List[RunResult] = []
    for alias, cmd in defs:
        for inst in insts:
            r = await run_one(cmd, inst, tag, timeout_sec=timeout_sec, solver_alias=alias)
            all_results.append(r)
            write_instance_csv(out_dir, tag, r)

    traj_csv, sum_csv = append_csv(out_dir, all_results)

    payload = {
        "trajectories_csv": str(traj_csv),
        "summary_csv": str(sum_csv),
        "results": [
            {
                **{k: v for k, v in asdict(r).items() if k != "events"},
                "events": [{"t_sec": e.t_sec, "cost": e.cost} for e in r.events],
            }
            for r in all_results
        ],
    }
    return payload