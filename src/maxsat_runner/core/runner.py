import asyncio, time
from pathlib import Path
from typing import List, Optional
from .types import Event, RunResult
from .parser import parse_o, is_optimum

async def run_one(
    cmd_template: str,
    inst_path: Path,
    tag: str,
    timeout_sec: Optional[int] = None
) -> RunResult:
    """
    Lance un solveur sur une instance, capture stdout en temps réel et extrait les événements.
    Si timeout_sec est défini, tue le processus au dépassement et renvoie exit_code=124.
    """
    t0 = time.perf_counter()
    cmd_line = cmd_template.replace("{inst}", f"\"{inst_path.absolute()}\"")
    proc = await asyncio.create_subprocess_shell(
        cmd_line,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    traj: List[Event] = []
    optimum = False

    async def _pump():
        nonlocal traj, optimum
        assert proc.stdout is not None
        async for raw in proc.stdout:
            now = time.perf_counter() - t0
            s = raw.decode(errors="replace").strip()
            c = parse_o(s)
            if c is not None:
                traj.append(Event(now, c))
            if is_optimum(s):
                optimum = True

    pump_task = asyncio.create_task(_pump())

    timed_out = False
    if timeout_sec is None or timeout_sec <= 0:
        exit_base = await proc.wait()
    else:
        try:
            exit_base = await asyncio.wait_for(proc.wait(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            exit_base = 124  # convention "timeout"

    # S'assurer que la pompe stdout est terminée
    try:
        await pump_task
    except Exception:
        # En cas d'arrêt brutal du flux après kill
        pass

    final_cost = traj[-1].cost if traj else None
    t_best = None
    if traj:
        best = None
        for e in traj:
            if best is None or e.cost < best:
                best = e.cost
                t_best = e.t_sec

    return RunResult(
        solver_tag=tag,
        solver_cmd=cmd_template,
        instance=str(inst_path.absolute()),
        events=traj,
        final_cost=final_cost,
        time_to_best_sec=t_best,
        optimum_found=optimum,
        exit_code=124 if timed_out else exit_base,
    )

def list_instances(instances_dir: Path, pattern: str) -> List[Path]:
    files = sorted(p for p in instances_dir.iterdir()
                   if p.is_file() and (pattern == "" or p.suffix == pattern))
    return files
