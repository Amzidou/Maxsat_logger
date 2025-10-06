import json
from pathlib import Path
from typing import List, Optional, Tuple
import typer
import uvicorn
import asyncio
import pandas as pd

from .core.campaign import run_campaign_sequential
from .analytics.stats import generate_basic_reports
from .analytics.similarities import generate_clusters

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
    out: str = typer.Option("./runs", "--out", help="Dossier de sortie (contiendra logs/ + CSV agrégés)"),
    timeout_sec: Optional[int] = typer.Option(None, "--timeout-sec", help="Timeout par run (secondes)"),
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
                out_dir=Path(out), timeout_sec=timeout_sec
            )
        )
        typer.echo(json.dumps({
            "ok": True,
            "trajectories_csv": payload["trajectories_csv"],
            "summary_csv": payload["summary_csv"],
            "results_count": payload.get("results_count", len(payload.get("results", [])))
        }, ensure_ascii=False, indent=2))
    except Exception as ex:
        typer.echo(json.dumps({"ok": False, "error": str(ex)}, ensure_ascii=False, indent=2))
        raise typer.Exit(code=1)

@app.command("stats")
def cli_stats(
    runs: str = typer.Option("data/runs", "--runs", help="Dossier contenant trajectories.csv/summary.csv"),
    out: str  = typer.Option("data/reports", "--out", help="Dossier de sortie pour rapports/PNGs"),
    by: str   = typer.Option("solver_alias", "--by", help="Clé d'agrégation (solver_alias|solver_cmd|solver_tag)"),
    instance: Optional[str] = typer.Option(None, "--instance", help="Basename d'une instance pour tracer la trajectoire"),
    t_min: Optional[float] = typer.Option(None, "--t-min", help="Borne inférieure de temps (sec)"),
    t_max: Optional[float] = typer.Option(None, "--t-max", help="Borne supérieure de temps (sec)"),
    t_at: Optional[float]  = typer.Option(None, "--t-at",  help="Snapshot à t_at (leaderboard relatif)"),
    log_time: bool = typer.Option(False, "--log-time", help="Axe du temps en échelle logarithmique"),  
):
    try:
        res = generate_basic_reports(
            Path(runs),
            Path(out),
            by=by,
            instance_basename=instance,
            t_min=t_min,
            t_max=t_max,
            t_at=t_at,
            log_time=log_time,
        )
        typer.echo(json.dumps({"ok": True, **res}, ensure_ascii=False, indent=2))
    except Exception as ex:
        typer.echo(json.dumps({"ok": False, "error": str(ex)}, ensure_ascii=False, indent=2))
        raise typer.Exit(code=1)

@app.command("clusters")
def cli_clusters(
    runs: str = typer.Option("data/runs", "--runs", help="Dossier contenant trajectories.csv"),
    out: str  = typer.Option("data/reports", "--out", help="Dossier de sortie des rapports"),
    by: str   = typer.Option("solver_alias", "--by", help="Clé d’agrégation (solver_alias|solver_cmd|solver_tag)"),
    metric: str = typer.Option(
        "spearman", "--metric",
        help="Métrique de distance: spearman|pearson|cosine|l2|manhattan|dtw"
    ),
    k: int = typer.Option(2, "--k", help="Nombre de clusters"),
    t_min: Optional[float] = typer.Option(None, "--t-min", help="Borne inférieure de temps (sec)"),
    t_max: Optional[float] = typer.Option(None, "--t-max", help="Borne supérieure de temps (sec)"),
    T: int = typer.Option(100, "--T", help="Nombre de points de discrétisation des courbes"),
    sampling: str = typer.Option(
        "linear", "--sampling",
        help="Schéma d’échantillonnage: linear|log (log sur-pondère le début)"
    ),
    ratio: float = typer.Option(
        1.10, "--ratio",
       help="Intensité du front-loading quand --sampling=log (ratio>1 → plus de poids au début)"
    ),
):
    try:
        # Validations simples
        metric = metric.lower().strip()
        sampling = sampling.lower().strip()

        valid_metrics = {"spearman", "pearson", "cosine", "l2", "euclidean", "manhattan", "l1", "dtw"}
        if metric not in valid_metrics:
            typer.echo(json.dumps({
                "ok": False,
                "error": f"Métrique invalide: {metric}. Autorisées: {sorted(valid_metrics)}"
            }, ensure_ascii=False, indent=2))
            raise typer.Exit(code=1)

        valid_sampling = {"linear", "log", "geom"}
        if sampling not in valid_sampling:
            typer.echo(json.dumps({
                "ok": False,
                "error": f"Sampling invalide: {sampling}. Autorisés: {sorted(valid_sampling)}"
            }, ensure_ascii=False, indent=2))
            raise typer.Exit(code=1)

        if sampling == "log" and ratio <= 0:
            typer.echo(json.dumps({
                "ok": False,
                "error": f"ratio doit être > 0 quand --sampling=log (reçu {ratio})"
            }, ensure_ascii=False, indent=2))
            raise typer.Exit(code=1)

        traj_file = Path(runs) / "trajectories.csv"
        if not traj_file.exists():
            typer.echo(json.dumps({"ok": False, "error": f"Fichier introuvable: {traj_file}"}, ensure_ascii=False, indent=2))
            raise typer.Exit(code=1)

        df_traj = pd.read_csv(traj_file)

        res = generate_clusters(
            df_traj,
            Path(out),
            by=by,
            metric=metric,
            k=k,
            t_min=t_min,
            t_max=t_max,
            T=T,
            sampling=sampling,
            ratio=ratio,
        )

        typer.echo(json.dumps({
            "ok": True,
            "params": {
                "runs": runs,
                "out": out,
                "by": by,
                "metric": metric,
                "k": k,
                "t_min": t_min,
                "t_max": t_max,
                "T": T,
                "sampling": sampling,
                "alpha": alpha,
            },
            **res
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
