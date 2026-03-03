from __future__ import annotations
import asyncio
import os
import signal
import time
import contextlib
import tempfile
import pandas as pd
import shlex
import csv
import logging


from pathlib import Path
from typing import List, Optional, Tuple

from .types import Event, RunResult
from .parser import parse_o, is_optimum
from ..io.logsink import open_run_log

HEADER_META = ["solver_tag","solver_alias","solver_cmd","instance","run_id","optimum_found","exit_code"]
logger = logging.getLogger(__name__)



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


# async def run_one_streaming(
#     *,
#     cmd_template: str,
#     inst_path: Path,
#     solver_alias: str,
#     solver_tag: str,
#     events_fp,          # file-like texte ouvert (csv), entête déjà écrite
#     meta_path: Path,    # chemin du _meta.csv
#     run_id: int,
#     timeout_sec: Optional[int] = None,
# ) -> RunResult:
#     """
#     Version “source de vérité” : écrit les événements AU FIL DE L’EAU dans <run>.csv
#     et écrit <run>_meta.csv à la fin. Timeout => SIGKILL + exit_code=124.
#     """
#     t0 = time.perf_counter()
#     cwd, cmd_wo = _extract_cwd(cmd_template)
#     cmd_line = cmd_wo.replace("{inst}", f"{inst_path.absolute()}")

#     # Nouvelle session => proc.pid leader de groupe, permet killpg()
#     print(f"--- cmd line lance: {cmd_line} ---")
#     proc = await asyncio.create_subprocess_shell(
#         cmd_line,
#         stdout=asyncio.subprocess.PIPE,
#         stderr=asyncio.subprocess.STDOUT,
#         cwd=cwd if cwd else None,
#         start_new_session=True,
#     )

#     writer = csv.writer(events_fp)
#     traj: List[Event] = []
#     optimum = False
#     best_seen: Optional[int] = None
#     exit_code: Optional[int] = None

#     async def _pump_stdout() -> None:
#         nonlocal best_seen, optimum
#         assert proc.stdout is not None
#         event_idx = 0

#         READ_CHUNK = 4096
#         MAX_BUF = 10 * 1024 * 1024  # 10MB anti-stdout sans '\n'
#         buf = bytearray()

#         def _handle_line(line_bytes: bytes) -> None:
#             nonlocal best_seen, optimum, event_idx

#             now = time.perf_counter() - t0
#             s = line_bytes.decode(errors="replace").strip()

#             print(f"--- c line: {s} ---")

#             try:
#                 c = parse_o(s)
#             except Exception:
#                 logger.exception("parse_o crashed (run_id=%s) line=%r", run_id, s)
#                 c = None

#             if c is not None and (best_seen is None or c < best_seen):
#                 best_seen = c
#                 traj.append(Event(now, c))

#                 logger.info(
#                     "New best (run_id=%s, event_idx=%d): c=%s at t=%.6f",
#                     run_id, event_idx, c, now
#                 )

#                 try:
#                     writer.writerow([
#                         solver_tag, solver_alias, cmd_template,
#                         str(inst_path.absolute()), run_id, event_idx, now, int(c)
#                     ])
#                     events_fp.flush()
#                     event_idx += 1
#                 except Exception:
#                     logger.exception("Failed to write/flush event row (run_id=%s)", run_id)

#             try:
#                 if is_optimum(s):
#                     if not optimum:
#                         logger.info("Optimum detected (run_id=%s)", run_id)
#                     optimum = True
#             except Exception:
#                 logger.exception("is_optimum crashed (run_id=%s) line=%r", run_id, s)

#         logger.debug(
#             "pump_stdout start (solver_tag=%s, alias=%s, run_id=%s, inst=%s)",
#             solver_tag, solver_alias, run_id, inst_path
#         )

#         try:
#             while True:
#                 try:
#                     chunk = await proc.stdout.read(READ_CHUNK)
#                 except Exception:
#                     logger.exception("stdout read failed (run_id=%s)", run_id)
#                     break

#                 if not chunk:
#                     if buf:
#                         _handle_line(bytes(buf))
#                         buf.clear()
#                     logger.debug("pump_stdout EOF (run_id=%s)", run_id)
#                     break

#                 buf.extend(chunk)

#                 if len(buf) > MAX_BUF:
#                     logger.warning(
#                         "stdout buffer > %d bytes without newline (run_id=%s). Truncating buffer.",
#                         MAX_BUF, run_id
#                     )
#                     buf[:] = buf[-(MAX_BUF // 2):]

#                 while True:
#                     nl = buf.find(b"\n")
#                     if nl == -1:
#                         break
#                     line_bytes = bytes(buf[:nl])
#                     del buf[:nl + 1]
#                     _handle_line(line_bytes)

#         except asyncio.CancelledError:
#             logger.debug("pump_stdout cancelled (run_id=%s)", run_id)
#             raise
#         except Exception:
#             logger.exception("Unhandled error in pump_stdout (run_id=%s)", run_id)
#         finally:
#             logger.debug("pump_stdout end (run_id=%s, events=%d)", run_id, event_idx)

    
#     pump_task = asyncio.create_task(_pump_stdout())

#     # Timeout => SIGKILL direct (spec)
#     try:
#         if timeout_sec and timeout_sec > 0:
#             try:
#                 await asyncio.wait_for(proc.wait(), timeout=float(timeout_sec))
#             except asyncio.TimeoutError:
#                 with contextlib.suppress(ProcessLookupError):
#                     if hasattr(os, "killpg"):
#                         os.killpg(proc.pid, signal.SIGKILL)
#                     else:
#                         proc.kill()
#                 await proc.wait()
#                 exit_code = 124
#         else:
#             await proc.wait()
#     finally:
#         try:
#             await asyncio.wait_for(pump_task, timeout=2.0)
#         except asyncio.TimeoutError:
#             pump_task.cancel()
#             with contextlib.suppress(asyncio.CancelledError):
#                 await pump_task

#     if exit_code is None:
#         exit_code = proc.returncode

#     final_cost = traj[-1].cost if traj else None
#     t_best = traj[-1].t_sec if traj else None


#     pd.DataFrame([{
#         "solver_tag": solver_tag,
#         "solver_alias": solver_alias,
#         "solver_cmd": cmd_template,
#         "instance": str(inst_path.absolute()),
#         "run_id": run_id,
#         "optimum_found": bool(optimum),
#         "exit_code": int(exit_code if exit_code is not None else -1),
#     }], columns=HEADER_META).to_csv(meta_path, index=False)

#     return RunResult(
#         solver_tag=solver_tag,
#         solver_cmd=cmd_template,
#         solver_alias=solver_alias,
#         instance=str(inst_path.absolute()),
#         events=traj,
#         final_cost=final_cost,
#         time_to_best_sec=t_best,
#         optimum_found=optimum,
#         exit_code=int(exit_code if exit_code is not None else -1),
#     )

import asyncio
import contextlib
import csv
import os
import shlex
import signal
import time
from pathlib import Path
from typing import List, Optional

import pandas as pd

# assume: logger, parse_o, is_optimum, Event, RunResult, HEADER_META, _extract_cwd exist

def _quote_path(p: Path) -> str:
    # Quoting robuste pour shell
    return shlex.quote(str(p))

async def run_one_streaming(
    *,
    cmd_template: str,
    inst_path: Path,
    solver_alias: str,
    solver_tag: str,
    events_fp,
    meta_path: Path,
    run_id: int,
    timeout_sec: Optional[int] = None,
) -> "RunResult":
    """
    Debug-friendly:
      - logs cmd/cwd/env checks
      - heartbeat while running (detect "nothing happens")
      - raw stdout log to <run>_raw.log
      - safer {inst} quoting
    """
    t0 = time.perf_counter()
    cwd, cmd_wo = _extract_cwd(cmd_template)

    inst_abs = inst_path.absolute()
    inst_q = _quote_path(inst_abs)
    cmd_line = cmd_wo.replace("{inst}", inst_q)

    # Optionnel: forcer line-buffering (Linux coreutils)
    # Si tu ne veux pas toucher la commande, commente la ligne suivante.
    cmd_line_dbg = f"stdbuf -oL -eL {cmd_line}"

    # raw log (même si parse rate)
    raw_path = meta_path.with_name(meta_path.stem.replace("_meta", "") + "_raw.log")
    raw_fp = open(raw_path, "w", encoding="utf-8", buffering=1)

    def _dbg(msg: str) -> None:
        print(msg, flush=True)
        try:
            raw_fp.write(msg + "\n")
        except Exception:
            pass

    # _dbg(f"[run_id={run_id}] cwd={cwd!r}")
    # _dbg(f"[run_id={run_id}] inst={inst_abs}")
    # _dbg(f"[run_id={run_id}] cmd_template={cmd_template!r}")
    # _dbg(f"[run_id={run_id}] cmd_line={cmd_line}")
    # _dbg(f"[run_id={run_id}] cmd_line_dbg={cmd_line_dbg}")

    # Vérifs basiques sur l’instance
    if not inst_abs.exists():
        _dbg(f"[run_id={run_id}] ERROR: instance does not exist: {inst_abs}")
    if not inst_abs.is_file():
        _dbg(f"[run_id={run_id}] ERROR: instance is not a file: {inst_abs}")

    # Si la commande commence par un chemin, tenter de vérifier l’exécutable.
    # (heuristique: premier token)
    try:
        first_tok = shlex.split(cmd_line)[0]
        if first_tok.startswith("/") or first_tok.startswith("./"):
            exe = Path(first_tok).expanduser()
            # _dbg(f"[run_id={run_id}] exe_guess={exe} exists={exe.exists()} x_ok={os.access(exe, os.X_OK)}")
    except Exception as e:
        _dbg(f"[run_id={run_id}] WARN: could not split cmd_line: {e!r}")

    # Nouvelle session => proc.pid leader de groupe, permet killpg()
    proc = await asyncio.create_subprocess_shell(
        cmd_line_dbg,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd if cwd else None,
        start_new_session=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    # _dbg(f"[run_id={run_id}] STARTED pid={proc.pid}")

    writer = csv.writer(events_fp)
    traj: List["Event"] = []
    optimum = False
    best_seen: Optional[int] = None
    exit_code: Optional[int] = None

    last_output_time = time.perf_counter()
    event_idx = 0

    async def _heartbeat() -> None:
        nonlocal last_output_time
        try:
            while proc.returncode is None:
                await asyncio.sleep(1.0)
                alive = (proc.returncode is None)
                since = time.perf_counter() - last_output_time
                # _dbg(f"[run_id={run_id}] HEARTBEAT alive={alive} pid={proc.pid} no_output_for={since:.2f}s")
        except asyncio.CancelledError:
            return

    async def _pump_stdout() -> None:
        nonlocal best_seen, optimum, event_idx, last_output_time
        assert proc.stdout is not None

        READ_CHUNK = 4096
        MAX_BUF = 10 * 1024 * 1024  # 10MB anti-stdout sans '\n'
        buf = bytearray()

        def _handle_line(line_bytes: bytes) -> None:
            nonlocal best_seen, optimum, event_idx, last_output_time
            last_output_time = time.perf_counter()

            now = last_output_time - t0
            s = line_bytes.decode(errors="replace").rstrip("\r")

            # Toujours log raw
            # _dbg(f"[run_id={run_id}] OUT: {s}")

            # Parse "o ..." (si applicable)
            try:
                c = parse_o(s.strip())
            except Exception:
                logger.exception("parse_o crashed (run_id=%s) line=%r", run_id, s)
                c = None

            if c is not None and (best_seen is None or c < best_seen):
                best_seen = c
                traj.append(Event(now, c))
                logger.info("New best (run_id=%s, event_idx=%d): c=%s at t=%.6f",
                            run_id, event_idx, c, now)

                try:
                    writer.writerow([
                        solver_tag, solver_alias, cmd_template,
                        str(inst_abs), run_id, event_idx, now, int(c)
                    ])
                    events_fp.flush()
                    event_idx += 1
                except Exception:
                    logger.exception("Failed to write/flush event row (run_id=%s)", run_id)

            # Optimum
            try:
                if is_optimum(s):
                    optimum = True
            except Exception:
                logger.exception("is_optimum crashed (run_id=%s) line=%r", run_id, s)

        try:
            while True:
                chunk = await proc.stdout.read(READ_CHUNK)
                if not chunk:
                    if buf:
                        _handle_line(bytes(buf))
                        buf.clear()
                    break

                buf.extend(chunk)
                if len(buf) > MAX_BUF:
                    logger.warning("stdout buffer > %d bytes without newline (run_id=%s). Truncating.", MAX_BUF, run_id)
                    buf[:] = buf[-(MAX_BUF // 2):]

                while True:
                    nl = buf.find(b"\n")
                    if nl == -1:
                        break
                    line_bytes = bytes(buf[:nl])
                    del buf[:nl + 1]
                    _handle_line(line_bytes)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unhandled error in pump_stdout (run_id=%s)", run_id)

    pump_task = asyncio.create_task(_pump_stdout())
    hb_task = asyncio.create_task(_heartbeat())

    # Timeout => SIGKILL
    try:
        if timeout_sec and timeout_sec > 0:
            try:
                await asyncio.wait_for(proc.wait(), timeout=float(timeout_sec))
            except asyncio.TimeoutError:
                # _dbg(f"[run_id={run_id}] TIMEOUT after {timeout_sec}s -> SIGKILL")
                with contextlib.suppress(ProcessLookupError):
                    if hasattr(os, "killpg"):
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                await proc.wait()
                exit_code = 124
        else:
            await proc.wait()
    finally:
        hb_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb_task

        try:
            await asyncio.wait_for(pump_task, timeout=2.0)
        except asyncio.TimeoutError:
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump_task

        raw_fp.close()

    if exit_code is None:
        exit_code = proc.returncode

    # _dbg(f"[run_id={run_id}] DONE returncode={exit_code} elapsed={time.perf_counter()-t0:.3f}s events={len(traj)}")

    final_cost = traj[-1].cost if traj else None
    t_best = traj[-1].t_sec if traj else None

    pd.DataFrame([{
        "solver_tag": solver_tag,
        "solver_alias": solver_alias,
        "solver_cmd": cmd_template,
        "instance": str(inst_abs),
        "run_id": run_id,
        "optimum_found": bool(optimum),
        "exit_code": int(exit_code if exit_code is not None else -1),
    }], columns=HEADER_META).to_csv(meta_path, index=False)

    return RunResult(
        solver_tag=solver_tag,
        solver_cmd=cmd_template,
        solver_alias=solver_alias,
        instance=str(inst_abs),
        events=traj,
        final_cost=final_cost,
        time_to_best_sec=t_best,
        optimum_found=optimum,
        exit_code=int(exit_code if exit_code is not None else -1),
    )

# --------- WRAPPER RÉTRO-COMPAT (ancienne signature) ---------
async def run_one(
    cmd_template: str,
    inst_path: Path,
    timeout_sec: Optional[int] = None,
    *,
    solver_alias: Optional[str] = None,
    solver_tag: Optional[str] = None,
    out_dir: Optional[Path] = None,
) -> RunResult:
    """
    Wrapper rétro-compat:
      - Accepte l’ancien appel positionnel: run_one(cmd_template, inst_path, timeout_sec=...)
      - Ouvre un fichier de log temporaire (ou sous out_dir si fourni)
      - Dérive alias/tag si absents (par défaut: alias = nom du binaire; tag = alias)
    """
    inst_path = Path(inst_path)

    if solver_alias is None:
        solver_alias = _derive_alias_from_cmd(cmd_template)
    if solver_tag is None:
        solver_tag = solver_alias

    # Choix du répertoire de logs: out_dir/logs si fourni, sinon dossier temp
    if out_dir is None:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="maxsat_runner_")
        tmp_root = Path(tmp_ctx.name)
        cleanup_ctx = tmp_ctx  # pour GC à la fin
        logs_root = tmp_root
    else:
        logs_root = Path(out_dir)
        cleanup_ctx = None  # pas de nettoyage auto

    # ouvrir le log du run
    events_path, events_fp, meta_path, run_id = open_run_log(logs_root, solver_alias, inst_path)
    try:
        res = await run_one_streaming(
            cmd_template=cmd_template,
            inst_path=inst_path,
            solver_alias=solver_alias,
            solver_tag=solver_tag,
            events_fp=events_fp,
            meta_path=meta_path,
            run_id=run_id,
            timeout_sec=timeout_sec,
        )
    finally:
        events_fp.close()
        # si tempdir, on laisse le context manager faire le ménage
        if cleanup_ctx is not None:
            cleanup_ctx.cleanup()

    return res


def list_instances(instances_dir: Path, pattern: str) -> List[Path]:
    """
    Liste les fichiers d'instances à exécuter :
      - Si 'instances_dir' est un fichier : vérifie qu'il existe et le retourne seul.
      - Si c'est un dossier : liste tous les fichiers dont le nom contient 'pattern'.
    Exemples :
      pattern=".wcnf"  → match aussi .wcnf.gz, .xml.wcnf, .dimacs.wcnf, etc.
      pattern=".cnf"   → match aussi .cnf, .cnf.gz, .dimacs.cnf, etc.
    """
    instances_dir = Path(instances_dir)

    # Cas 1: un fichier individuel
    if instances_dir.is_file():
        if not instances_dir.exists():
            raise FileNotFoundError(f"Fichier spécifié introuvable : {instances_dir}")
        return [instances_dir.resolve()]

    # Cas 2: un dossier contenant plusieurs instances
    if not instances_dir.exists():
        raise FileNotFoundError(f"Dossier spécifié introuvable : {instances_dir}")

    pattern = pattern.strip().lower()
    instances = sorted(
        p for p in instances_dir.iterdir()
        if p.is_file() and (
            not pattern
            or pattern in p.name.lower()
            or (pattern == ".wcnf" and p.name.lower().endswith((".wcnf.gz", ".xml.wcnf", ".xml.wcnf.gz")))
            or (pattern == ".cnf"  and p.name.lower().endswith((".cnf", ".cnf.gz", ".dimacs.cnf")))
        )
    )

    if not instances:
        print(f"Aucune instance trouvée dans {instances_dir} avec le pattern '{pattern}'")
    return instances
