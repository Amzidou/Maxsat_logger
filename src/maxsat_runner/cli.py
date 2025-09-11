import json
from pathlib import Path
from typing import List, Optional, Tuple
import typer
import uvicorn
import asyncio

from .core.campaign import run_campaign_sequential
from .analytics.stats import generate_basic_reports 

app = typer.Typer(help="Orchestration MaxSAT: CLI & serveur API + UI")

def _parse_solver_arg(s: str) -> Tuple[Optional[str], str]:
    s = s.strip()
    if "=" in s and not s.startswith("[cwd="):
        a, c = s.split("=", 1)
        if "{inst}" in c:
            return (a.strip(), c.strip())
    return (None, s)

@app.command("run")
def cli_run(
    solver: List[str] = typer.Option(..., "--solver", help="Commande (répétable), optionnellement alias=CMD, doit contenir {inst}"),
    instances: str = typer.Option(..., "--instances", help="Dossier d'instances"),
    pattern: str = typer.Option(".wcnf", "--pattern", help="Extension à filtrer (ex: .wcnf)"),
    out: str = typer.Option("./runs", "--out", help="Dossier de sortie CSV"),
    tag: str = typer.Option("run", "--tag", help="Étiquette de la campagne"),
    timeout_sec: Optional[int] = typer.Option(None, "--timeout-sec", help="Timeout par instance (secondes)"),
):
    pairs = []
    for s in solver:
        alias, cmd = _parse_solver_arg(s)
        pairs.append({"alias": alias, "cmd": cmd})

    try:
        payload = asyncio.run(
            run_campaign_sequential(
                solver_pairs=pairs, solver_cmds=None,
                instances_dir=Path(instances), pattern=pattern,
                out_dir=Path(out), tag=tag, timeout_sec=timeout_sec
            )
        )
        typer.echo(json.dumps({
            "ok": True,
            "trajectories_csv": payload["trajectories_csv"],
            "summary_csv": payload["summary_csv"],
            "results_count": len(payload["results"])
        }, ensure_ascii=False, indent=2))
    except Exception as ex:
        typer.echo(json.dumps({"ok": False, "error": str(ex)}, ensure_ascii=False, indent=2))
        raise typer.Exit(code=1)

@app.command("stats")
def cli_stats(
    runs: str = typer.Option("data/runs", "--runs", help="Dossier contenant trajectories.csv/summary.csv"),
    out: str  = typer.Option("data/reports", "--out", help="Dossier de sortie pour rapports/PNGs"),
    by: str   = typer.Option("solver_cmd", "--by", help="Clé d'agrégation (solver_cmd|solver_tag)"),
    instance: Optional[str] = typer.Option(None, "--instance", help="Basename d'une instance pour tracer la trajectoire")
):
    try:
        res = generate_basic_reports(Path(runs), Path(out), by=by, instance_basename=instance)
        typer.echo(json.dumps({"ok": True, **res}, ensure_ascii=False, indent=2))
    except Exception as ex:
        typer.echo(json.dumps({"ok": False, "error": str(ex)}, ensure_ascii=False, indent=2))
        raise typer.Exit(code=1)

@app.command("serve")
def cli_serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port")
):
    uvicorn.run("maxsat_runner.api:api", host=host, port=port, reload=False)
