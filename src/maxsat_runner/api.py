from pathlib import Path
from typing import Dict, List
import time
import asyncio

from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .core.campaign import run_campaign_sequential

# Dossiers autorisés pour l'API FS
DATA_ROOT = (Path.cwd() / "data").resolve()
DATA_ROOT.mkdir(parents=True, exist_ok=True)
(DATA_ROOT / "instances").mkdir(parents=True, exist_ok=True)
(DATA_ROOT / "runs").mkdir(parents=True, exist_ok=True)

api = FastAPI(title="MaxSAT Runner API", version="0.4.0")

# ---- UI statique (/ui) ----
WEBUI_DIR = Path(__file__).with_name("webui")
api.mount("/ui", StaticFiles(directory=str(WEBUI_DIR), html=True), name="ui")

@api.get("/")
def root():
    # redirige vers l'UI
    return RedirectResponse(url="/ui/")

# ---- File de jobs ----
JOBS: Dict[str, Dict] = {}
RUNNING = False

def _job_id() -> str:
    return f"job_{int(time.time()*1000)}"

def _safe_join_under_root(root: Path, subpath: str) -> Path:
    target = (root / subpath).resolve()
    if root not in target.parents and target != root:
        raise ValueError("Chemin hors périmètre autorisé")
    return target

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
                    solver_cmds=p["solver_cmds"],
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

# ---- API RUN ----
@api.post("/run")
async def api_run(body: Dict, background: BackgroundTasks):
    required = ["solver_cmds","instances_dir","pattern","out_dir","tag"]
    for k in required:
        if k not in body:
            return JSONResponse({"ok": False, "error": f"champ manquant: {k}"}, status_code=400)

    try:
        inst_dir = _safe_join_under_root(DATA_ROOT, body["instances_dir"]) if not Path(body["instances_dir"]).is_absolute() else Path(body["instances_dir"]).resolve()
        out_dir  = _safe_join_under_root(DATA_ROOT, body["out_dir"]) if not Path(body["out_dir"]).is_absolute() else Path(body["out_dir"]).resolve()
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

# ---- API FS (optionnel : mkdir/upload/ls) ----
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
