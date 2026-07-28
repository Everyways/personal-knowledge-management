"""Écriture des notes markdown dans le vault Obsidian."""
import datetime
import re
from pathlib import Path

import yaml

CONFIG = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text(encoding="utf-8"))
VAULT = Path(CONFIG["vault_path"]).expanduser()


def slug(s: str) -> str:
    s = re.sub(r"[^\w\- ]+", "", s, flags=re.UNICODE).strip()
    return re.sub(r"\s+", "-", s)[:60].strip("-") or "note"


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
