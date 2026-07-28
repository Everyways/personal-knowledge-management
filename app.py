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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
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


def process_job(job_id: str, url=None, file_path=None, raw_text=None, label=""):
    ACTIVE_JOBS[job_id]["status"] = "processing"
    ACTIVE_JOBS[job_id]["date"] = _now()
    try:
        title, summary, source = pipeline.process(url=url, file_path=file_path, raw_text=raw_text)
        path = notes.write_note(CONFIG["paths"]["inbox"], title, summary,
                                meta={"url": source})
        ACTIVE_JOBS[job_id].update({"status": "ok", "label": title,
                                    "detail": path.name, "date": _now()})
        log("ok", title, path.name)
    except Exception as e:
        err = str(e)[:200]
        ACTIVE_JOBS[job_id].update({"status": "erreur", "detail": err, "date": _now()})
        log("erreur", label or url or "fichier", err)
        traceback.print_exc()
    finally:
        if file_path:
            Path(file_path).unlink(missing_ok=True)


def _new_job(label: str) -> str:
    job_id = str(uuid.uuid4())[:8]
    ACTIVE_JOBS[job_id] = {"id": job_id, "label": label,
                           "status": "queued", "detail": "", "date": _now()}
    return job_id


FAVICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
               '<text y="0.9em" font-size="90">&#129504;</text></svg>')

PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="/favicon.ico">
<title>second-brain</title><style>
body{font-family:system-ui;max-width:680px;margin:40px auto;padding:0 16px;background:#fafaf7;color:#222}
h1{font-size:1.4em}
form{background:#fff;border:1px solid #ddd;border-radius:10px;padding:18px;margin-bottom:24px}
input[type=url],textarea{width:100%;padding:10px;margin:6px 0 14px;border:1px solid #ccc;border-radius:6px;box-sizing:border-box}
button{background:#3a5a40;color:#fff;border:0;padding:10px 22px;border-radius:6px;font-size:1em;cursor:pointer}
table{width:100%;border-collapse:collapse;font-size:.9em}
td{padding:7px 8px;border-bottom:1px solid #eee;word-break:break-word}
td:first-child{white-space:nowrap;width:80px}
td:nth-child(2){width:28px;text-align:center}
.ok{color:#3a7d44} .erreur{color:#c0392b} .info{color:#aaa} .processing{color:#e07b00}
.dot{display:inline-block;animation:spin 1.2s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
#indicator{font-size:.8em;color:#aaa;margin-bottom:6px;min-height:1.2em}
small{color:#888}
</style></head><body>
<h1>&#129504; second-brain</h1>
<form action="/submit" method="post" enctype="multipart/form-data">
  <label>URL (article, YouTube&hellip;)</label>
  <input type="url" name="url" placeholder="https://&hellip;">
  <label>ou fichier (audio, vid&eacute;o, texte)</label>
  <input type="file" name="file" style="margin:6px 0 14px">
  <label>ou texte &agrave; analyser</label>
  <textarea name="text" rows="4" placeholder="Colle un texte ici&hellip;"></textarea>
  <button>Analyser &rarr; Obsidian</button>
  <p><small>Traitement asynchrone &mdash; l&rsquo;audio/vid&eacute;o sans sous-titres peut prendre plusieurs minutes.</small></p>
</form>
<h2>Traitements</h2>
<div id="indicator"></div>
<table id="jobs-table"><tr><td class="info" colspan="4">Chargement&hellip;</td></tr></table>
<script>
const ICONS = {queued:"&#9203;", processing:'<span class="dot">&#9696;</span>', ok:"&#9989;", erreur:"&#10060;", info:"&#128203;"};
const CSS   = {queued:"info", processing:"processing", ok:"ok", erreur:"erreur", info:"info"};
let timer = null;

function render(data) {
  const rows = data.jobs || [];
  const hist = (data.history || []).filter(h => !rows.find(r => r.label === h.label && r.status !== "queued"));
  const all = [...rows, ...hist].slice(0, 24);
  if (!all.length) {
    document.getElementById("jobs-table").innerHTML = "<tr><td class='info' colspan='4'>Rien pour l'instant</td></tr>";
    return;
  }
  document.getElementById("jobs-table").innerHTML = all.map(e =>
    `<tr>
      <td class="info">${e.date}</td>
      <td title="${e.status}">${ICONS[e.status] || e.status}</td>
      <td class="${CSS[e.status] || ''}">${e.label}</td>
      <td class="info" style="font-size:.8em">${e.detail || ''}</td>
    </tr>`
  ).join("");

  const active = rows.filter(r => r.status === "queued" || r.status === "processing");
  const ind = document.getElementById("indicator");
  if (active.length) {
    ind.innerHTML = `&#128260; ${active.length} job(s) en cours&hellip;`;
    schedule(2000);
  } else {
    ind.innerHTML = "";
    schedule(10000);
  }
}

function schedule(ms) {
  clearTimeout(timer);
  timer = setTimeout(poll, ms);
}

async function poll() {
  try {
    const r = await fetch("/api/status");
    render(await r.json());
  } catch(e) { schedule(5000); }
}

poll();
</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(FAVICON_SVG, media_type="image/svg+xml",
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


@app.post("/submit")
async def submit(background: BackgroundTasks,
                 url: str = Form(""), text: str = Form(""),
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
    return RedirectResponse("/", status_code=303)


@app.post("/api/submit")
async def api_submit(payload: dict, background: BackgroundTasks):
    url = (payload.get("url") or "").strip()
    text = (payload.get("text") or "").strip()
    if not url and not text:
        return {"ok": False, "error": "url ou text requis"}
    label = url or "texte collé"
    job_id = _new_job(label[:80])
    background.add_task(process_job, job_id, url=url or None, raw_text=text or None, label=label)
    return {"ok": True, "job_id": job_id}


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
    scheduler.start()
    return scheduler


if __name__ == "__main__":
    start_scheduler()
    uvicorn.run(app, host="0.0.0.0", port=CONFIG.get("port", 8085))
