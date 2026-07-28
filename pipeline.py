"""Extraction de contenu (article / YouTube / audio) + résumé via Mistral."""
import ipaddress
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).parent
YT_DLP = [sys.executable, "-m", "yt_dlp"]

import requests
import yaml
from mistralai.client import Mistral
from mistralai.client.utils.retries import BackoffStrategy, RetryConfig

CONFIG = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text(encoding="utf-8"))

SYSTEM = (
    "Tu es un assistant d'analyse de contenu. Tu réponds toujours en français, "
    "de façon concise et structurée, en markdown."
)


def mistral_summarize(prompt: str, system: str = SYSTEM) -> str:
    """Appelle Mistral (modèle gratuit)."""
    api_key = os.environ.get("MISTRAL_API_KEY") or CONFIG.get("mistral_api_key")
    if not api_key:
        raise ValueError("MISTRAL_API_KEY manquant dans .env ou config.yaml")

    client = Mistral(api_key=api_key)
    response = client.chat.complete(
        model=CONFIG.get("mistral_model", "mistral-large-latest"),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ],
        max_tokens=2000,
        # Le tier gratuit renvoie fréquemment 429 ; on réessaie avec backoff
        # exponentiel plutôt que de faire échouer tout le job.
        retries=RetryConfig(
            strategy="backoff",
            backoff=BackoffStrategy(
                initial_interval=1000, max_interval=20_000,
                exponent=2, max_elapsed_time=120_000,
            ),
            retry_connection_errors=True,
        ),
    )
    return response.choices[0].message.content.strip()


def summarize(text: str, title: str = "") -> str:
    """Résumé map-reduce via Mistral."""
    text = text.strip()
    if not text:
        raise ValueError("Aucun texte à résumer")
    chunks = [text[i : i + 8000] for i in range(0, len(text), 8000)][:6]
    if len(chunks) > 1:
        partials = [
            mistral_summarize(f"Résume fidèlement les points clés de cet extrait (partie {i+1}/{len(chunks)}) :\n\n{c}")
            for i, c in enumerate(chunks)
        ]
        text = "\n\n".join(partials)
    prompt = f"""Analyse ce contenu{f' intitulé « {title} »' if title else ''} et dégage les points importants à retenir.

Réponds exactement dans ce format markdown :
## Résumé
(3 phrases maximum)
## Points clés
(5 à 8 puces)
## Idées actionnables
(2 à 4 puces, ou "Aucune" si non pertinent)

Contenu :
{text}"""
    return mistral_summarize(prompt)


# ---------- Extraction ----------

YOUTUBE_RE = re.compile(r"(youtube\.com|youtu\.be)")
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".opus", ".mp4", ".webm", ".mkv"}


def _fetch_via_jina(url: str):
    """Repli quand le fetch direct est bloqué (anti-bot type Cloudflare qui bannit
    l'IP du serveur) : r.jina.ai est un proxy lecteur public, la requête part de
    son réseau à lui et non du serveur second-brain."""
    try:
        r = requests.get(f"https://r.jina.ai/{url}", timeout=30)
    except requests.RequestException:
        return None, None
    if r.status_code != 200 or not r.text.strip():
        return None, None
    text = r.text
    m = re.search(r"^Title:\s*(.+)$", text, re.MULTILINE)
    title = m.group(1).strip() if m else url
    marker = "Markdown Content:"
    idx = text.find(marker)
    body = text[idx + len(marker):].strip() if idx != -1 else text
    return (title, body) if body else (None, None)


def extract_article(url: str):
    import trafilatura

    html = trafilatura.fetch_url(url)
    text = trafilatura.extract(html) if html else None
    if text:
        meta = trafilatura.extract_metadata(html)
        title = (meta.title if meta and meta.title else url)
        return title, text

    # Fetch direct échoué ou texte non extractible -> repli via jina.ai
    title, text = _fetch_via_jina(url)
    if text:
        return title, text

    raise ValueError(f"Page inaccessible (fetch direct et repli jina.ai) : {url}")


def _clean_subs(raw: str) -> str:
    """Nettoie un fichier .srt/.vtt : enlève horodatages, numéros, doublons."""
    lines, seen = [], None
    for line in raw.splitlines():
        line = re.sub(r"<[^>]+>", "", line).strip()
        if (not line or line.isdigit() or "-->" in line
                or line.startswith(("WEBVTT", "Kind:", "Language:"))):
            continue
        if line != seen:
            lines.append(line)
            seen = line
    return " ".join(lines)


def extract_youtube(url: str):
    info = subprocess.run(
        [*YT_DLP, "--no-download", "--print", "%(title)s", url],
        capture_output=True, text=True, timeout=120,
    )
    title = info.stdout.strip().splitlines()[-1] if info.stdout.strip() else url
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(
            [*YT_DLP, "--skip-download", "--write-auto-subs", "--write-subs",
             "--sub-langs", "fr,en", "-o", f"{td}/v", url],
            capture_output=True, text=True, timeout=600,
        )
        subs = sorted(Path(td).glob("v*"))
        if subs:
            text = _clean_subs(subs[0].read_text(errors="ignore"))
            if len(text) > 200:
                return title, text
        # Pas de sous-titres -> transcription audio (LENT sur ce CPU)
        subprocess.run(
            [*YT_DLP, "-x", "--audio-format", "mp3", "--audio-quality", "9",
             "-o", f"{td}/audio.%(ext)s", url],
            capture_output=True, text=True, timeout=1800,
        )
        audios = list(Path(td).glob("audio.*"))
        if not audios:
            raise ValueError("Ni sous-titres ni audio récupérables pour cette vidéo")
        return title, transcribe_file(audios[0])


def transcribe_file(path) -> str:
    """Transcription locale via faster-whisper (modèle 'base', CPU int8)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ValueError(
            "faster-whisper n'est pas installé (pip install faster-whisper). "
            "Transcription audio indisponible."
        )
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(path), vad_filter=True)
    return " ".join(s.text.strip() for s in segments)


def _check_url_public(url: str):
    """Anti-SSRF : refuse les URL qui pointent vers le serveur lui-même ou le
    réseau local (une URL soumise ne doit pas pouvoir sonder le LAN/l'app)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL refusée : seuls http(s) sont acceptés")
    host = parsed.hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError(f"Hôte introuvable : {host}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("URL refusée : adresse privée/locale")


def process(url: str = None, file_path: str = None, raw_text: str = None):
    """Point d'entrée : retourne (titre, résumé, source)."""
    if url:
        url = url.strip()
        _check_url_public(url)
        if YOUTUBE_RE.search(url):
            title, text = extract_youtube(url)
        else:
            title, text = extract_article(url)
        return title, summarize(text, title), url
    if file_path:
        p = Path(file_path)
        if p.suffix.lower() in AUDIO_EXT:
            text = transcribe_file(p)
        else:
            text = p.read_text(errors="ignore")
        return p.stem, summarize(text, p.stem), p.name
    if raw_text:
        title = raw_text.strip().splitlines()[0][:80]
        return title, summarize(raw_text, title), "texte collé"
    raise ValueError("Rien à traiter")
