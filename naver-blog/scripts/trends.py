"""Daily Naver DataLab trend scores for every model in config/models.json.

DataLab ratios are only comparable inside one call, so every call carries
the anchor group ("BMW") and each model is divided by the anchor on the
same day. That makes scores from different batches comparable.

Results are cached per day in data/trends/YYYY-MM-DD.json, so the hourly
run calls DataLab about 7 times a day, not 7 times an hour.

Usage: python3 trends.py [--refresh]
"""

import json
import sys
from datetime import timedelta

from common import DATA, kst_now, load_config
from naver_api import NaverApiError, datalab_search

CACHE_DIR = DATA / "trends"
WINDOW_DAYS = 28
RECENT_DAYS = 7
BATCH = 4  # + anchor = 5 groups, the DataLab maximum


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def fetch_scores(config):
    today = kst_now().date()
    end = today - timedelta(days=1)
    start = end - timedelta(days=WINDOW_DAYS - 1)
    days = [(start + timedelta(days=i)).isoformat() for i in range(WINDOW_DAYS)]

    anchor_name = "__anchor__"
    anchor = (anchor_name, config["anchor"]["keywords"])
    models = config["owned"] + config["interested"]
    scores = {}
    for i in range(0, len(models), BATCH):
        batch = models[i:i + BATCH]
        groups = [anchor] + [(m["id"], m["keywords"]) for m in batch]
        result = datalab_search(start.isoformat(), end.isoformat(), groups)
        anchor_series = result.get(anchor_name, {})
        for m in batch:
            series = result.get(m["id"], {})
            normalized = []
            for d in days:
                a = anchor_series.get(d, 0.0)
                normalized.append(series.get(d, 0.0) / a if a > 0 else 0.0)
            recent = _mean(normalized[-RECENT_DAYS:])
            base = _mean(normalized[:-RECENT_DAYS])
            scores[m["id"]] = {
                "level": round(recent, 5),
                "momentum": round((recent + 1e-4) / (base + 1e-4), 3),
            }
    return {
        "date": today.isoformat(),
        "period": [start.isoformat(), end.isoformat()],
        "scores": scores,
    }


def load_or_fetch(config=None, refresh=False):
    """Return today's trend scores, or None if DataLab is unreachable."""
    config = config or load_config()
    path = CACHE_DIR / f"{kst_now().date().isoformat()}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        data = fetch_scores(config)
    except NaverApiError as e:
        print(f"[trends] DataLab unavailable, picking without trends: {e}", file=sys.stderr)
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


if __name__ == "__main__":
    data = load_or_fetch(refresh="--refresh" in sys.argv)
    if data is None:
        sys.exit(1)
    ranked = sorted(data["scores"].items(), key=lambda kv: kv[1]["level"], reverse=True)
    for model_id, s in ranked:
        print(f"{model_id:16s} level={s['level']:.4f} momentum={s['momentum']:.2f}")
