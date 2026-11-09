import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "models.json"
DATA = ROOT / "data"
HISTORY = DATA / "history.jsonl"
CURRENT_TOPIC = DATA / "current_topic.json"
NOTES = ROOT / "notes"
POSTS = ROOT / "posts"

KST = timezone(timedelta(hours=9))


def kst_now():
    return datetime.now(KST)


def load_config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def load_history():
    if not HISTORY.exists():
        return []
    rows = []
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def all_models(config):
    return [dict(m, category="owned") for m in config["owned"]] + \
           [dict(m, category="interested") for m in config["interested"]]
