import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional
from .runner import run_one, list_instances
from .types import RunResult
from ..io.csvsink import append_csv, write_instance_csv

async def run_campaign_sequential(
    solver_cmds: List[str],
    instances_dir: Path,
    pattern: str,
    out_dir: Path,
    tag: str,
    timeout_sec: Optional[int] = None,
) -> Dict:
    insts = list_instances(instances_dir, pattern)
    if not insts:
        raise RuntimeError("Aucune instance trouvée.")

    all_results: List[RunResult] = []
    for solver in solver_cmds:
        for inst in insts:
            r = await run_one(solver, inst, tag, timeout_sec=timeout_sec)
            all_results.append(r)
            # Écriture CSV par instance immédiatement
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