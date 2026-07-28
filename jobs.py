"""Tâches planifiées : git sync, analyse projet quotidienne, veille email du matin."""
import datetime
import fcntl
import os
import smtplib
import subprocess
import urllib.parse
from contextlib import contextmanager
from email.mime.text import MIMEText
from pathlib import Path

import yaml

import notes
import pipeline
from journal import log

CONFIG = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text(encoding="utf-8"))
VAULT = Path(CONFIG["vault_path"]).expanduser()

# Même fichier que celui utilisé par sync-vault.sh (flock) : empêche le job
# planifié ici et le timer systemd de toucher le repo git en même temps
# (source d'un vrai conflit .git/index.lock observé en test).
LOCK_PATH = Path(__file__).parent / "vault-sync.lock"


@contextmanager
def _vault_lock():
    LOCK_PATH.touch(exist_ok=True)
    with LOCK_PATH.open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# ---------- Git ----------

def _git(*args):
    return subprocess.run(
        ["git", "-C", str(VAULT), *args], capture_output=True, text=True, timeout=300
    )

def _pull():
    """Pull non verrouillé — réservé à git_pull() et git_push() qui gèrent le verrou."""
    r = _git("pull", "--rebase", "--autostash")
    if r.returncode != 0:
        log("erreur", "git pull (vault)", (r.stdout + r.stderr).strip()[:200])
    return r

def git_pull():
    with _vault_lock():
        return _pull()

def git_push():
    with _vault_lock():
        _pull()
        _git("add", "-A")
        r = _git("commit", "-m", f"second-brain sync {datetime.datetime.now():%Y-%m-%d %H:%M}")
        if "nothing to commit" in (r.stdout + r.stderr):
            return
        if r.returncode != 0:
            log("erreur", "git commit (vault)", (r.stdout + r.stderr).strip()[:200])
            return
        p = _git("push")
        if p.returncode != 0:
            log("erreur", "git push (vault)", (p.stdout + p.stderr).strip()[:200])
        else:
            log("ok", "git push (vault)", "sync effectuée")


# ---------- Email ----------

def send_email(subject: str, body: str):
    cfg = CONFIG["email"]
    password = os.environ.get("SMTP_PASSWORD") or cfg.get("smtp_password")
    if not password:
        log("erreur", "email (veille)", "SMTP_PASSWORD manquant (.env non chargé ?)")
        raise RuntimeError("SMTP_PASSWORD manquant : voir /home/everyways/second-brain/.env")
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = cfg["smtp_user"]
    msg["To"] = cfg["to"]
    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=60) as s:
        s.starttls()
        s.login(cfg["smtp_user"], password)
        s.send_message(msg)


# ---------- Veille du matin ----------

def read_themes():
    raw = notes.read_note(CONFIG["themes_file"])
    themes = []
    for line in raw.splitlines():
        line = line.strip().lstrip("-*").strip()
        if line and not line.startswith("#") and not line.startswith("---"):
            themes.append(line)
    return themes


def fetch_news(theme: str, n: int = 6):
    import feedparser
    q = urllib.parse.quote(theme)
    feed = feedparser.parse(
        f"https://news.google.com/rss/search?q={q}&hl=fr&gl=FR&ceid=FR:fr"
    )
    return [(e.title, e.link) for e in feed.entries[:n]]


def morning_digest():
    git_pull()
    today = datetime.date.today().isoformat()
    themes = read_themes()
    sections = []
    for theme in themes:
        try:
            items = fetch_news(theme)
            if not items:
                continue
            listing = "\n".join(f"- {t}\n  {l}" for t, l in items)
            synthesis = pipeline.ollama(
                f"Voici les actualités du jour sur le thème « {theme} » :\n{listing}\n\n"
                "Rédige une synthèse en français (5-8 lignes) des tendances notables, "
                "puis indique les 2 ou 3 liens les plus importants avec leur URL."
            )
            sections.append(f"## {theme}\n\n{synthesis}")
        except Exception as e:
            sections.append(f"## {theme}\n\nErreur : {e}")
    body = "\n\n".join(sections) if sections else (
        f"Aucun thème défini. Ajoute des thèmes (un par ligne) dans "
        f"{CONFIG['themes_file']} de ton vault Obsidian."
    )
    # Joindre l'analyse projet du jour si elle existe
    analysis_folder = VAULT / CONFIG["paths"]["analyses"]
    if analysis_folder.exists():
        todays = sorted(analysis_folder.glob(f"{today}*.md"))
        if todays:
            body += "\n\n# Analyse projet du jour\n\n" + todays[-1].read_text(encoding="utf-8")
    notes.write_note(CONFIG["paths"]["veille"], f"Veille du {today}", body)
    send_email(f"Veille du {today}", body)
    # Pas de push ici : project_analysis (03:00, avant ce job) est l'unique
    # point de push quotidien. La note du jour partira au push du lendemain.


# ---------- Analyse projet quotidienne ----------

def project_analysis():
    git_pull()
    projects = CONFIG["projects"]
    today = datetime.date.today()
    project = projects[today.toordinal() % len(projects)]
    desc = notes.read_note(f"Projets/{project}.md")[:4000]
    veille = notes.recent_notes(CONFIG["paths"]["veille"], n=2)
    inbox = notes.recent_notes(CONFIG["paths"]["inbox"], n=3)
    prompt = f"""Tu es un conseiller produit/business pragmatique. Projet analysé aujourd'hui : « {project} ».

Description du projet :
{desc or '(aucune description trouvée — suggère d en créer une dans Projets/' + project + '.md)'}

Contexte récent (veille et lectures de l'utilisateur) :
{(veille + chr(10) + inbox)[:6000]}

Tâche :
1. Évalue d'abord si le contexte récent contient quelque chose de PERTINENT pour ce
   projet. Ignore les newsletters génériques, pages d'erreur, captchas, contenus
   promotionnels sans rapport.
2. Si le contexte est pertinent : propose 1 à 3 améliorations concrètes et priorisées
   (impact / effort), chacune rattachée explicitement à l'article source qui l'inspire.
3. Si rien n'est pertinent : écris simplement "Rien de pertinent dans le contexte
   récent pour {project}." et arrête-toi là. C'est une réponse correcte et attendue.
4. Si (et seulement si) un article précis du contexte révèle une opportunité business
   concrète et rentable — liée ou non au projet — ajoute une section
   "## Opportunité business (à valider)" : 3-5 lignes, l'article source cité, et un
   premier pas concret. Si aucun article ne fonde une telle opportunité, n'écris
   rien sur le sujet (pas de section vide, pas d'opportunité inventée).
5. Si (et seulement si) un article précis du contexte contient un conseil ou une
   information utile pour les finances personnelles de l'utilisateur (épargne,
   investissement, fiscalité, dépenses), ajoute une section
   "## Finances personnelles (à valider)" : 2-4 lignes, l'article source cité.
   Sinon, n'écris rien sur le sujet.
6. Ne propose JAMAIS d'idée qui ne découle ni de la description du projet ni du
   contexte. N'invente pas de fonctionnalités sur des domaines absents de la
   description (finance, IA, etc. si le projet n'en parle pas).

Réponds en français, markdown, concis."""
    result = pipeline.ollama(prompt)
    notes.write_note(CONFIG["paths"]["analyses"], f"Analyse {project}", result,
                     meta={"projet": project})
    git_push()
