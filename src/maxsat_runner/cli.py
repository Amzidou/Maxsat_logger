import json
from pathlib import Path
from typing import List, Optional
import typer
import uvicorn
import asyncio

from .core.campaign import run_campaign_sequential

app = typer.Typer(help="Orchestration MaxSAT: CLI & serveur API + UI")

@app.command("run")
def cli_run(
    solver: List[str] = typer.Option(..., "--solver", help="Commande (répétable) avec placeholder {inst}"),
    instances: str = typer.Option(..., "--instances", help="Dossier d'instances"),
    pattern: str = typer.Option(".wcnf", "--pattern", help="Extension à filtrer (ex: .wcnf)"),
    out: str = typer.Option("./runs", "--out", help="Dossier de sortie CSV"),
    tag: str = typer.Option("run", "--tag", help="Étiquette de la campagne"),
    timeout_sec: Optional[int] = typer.Option(None, "--timeout-sec", help="Timeout par instance (secondes)"),
):
    try:
        payload = asyncio.run(
            run_campaign_sequential(
                solver, Path(instances), pattern, Path(out), tag, timeout_sec=timeout_sec
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

@app.command("serve")
def cli_serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port")
):
    uvicorn.run("maxsat_runner.api:api", host=host, port=port, reload=False)
