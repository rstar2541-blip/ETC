"""Pick the next post: which model, which angle, plus trend and competition data.

Writes data/current_topic.json and prints it.

Rules:
- keep the share of owned-model posts near config "owned_share";
- skip models used in the last "cooldown_posts" posts;
- with DataLab data, prefer models whose search interest is high or rising;
  without it, prefer the model that has gone longest without a post;
- give each model an angle it has not had yet (seasonal angles included).
"""

import json
import random
import sys

from common import CURRENT_TOPIC, NOTES, all_models, kst_now, load_config, load_history
from naver_api import NaverApiError, blog_search
import trends


def choose_category(config, history):
    last = history[-10:]
    if not last:
        return "owned"
    owned_share = sum(1 for h in last if h["category"] == "owned") / len(last)
    return "owned" if owned_share < config["owned_share"] else "interested"


def last_used_index(history):
    idx = {}
    for i, h in enumerate(history):
        idx[h["model_id"]] = i
    return idx


def trend_score(candidates, scores):
    """Search level relative to the best candidate, plus a bonus for rising interest."""
    top_level = max((scores.get(m["id"], {}).get("level", 0) for m in candidates), default=0) or 1
    out = {}
    for m in candidates:
        s = scores.get(m["id"], {})
        out[m["id"]] = s.get("level", 0) / top_level + 0.5 * min(s.get("momentum", 1), 3.0) / 3.0
    return out


def choose_model(config, history, category, trend_data, rng):
    models = [m for m in all_models(config) if m["category"] == category]
    recent = {h["model_id"] for h in history[-config["cooldown_posts"]:]}
    candidates = [m for m in models if m["id"] not in recent] or models

    if trend_data:
        score = trend_score(candidates, trend_data["scores"])
        top = sorted(candidates, key=lambda m: score[m["id"]], reverse=True)[:3]
        return rng.choices(top, weights=[3, 2, 1][:len(top)])[0]

    used = last_used_index(history)
    never = [m for m in candidates if m["id"] not in used]
    if never:
        return rng.choice(never)
    return min(candidates, key=lambda m: used[m["id"]])


def choose_angle(config, history, model, month):
    pool = list(config["angles"][model["category"]])
    if model["category"] == "owned":
        pool = config["angles"]["seasonal"].get(str(month), []) + pool
    used = [h["angle"] for h in history if h["model_id"] == model["id"]]
    for angle in pool:
        if angle not in used:
            return angle
    # every angle used once: take the one used longest ago
    last_seen = {a: max(i for i, u in enumerate(used) if u == a) for a in pool}
    return min(pool, key=lambda a: last_seen[a])


def competition(keyword):
    try:
        res = blog_search(keyword, display=10, sort="sim")
    except NaverApiError as e:
        print(f"[pick] blog search unavailable: {e}", file=sys.stderr)
        return None
    return {"query": keyword, "total": res["total"], "top_titles": [i["title"] for i in res["items"]]}


def main():
    config = load_config()
    history = load_history()
    now = kst_now()
    rng = random.Random(now.strftime("%Y%m%d%H"))

    trend_data = trends.load_or_fetch(config)
    category = choose_category(config, history)
    model = choose_model(config, history, category, trend_data, rng)
    angle = choose_angle(config, history, model, now.month)

    notes_file = NOTES / f"{model['id']}.md"
    topic = {
        "created_kst": now.isoformat(timespec="minutes"),
        "category": category,
        "model": {"id": model["id"], "label": model["label"], "keywords": model["keywords"]},
        "angle": angle,
        "trend": trend_data["scores"].get(model["id"]) if trend_data else None,
        "trend_source": "naver-datalab" if trend_data else "none (rotation only)",
        "competition": competition(model["keywords"][0]),
        "notes_file": str(notes_file.relative_to(NOTES.parent)) if notes_file.exists() else None,
        "post_path": f"posts/{now:%Y-%m-%d}/{now:%H%M}_{model['id']}.txt",
    }
    CURRENT_TOPIC.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_TOPIC.write_text(json.dumps(topic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(topic, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
