"""Append the finished post to data/history.jsonl.

Run after check_post.py passes. Reads data/current_topic.json.

Usage: python3 record.py "<post title>"
"""

import json
import sys

from check_post import check
from common import CURRENT_TOPIC, HISTORY, ROOT


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else ""
    topic = json.loads(CURRENT_TOPIC.read_text(encoding="utf-8"))
    chars, problems = check(ROOT / topic["post_path"])
    if problems:
        print("not recorded: " + "; ".join(problems))
        sys.exit(1)
    row = {
        "created_kst": topic["created_kst"],
        "model_id": topic["model"]["id"],
        "category": topic["category"],
        "angle": topic["angle"],
        "title": title,
        "post": topic["post_path"],
        "chars": chars,
        "trend_source": topic["trend_source"],
    }
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"recorded: {row['post']} ({chars}자)")


if __name__ == "__main__":
    main()
