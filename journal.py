"""Journal partagé (jobs.py + app.py) : append-only log des traitements et tâches planifiées."""
import datetime
import json
from pathlib import Path

BASE = Path(__file__).parent
LOG = BASE / "journal.jsonl"
MAX_ENTRIES = 1000  # au-delà, on ne garde que les plus récentes (fichier non borné sinon)


def now() -> str:
    return datetime.datetime.now().strftime("%d/%m %H:%M")


def log(status: str, label: str, detail: str = "", url: str = "", tags: list | None = None):
    entry = {"date": now(), "status": status, "label": label, "detail": detail[:200]}
    if url:
        entry["url"] = url
    if tags:
        entry["tags"] = tags
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _rotate()


def _rotate():
    if not LOG.exists():
        return
    lines = LOG.read_text(encoding="utf-8").splitlines()
    if len(lines) > MAX_ENTRIES:
        LOG.write_text("\n".join(lines[-MAX_ENTRIES:]) + "\n", encoding="utf-8")


def last_logs(n: int = 20) -> list[dict]:
    if not LOG.exists():
        return []
    lines = LOG.read_text(encoding="utf-8").strip().splitlines()[-n:]
    return [json.loads(l) for l in reversed(lines)]
