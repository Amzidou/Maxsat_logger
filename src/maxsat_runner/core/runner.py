from __future__ import annotations
import asyncio
import os
import signal
import time
import contextlib
from pathlib import Path
from typing import List, Optional, Tuple

from .types import Event, RunResult
from .parser import parse_o, is_optimum


def _extract_cwd(cmd_template: str) -> Tuple[Optional[str], str]:
    """
    Extrait un éventuel préfixe [cwd=...] en tête de commande.
    Retourne (cwd, cmd_sans_prefixe).
    """
    s = cmd_template.lstrip()
    if s.startswith("[cwd="):
        end = s.find("]")
        if end != -1:
            inside = s[5:end].strip()
            rest = s[end + 1 :].lstrip()
            return (inside or None, rest)
    return (None, cmd_template)


async def run_one(
    cmd_template: str,
    inst_path: Path,
    tag: str,
    timeout_sec: Optional[int] = None,
    solver_alias: str = "solver",
) -> RunResult:
    """
    Lance un solver 'anytime' et lit stdout en temps réel.
    Timeout robuste:
      - nouvelle session de processus (kill du *groupe*)
      - SIGTERM à l’échéance, délai de grâce, puis SIGKILL groupe
    Dédup: on garde uniquement les améliorations strictes (coût décroissant).
    """
    t0 = time.perf_counter()
    cwd, cmd_wo = _extract_cwd(cmd_template)
    cmd_line = cmd_wo.replace("{inst}", f"\"{inst_path.absolute()}\"")

    # Nouvelle session => proc.pid leader de groupe, permet killpg()
    proc = await asyncio.create_subprocess_shell(
        cmd_line,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd if cwd else None,
        start_new_session=True,
    )

    traj: List[Event] = []
    optimum = False
    best_seen: Optional[int] = None

    async def _pump_stdout() -> None:
        """Lit stdout ligne par ligne, parse 'o <cost>' et 's OPTIMUM FOUND'."""
        nonlocal best_seen, optimum
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            now = time.perf_counter() - t0
            s = line.decode(errors="replace").strip()
            c = parse_o(s)
            if c is not None:
                if best_seen is None or c < best_seen:
                    best_seen = c
                    traj.append(Event(now, c))  # amélioration stricte
            if is_optimum(s):
                optimum = True

    pump_task = asyncio.create_task(_pump_stdout())
    wait_task = asyncio.create_task(proc.wait())
    timed_out = False

    # Attente principale avec mur de temps (n'annule pas wait_task)
    if timeout_sec is not None and timeout_sec > 0:
        done, _ = await asyncio.wait({wait_task}, timeout=float(timeout_sec))
        if not done:
            timed_out = True
            # Étape 1: SIGTERM
            with contextlib.suppress(ProcessLookupError):
                proc.send_signal(signal.SIGTERM)

            # Petite période de grâce (ne pas utiliser wait_for pour ne pas annuler)
            grace = 2.0
            done2, _ = await asyncio.wait({wait_task}, timeout=grace)

            if not done2:
                # Étape 2: SIGKILL sur le groupe (Linux/Unix), fallback Windows
                with contextlib.suppress(ProcessLookupError):
                    if hasattr(os, "killpg"):
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                # Attendre la fin sans timeout artificiel
                await wait_task
    else:
        # Pas de timeout: attendre la fin
        await wait_task

    # S'assurer que le lecteur se termine (évite "event loop is closed")
    try:
        await asyncio.wait_for(pump_task, timeout=2.0)
    except asyncio.TimeoutError:
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump_task

    exit_code = proc.returncode
    if timed_out:
        exit_code = 124  # convention GNU timeout

    final_cost = traj[-1].cost if traj else None
    t_best = traj[-1].t_sec if traj else None

    return RunResult(
        solver_tag=tag,
        solver_cmd=cmd_template,
        solver_alias=solver_alias,
        instance=str(inst_path.absolute()),
        events=traj,
        final_cost=final_cost,
        time_to_best_sec=t_best,
        optimum_found=optimum,
        exit_code=exit_code,
    )


def list_instances(instances_dir: Path, pattern: str) -> List[Path]:
    """
    Liste les fichiers du répertoire dont le suffixe correspond à 'pattern'.
    Si pattern == "", on prend tous les fichiers.
    """
    return sorted(
        p for p in instances_dir.iterdir()
        if p.is_file() and (pattern == "" or p.suffix == pattern)
    )
