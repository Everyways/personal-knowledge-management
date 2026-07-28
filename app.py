"""second-brain : page de soumission + planificateur. Lancer : python app.py"""
import shutil
import tempfile
import traceback
import uuid
from datetime import datetime
from pathlib import Path

import uvicorn
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, UploadFile, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import base64
import ipaddress
import os
import secrets
import time

load_dotenv(Path(__file__).parent / ".env")

import jobs
import notes
import pipeline
from journal import log, last_logs, now as _now

BASE = Path(__file__).parent
CONFIG = yaml.safe_load((BASE / "config.yaml").read_text(encoding="utf-8"))

app = FastAPI(title="second-brain")

# Mount static files and templates (inside existing folder)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

# --- Auth HTTP Basic sur toutes les routes (LAN et internet) ---
# Identifiants dans /home/everyways/second-brain/.env (APP_USERNAME / APP_PASSWORD).
_USER = os.environ.get("APP_USERNAME", "")
_PASS = os.environ.get("APP_PASSWORD", "")

MAX_BODY = 200 * 1024 * 1024  # taille max d'une requête (protège le disque)
MAX_FAILS = 5                 # mots de passe erronés tolérés par fenêtre de 15 min
BLOCK_SECONDS = 15 * 60       # durée du blocage
_FAILS: dict[str, list] = {}      # ip -> horodatages des échecs récents
_BLOCKED: dict[str, float] = {}   # ip -> fin de blocage (epoch)


def _client_ip(request) -> str:
    """IP réelle du visiteur : le trafic tunnel arrive en loopback, la vraie
    IP est alors dans l'en-tête Cf-Connecting-IP posé par Cloudflare."""
    host = request.client.host if request.client else "?"
    try:
        if ipaddress.ip_address(host).is_loopback:
            return request.headers.get("cf-connecting-ip", host)
    except ValueError:
        pass
    return host


if _USER and _PASS:
    @app.middleware("http")
    async def _guard(request, call_next):
        # /favicon.ico est public : les navigateurs le redemandent automatiquement
        # sans forcément réutiliser les identifiants Basic Auth déjà saisis, et son
        # contenu n'est pas sensible.
        if request.url.path == "/favicon.ico":
            return await call_next(request)

        # 1. Limite de taille (un upload géant ne peut plus remplir le disque)
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return Response("Fichier trop volumineux (max 200 Mo)", status_code=413)

        ip = _client_ip(request)
        now = time.time()

        # 2. IP en cours de blocage (anti brute-force)
        if _BLOCKED.get(ip, 0) > now:
            return Response("Trop de tentatives — réessaie plus tard",
                            status_code=429)

        # 3. HTTP Basic
        auth = request.headers.get("authorization", "")
        has_creds = auth.startswith("Basic ")
        user = pwd = ""
        if has_creds:
            try:
                user, _, pwd = base64.b64decode(auth[6:]).decode().partition(":")
            except Exception:
                pass
        if secrets.compare_digest(user, _USER) and secrets.compare_digest(pwd, _PASS):
            _FAILS.pop(ip, None)
            return await call_next(request)

        # Échec AVEC identifiants fournis (l'absence d'en-tête n'est pas comptée :
        # c'est le passage normal du navigateur avant l'affichage du popup)
        if has_creds:
            fails = [t for t in _FAILS.get(ip, []) if now - t < BLOCK_SECONDS]
            fails.append(now)
            _FAILS[ip] = fails
            log("erreur", "auth", f"mot de passe erroné ({ip}, {len(fails)}/{MAX_FAILS})")
            if len(fails) >= MAX_FAILS:
                _BLOCKED[ip] = now + BLOCK_SECONDS
                _FAILS.pop(ip, None)
                log("erreur", "auth", f"IP {ip} bloquée {BLOCK_SECONDS // 60} min")
            if len(_FAILS) > 1000:  # garde-fou mémoire
                _FAILS.clear()
        return Response(status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="second-brain"'})
else:
    print("⚠️  Auth DÉSACTIVÉE : renseigner APP_USERNAME et APP_PASSWORD dans .env")

# In-memory job tracker: {job_id: {id, label, status, detail, date}}
ACTIVE_JOBS: dict[str, dict] = {}

# Un job qui échoue sur un 429 (quota Mistral épuisé) a de bonnes chances de
# passer s'il réessaie un peu plus tard plutôt que d'échouer immédiatement :
# observé en pratique sur le tier gratuit (plusieurs 429 le 28/07).
RATE_LIMIT_BACKOFF = [30, 90]  # secondes entre les essais


def _is_rate_limited(err: str) -> bool:
    low = err.lower()
    return "429" in err or "rate_limited" in low or "rate limit" in low


def process_job(job_id: str, url=None, file_path=None, raw_text=None, label=""):
    ACTIVE_JOBS[job_id]["status"] = "processing"
    ACTIVE_JOBS[job_id]["date"] = _now()
    try:
        if url:
            existing = notes.find_recent_url(url)
            if existing:
                detail = f"déjà traité le {existing['date']} -> {existing['path']}"
                ACTIVE_JOBS[job_id].update({"status": "doublon", "label": label or url,
                                            "detail": detail, "date": _now()})
                log("doublon", label or url, detail, url=url)
                return

        attempt = 0
        while True:
            try:
                title, summary, source, tags = pipeline.process(
                    url=url, file_path=file_path, raw_text=raw_text)
                break
            except Exception as e:
                err = str(e)
                if attempt < len(RATE_LIMIT_BACKOFF) and _is_rate_limited(err):
                    wait = RATE_LIMIT_BACKOFF[attempt]
                    attempt += 1
                    ACTIVE_JOBS[job_id].update({
                        "status": "attente",
                        "detail": f"limite atteinte, nouvel essai dans {wait}s ({attempt}/{len(RATE_LIMIT_BACKOFF)})",
                        "date": _now()})
                    time.sleep(wait)
                    ACTIVE_JOBS[job_id]["status"] = "processing"
                    continue
                raise

        meta = {"url": source}
        if tags:
            meta["tags"] = tags
        path = notes.write_note(CONFIG["paths"]["inbox"], title, summary, meta=meta)
        ACTIVE_JOBS[job_id].update({"status": "ok", "label": title,
                                    "detail": path.name, "date": _now(), "tags": tags})
        log("ok", title, path.name, url=url or "", tags=tags)
    except Exception as e:
        err = str(e)[:200]
        ACTIVE_JOBS[job_id].update({"status": "erreur", "detail": err, "date": _now()})
        log("erreur", label or url or "fichier", err, url=url or "")
        traceback.print_exc()
    finally:
        if file_path:
            Path(file_path).unlink(missing_ok=True)


def _new_job(label: str) -> str:
    job_id = str(uuid.uuid4())[:8]
    ACTIVE_JOBS[job_id] = {"id": job_id, "label": label,
                           "status": "queued", "detail": "", "date": _now()}
    return job_id


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(BASE / "static" / "favicon.ico",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/status")
def api_status():
    active = list(ACTIVE_JOBS.values())
    # Exclude history entries already covered by active jobs
    active_labels = {j["label"] for j in active}
    history = [e for e in last_logs(20) if e["label"] not in active_labels]
    return JSONResponse({"jobs": active, "history": history})

@app.get("/api/today")
def api_today():
    """Retourne les médias lus aujourd'hui (status=ok du jour)."""
    today = datetime.now().strftime("%d/%m")
    entries = last_logs(100)
    today_entries = [e for e in entries if e["date"].startswith(today) and e["status"] == "ok"]
    return JSONResponse(today_entries)


@app.get("/api/search")
def api_search(q: str = ""):
    return JSONResponse(notes.search_notes(q))


@app.get("/api/note")
def api_note(path: str = ""):
    content = notes.get_note_safe(path)
    if content is None:
        return JSONResponse({"error": "introuvable"}, status_code=404)
    return JSONResponse({"content": content})


def _queue_urls(background: BackgroundTasks, raw: str):
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("http://") or line.startswith("https://"):
            job_id = _new_job(line[:80])
            background.add_task(process_job, job_id, url=line, label=line)


@app.post("/submit")
async def submit(background: BackgroundTasks,
                 url: str = Form(""), urls: str = Form(""), text: str = Form(""),
                 file: UploadFile = File(None)):
    if file and file.filename:
        # Path(...).name strips any directory components (e.g. "../../etc/passwd")
        # so a malicious filename can't escape the temp dir; a uuid prefix avoids
        # two concurrent uploads with the same name overwriting each other.
        safe_name = Path(file.filename).name or "upload"
        tmp = Path(tempfile.gettempdir()) / f"{uuid.uuid4().hex[:8]}_{safe_name}"
        with tmp.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        job_id = _new_job(safe_name)
        background.add_task(process_job, job_id, file_path=str(tmp), label=safe_name)
    elif url.strip():
        job_id = _new_job(url.strip()[:80])
        background.add_task(process_job, job_id, url=url.strip(), label=url.strip())
    elif text.strip():
        job_id = _new_job("texte collé")
        background.add_task(process_job, job_id, raw_text=text, label="texte collé")
    if urls.strip():
        _queue_urls(background, urls)
    return RedirectResponse("/", status_code=303)


@app.post("/api/submit")
async def api_submit(payload: dict, background: BackgroundTasks):
    url = (payload.get("url") or "").strip()
    text = (payload.get("text") or "").strip()
    urls = payload.get("urls") or []
    if not url and not text and not urls:
        return {"ok": False, "error": "url, urls ou text requis"}
    job_ids = []
    if url or text:
        label = url or "texte collé"
        job_id = _new_job(label[:80])
        background.add_task(process_job, job_id, url=url or None, raw_text=text or None, label=label)
        job_ids.append(job_id)
    for u in urls:
        u = (u or "").strip()
        if u.startswith("http://") or u.startswith("https://"):
            job_id = _new_job(u[:80])
            background.add_task(process_job, job_id, url=u, label=u)
            job_ids.append(job_id)
    return {"ok": True, "job_ids": job_ids}


@app.post("/api/retry")
async def api_retry(payload: dict, background: BackgroundTasks):
    url = (payload.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "url requise"}
    job_id = _new_job(url[:80])
    background.add_task(process_job, job_id, url=url, label=url)
    return {"ok": True, "job_id": job_id}


@app.get("/share")
async def share(background: BackgroundTasks, url: str = "", text: str = "", title: str = ""):
    """Cible du Web Share Target (PWA) : reçoit ce que le téléphone partage.
    Selon l'appli source, le lien arrive dans `url` OU dans `text` — on
    détecte donc le cas où `text` est en fait une URL."""
    candidate = url.strip() or text.strip()
    if candidate.startswith("http://") or candidate.startswith("https://"):
        job_id = _new_job(candidate[:80])
        background.add_task(process_job, job_id, url=candidate, label=candidate)
    else:
        raw = (title.strip() + "\n" + text.strip()).strip()
        if raw:
            job_id = _new_job("partagé depuis mobile")
            background.add_task(process_job, job_id, raw_text=raw, label="partagé depuis mobile")
    return RedirectResponse("/", status_code=303)


def start_scheduler():
    sch = CONFIG["schedule"]
    scheduler = BackgroundScheduler(timezone="Europe/Paris")
    # Pas de job git_push dédié : project_analysis (ci-dessous) et morning_digest
    # font déjà un pull+push à la fin de leur exécution, ce qui suffit à pousser
    # aussi les notes soumises dans la journée (add -A ramasse tout).
    scheduler.add_job(jobs.project_analysis, "cron", hour=sch["project_hour"], minute=0,
                      id="analyse_projet", misfire_grace_time=3600)
    scheduler.add_job(jobs.morning_digest, "cron",
                      hour=sch["digest_hour"], minute=sch.get("digest_minute", 0),
                      id="veille_email", misfire_grace_time=3600)
    scheduler.add_job(jobs.weekly_review, "cron",
                      day_of_week=sch.get("weekly_day", "sun"), hour=sch.get("weekly_hour", 8),
                      minute=0, id="synthese_hebdo", misfire_grace_time=3600)
    scheduler.start()
    return scheduler


if __name__ == "__main__":
    start_scheduler()
    uvicorn.run(app, host="0.0.0.0", port=CONFIG.get("port", 8085))
