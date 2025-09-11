from __future__ import annotations
import asyncio, shlex
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .runner import run_one, list_instances
from .types import RunResult
from ..io.csvsink import append_csv, write_instance_csv


def _derive_alias_from_cmd(cmd: str) -> str:
    """Dérive un alias court depuis la commande (en retirant un éventuel [cwd=...])."""
    s = cmd.strip()
    if s.startswith("[cwd="):
        r = s.find("]")
        if r != -1:
            s = s[r + 1 :].lstrip()
    try:
        first = shlex.split(s)[0]
    except Exception:
        toks = s.split()
        first = toks[0] if toks else "solver"
    return Path(first).name


def _normalize_solvers(
    *,
    solver_pairs: Optional[List[Dict[str, str]]] = None,
    solver_cmds: Optional[List[str]] = None,
) -> List[Tuple[str, str]]:
    """
    Retourne une liste normalisée de (alias, cmd).

    - Nouveau format (UI/API) : solver_pairs = [{alias: "...", cmd: "..."}, ...]
      -> 'alias' peut être vide/omise : on le dérive de 'cmd'.
    - Ancien format (tests/CLI rétro-compat) : solver_cmds = ["alias=CMD {inst}", "CMD {inst}", ...]
      -> si "alias=..." est absent, on dérive l'alias depuis 'CMD'.
    """
    out: List[Tuple[str, str]] = []

    if solver_pairs:
        for item in solver_pairs:
            cmd = str(item["cmd"]).strip()
            alias = str(item.get("alias") or "").strip() or _derive_alias_from_cmd(cmd)
            out.append((alias, cmd))
        return out

    if solver_cmds:
        for s in solver_cmds:
            s = s.strip()
            alias: Optional[str] = None
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
    *,
    solver_pairs: Optional[List[Dict[str, str]]] = None,  # NEW: optionnel + keyword-only
    solver_cmds: Optional[List[str]] = None,              # NEW: optionnel + keyword-only
    instances_dir: Path,
    pattern: str,
    out_dir: Path,
    tag: str,
    timeout_sec: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Exécution séquentielle (solver × instance) avec logging streaming.
    Compatible :
      - UI/API : solver_pairs = [{alias, cmd}, ...]
      - Tests/CLI anciens : solver_cmds = ["alias=CMD {inst}", "CMD {inst}", ...]
    Écrit:
      - {out_dir}/trajectories.csv
      - {out_dir}/summary.csv
    Retourne un payload JSON-serializable (chemins + résultats).
    """
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
        for inst in insts:
            r = await run_one(
                cmd_template=cmd,
                inst_path=inst,
                tag=tag,
                timeout_sec=timeout_sec,
                solver_alias=alias,
            )
            all_results.append(r)
            # CSV par instance/tag
            write_instance_csv(out_dir, tag, r)

    # CSV globaux
    traj_csv, sum_csv = append_csv(out_dir, all_results)

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
    }
    return payload