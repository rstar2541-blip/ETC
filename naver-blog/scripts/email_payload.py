"""Build the Gmail message for the current post (data/current_topic.json).

Prints JSON with subject, body, filename and the base64 attachment, ready
for the Gmail send_message tool.

Usage: python3 email_payload.py
"""

import base64
import json
import re

from common import CURRENT_TOPIC, ROOT

REPO_URL = "https://github.com/rstar2541-blip/ETC/blob/claude/upbeat-franklin-0nprlp/naver-blog/"


def main():
    topic = json.loads(CURRENT_TOPIC.read_text(encoding="utf-8"))
    path = ROOT / topic["post_path"]
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    title = re.search(r"^제목:\s*(.+)$", text, flags=re.M).group(1).strip()
    footer = (
        "\n\n----\n"
        f"모델: {topic['model']['label']} / 주제: {topic['angle']}\n"
        f"트렌드 출처: {topic['trend_source']}\n"
        f"원본 파일: {REPO_URL}{topic['post_path']}\n"
    )
    print(json.dumps({
        "subject": f"[BMW 초안] {title}",
        "body": text.rstrip() + footer,
        "filename": path.name,
        "attachment_b64": base64.b64encode(raw).decode("ascii"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
