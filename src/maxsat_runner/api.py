from pathlib import Path
from typing import Dict, List, Optional
import time
import asyncio

from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .core.campaign import run_campaign_sequential
from .analytics.stats import generate_basic_reports  # NEW

api = FastAPI(title="MaxSAT Runner API", version="0.4.0")

# --- FS root sandbox (toutes les E/S sous ./data) ---
DATA_ROOT = (Path.cwd() / "data").resolve()
DATA_ROOT.mkdir(parents=True, exist_ok=True)
(DATA_ROOT / "instances").mkdir(parents=True, exist_ok=True)
(DATA_ROOT / "runs").mkdir(parents=True, exist_ok=True)
(DATA_ROOT / "reports").mkdir(parents=True, exist_ok=True)  # pour /stats

# UI statique
WEBUI_DIR = Path(__file__).with_name("webui")
api.mount("/ui", StaticFiles(directory=str(WEBUI_DIR), html=True), name="ui")

# EXPOSITION EN LECTURE SEULE DES FICHIERS DE data/
# -> permet d'afficher les PNG/CSV produits par /stats directement dans le navigateur
api.mount("/data", StaticFiles(directory=str(DATA_ROOT), html=False), name="data")

@api.get("/")
def root():
    return RedirectResponse(url="/ui/")

# ---------- File de jobs ----------
JOBS: Dict[str, Dict] = {}
RUNNING = False

def _job_id() -> str:
    return f"job_{int(time.time()*1000)}"

def _safe_join_under_root(root: Path, subpath: str) -> Path:
    """
    Résout un chemin sous `root` et vérifie qu'on ne sort pas du périmètre.
    Accepte aussi un chemin absolu déjà sous root.
    """
    base = Path(subpath)
    target = (base if base.is_absolute() else (root / base)).resolve()
    if root not in target.parents and target != root:
        raise ValueError("Chemin hors périmètre autorisé")
    return target

def _path_to_data_url(p: Optional[Path]) -> Optional[str]:
    """
    Convertit un chemin absolu sous DATA_ROOT en URL publique '/data/...'.
    """
    if p is None:
        return None
    rp = Path(p).resolve()
    try:
        rel = rp.relative_to(DATA_ROOT)
        return f"/data/{rel.as_posix()}"
    except Exception:
        return None

async def _worker():
    global RUNNING
    if RUNNING:
        return
    RUNNING = True
    try:
        while True:
            next_id = None
            for jid, info in JOBS.items():
                if info["status"] == "queued":
                    next_id = jid
                    break
            if next_id is None:
                break
            JOBS[next_id]["status"] = "running"
            p = JOBS[next_id]["params"]
            try:
                result = await run_campaign_sequential(
                    solver_pairs=p.get("solver_pairs"),
                    solver_cmds=p.get("solver_cmds"),
                    instances_dir=Path(p["instances_dir"]),
                    pattern=p["pattern"],
                    out_dir=Path(p["out_dir"]),
                    tag=p["tag"],
                    timeout_sec=p.get("timeout_sec"),
                )
                JOBS[next_id]["result"] = result
                JOBS[next_id]["status"] = "done"
            except Exception as ex:
                JOBS[next_id]["result"] = {"error": str(ex)}
                JOBS[next_id]["status"] = "error"
    finally:
        RUNNING = False

# ---------- RUN ----------
@api.post("/run")
async def api_run(body: Dict, background: BackgroundTasks):
    # Compat: accepter solver_pairs OU solver_cmds (legacy)
    if not ("solver_pairs" in body or "solver_cmds" in body):
        return JSONResponse({"ok": False, "error": "champ manquant: solver_pairs ou solver_cmds"}, status_code=400)

    required = ["instances_dir","pattern","out_dir","tag"]
    for k in required:
        if k not in body:
            return JSONResponse({"ok": False, "error": f"champ manquant: {k}"}, status_code=400)

    try:
        inst_dir = _safe_join_under_root(DATA_ROOT, body["instances_dir"])
        out_dir  = _safe_join_under_root(DATA_ROOT, body["out_dir"])
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    body["instances_dir"] = str(inst_dir)
    body["out_dir"] = str(out_dir)

    if "timeout_sec" in body and body["timeout_sec"] is not None:
        try:
            body["timeout_sec"] = int(body["timeout_sec"])
        except ValueError:
            return JSONResponse({"ok": False, "error": "timeout_sec doit être un entier"}, status_code=400)

    jid = _job_id()
    JOBS[jid] = {"status": "queued", "params": body, "result": None}
    background.add_task(_worker)
    return {"ok": True, "job_id": jid}

@api.get("/status/{job_id}")
def api_status(job_id: str):
    if job_id not in JOBS:
        return JSONResponse({"ok": False, "error": "job introuvable"}, status_code=404)
    info = JOBS[job_id]
    return {"ok": True, "job_id": job_id, "status": info["status"], "result": info["result"]}

# ---------- STATS ----------
@api.post("/stats")
def api_stats(body: Dict):
    runs_dir = body.get("runs_dir", "runs")
    out_dir  = body.get("out_dir", "reports")
    by       = body.get("by", "solver_alias")
    instance = body.get("instance", None)
    t_min    = body.get("t_min", None)
    t_max    = body.get("t_max", None)
    t_at     = body.get("t_at", None)

    try:
        runs_p = _safe_join_under_root(DATA_ROOT, runs_dir)
        out_p  = _safe_join_under_root(DATA_ROOT, out_dir)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    try:
        res = generate_basic_reports(
            runs_p, out_p, by=by, instance_basename=instance,
            t_min=t_min, t_max=t_max, t_at=t_at
        )
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)}, status_code=400)

    # URLs publiques
    def u(p): return _path_to_data_url(Path(p)) if p else None

    urls = {
        "leaderboard_csv_url": u(res["leaderboard_csv"]),
        "plot_leaderboard_wins_url": u(res["plot_leaderboard_wins"]),
        "plot_time_to_best_box_url": u(res["plot_time_to_best_box"]),
        "plot_trajectory_url": u(res.get("plot_trajectory")),
        "leaderboard_relative_csv_url": u(res["leaderboard_relative_csv"]),
        "avg_scores_csv_url": _path_to_data_url(Path(res["avg_scores_csv"])) if res.get("avg_scores_csv") else None,
        "avg_scores_png_url": _path_to_data_url(Path(res["avg_scores_png"])) if res.get("avg_scores_png") else None,
    }

    inst_cost_urls = []
    for it in res.get("instance_plots", []):
        inst_cost_urls.append({"instance": it["instance"], "url": u(it["png"])})

    inst_score_urls = []
    for it in res.get("instance_score_plots", []):
        inst_score_urls.append({"instance": it["instance"], "url": u(it["png"])})

    return {"ok": True, **res, **urls,
            "instance_plots": inst_cost_urls,
            "instance_score_plots": inst_score_urls}

# ---------- FS utilitaires (optionnels) ----------
@api.post("/fs/mkdir")
def api_mkdir(path: str):
    try:
        target = _safe_join_under_root(DATA_ROOT, path)
        target.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "created": str(target)}
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)}, status_code=400)

@api.post("/fs/upload")
async def api_upload(dir: str = Form(...), files: List[UploadFile] = File(...)):
    try:
        target_dir = _safe_join_under_root(DATA_ROOT, dir)
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)}, status_code=400)

    saved: List[str] = []
    for f in files:
        dest = target_dir / f.filename
        content = await f.read()
        dest.write_bytes(content)
        saved.append(str(dest))
    return {"ok": True, "saved": saved}

@api.get("/fs/ls")
def api_ls(path: str = "instances"):
    try:
        p = _safe_join_under_root(DATA_ROOT, path)
        if not p.exists() or not p.is_dir():
            return JSONResponse({"ok": False, "error": "dossier introuvable"}, status_code=404)
        items = []
        for e in sorted(p.iterdir()):
            items.append({
                "name": e.name,
                "is_dir": e.is_dir(),
                "size": (e.stat().st_size if e.is_file() else None),
            })
        return {"ok": True, "cwd": str(p), "items": items}
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)}, status_code=400)

@api.get("/fs/root")
def api_root():
    return {"ok": True, "data_root": str(DATA_ROOT)}
