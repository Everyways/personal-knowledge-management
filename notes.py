"""Écriture des notes markdown dans le vault Obsidian."""
import datetime
import json
import re
from pathlib import Path

import yaml

CONFIG = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text(encoding="utf-8"))
VAULT = Path(CONFIG["vault_path"]).expanduser()

# Dossiers exposés à la recherche / prévisualisation depuis l'UI web (pas tout
# le vault : Projets/*.md par ex. reste privé à Obsidian).
SEARCHABLE = ["Inbox", "Veille", "Projets/Analyses"]

# Index url -> {date, path} pour la détection de doublons. Vit à côté de
# journal.jsonl (pas dans le vault : c'est un détail d'implémentation de
# l'app, pas une note à versionner).
URL_INDEX = Path(__file__).parent / "url_index.json"
URL_INDEX_MAX = 500  # garde-fou mémoire/disque


def slug(s: str) -> str:
    s = re.sub(r"[^\w\- ]+", "", s, flags=re.UNICODE).strip()
    return re.sub(r"\s+", "-", s)[:60].strip("-") or "note"


def _load_url_index() -> dict:
    if not URL_INDEX.exists():
        return {}
    try:
        return json.loads(URL_INDEX.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def find_recent_url(url: str, days: int = 7) -> dict | None:
    """Retourne {date, path} si cette URL a déjà été traitée il y a moins de
    `days` jours, sinon None."""
    if not url:
        return None
    entry = _load_url_index().get(url)
    if not entry:
        return None
    seen = datetime.date.fromisoformat(entry["date"])
    if (datetime.date.today() - seen).days > days:
        return None
    return entry


def _remember_url(url: str, path: Path):
    if not url:
        return
    idx = _load_url_index()
    idx[url] = {"date": datetime.date.today().isoformat(), "path": str(path.relative_to(VAULT))}
    if len(idx) > URL_INDEX_MAX:
        idx = dict(sorted(idx.items(), key=lambda kv: kv[1]["date"])[-URL_INDEX_MAX:])
    URL_INDEX.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")


def write_note(subfolder: str, title: str, body: str, meta: dict = None) -> Path:
    folder = VAULT / subfolder
    folder.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    fm = {"date": today, "source": "second-brain", **(meta or {})}
    front = "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n\n"
    path = folder / f"{today} {slug(title)}.md"
    i = 1
    while path.exists():
        i += 1
        path = folder / f"{today} {slug(title)} {i}.md"
    path.write_text(front + f"# {title}\n\n{body.strip()}\n", encoding="utf-8")
    _remember_url((meta or {}).get("url"), path)
    return path


def read_note(rel_path: str) -> str:
    p = VAULT / rel_path
    return p.read_text(encoding="utf-8") if p.exists() else ""


def recent_notes(subfolder: str, n: int = 3, max_chars: int = 3000) -> str:
    folder = VAULT / subfolder
    if not folder.exists():
        return ""
    files = sorted(folder.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)[:n]
    return "\n\n---\n\n".join(f.read_text(encoding="utf-8")[:max_chars] for f in files)


def notes_since(subfolder: str, days: int = 7, max_chars: int = 2000, max_files: int = 40) -> str:
    """Concatène les notes modifiées dans les `days` derniers jours (pour la
    synthèse hebdomadaire)."""
    folder = VAULT / subfolder
    if not folder.exists():
        return ""
    cutoff = datetime.datetime.now().timestamp() - days * 86400
    files = sorted(
        (f for f in folder.glob("*.md") if f.stat().st_mtime >= cutoff),
        key=lambda f: f.stat().st_mtime,
    )[:max_files]
    return "\n\n---\n\n".join(f.read_text(encoding="utf-8")[:max_chars] for f in files)


def _frontmatter_tags(text: str) -> list[str]:
    if not text.startswith("---\n"):
        return []
    end = text.find("\n---", 4)
    if end == -1:
        return []
    try:
        fm = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        return []
    return [str(t).lower() for t in (fm.get("tags") or [])]


def notes_matching(subfolder: str, keywords: list[str], days: int = 14,
                   max_files: int = 5, max_chars: int = 2000) -> str:
    """Notes récentes triées par recoupement de tags avec `keywords` (ex. mots
    du nom/de la description d'un projet). Sans recoupement, le score reste à
    0 partout et le tri retombe naturellement sur la plus récente d'abord —
    jamais de résultat vide tant qu'il existe des notes dans la fenêtre."""
    folder = VAULT / subfolder
    if not folder.exists():
        return ""
    cutoff = datetime.datetime.now().timestamp() - days * 86400
    kw = {k for k in keywords if len(k) > 3}
    scored = []
    for f in folder.glob("*.md"):
        mtime = f.stat().st_mtime
        if mtime < cutoff:
            continue
        text = f.read_text(encoding="utf-8")
        tags = _frontmatter_tags(text)
        score = sum(1 for t in tags for k in kw if k in t or t in k)
        scored.append((score, mtime, text))
    if not scored:
        return ""
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return "\n\n---\n\n".join(text[:max_chars] for _, _, text in scored[:max_files])


def search_notes(query: str, max_results: int = 20) -> list[dict]:
    """Recherche plein texte (titre + corps) dans les dossiers exposés à
    l'UI. Retourne les résultats les plus récents d'abord."""
    query = query.strip().lower()
    if not query:
        return []
    results = []
    for sub in SEARCHABLE:
        folder = VAULT / sub
        if not folder.exists():
            continue
        for f in folder.rglob("*.md"):
            text = f.read_text(encoding="utf-8", errors="ignore")
            low = text.lower()
            idx = low.find(query)
            if idx == -1 and query not in f.stem.lower():
                continue
            start = max(0, idx - 60)
            excerpt = text[start:idx + 120] if idx != -1 else text[:150]
            title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
            results.append({
                "path": str(f.relative_to(VAULT)),
                "title": title_match.group(1).strip() if title_match else f.stem,
                "date": datetime.date.fromtimestamp(f.stat().st_mtime).isoformat(),
                "excerpt": " ".join(excerpt.split()),
                "mtime": f.stat().st_mtime,
            })
    results.sort(key=lambda r: r["mtime"], reverse=True)
    for r in results:
        del r["mtime"]
    return results[:max_results]


def get_note_safe(rel_path: str) -> str | None:
    """Lit une note en s'assurant que le chemin résolu reste dans un des
    dossiers autorisés du vault (empêche un ../.. de sortir du vault ou
    d'exposer des fichiers non prévus pour l'UI, ex. Projets/*.md)."""
    vault_resolved = VAULT.resolve()
    try:
        p = (VAULT / rel_path).resolve()
        rel = p.relative_to(vault_resolved)
    except (OSError, ValueError):
        return None
    if p.suffix != ".md" or not p.is_file():
        return None
    if not any(str(rel).startswith(sub) for sub in SEARCHABLE):
        return None
    return p.read_text(encoding="utf-8")
