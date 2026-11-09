"""Check a post file before it is recorded and sent.

Passes when the file has a title line, the [서론] [본론] [결론] sections in
that order, and at least MIN_CHARS characters (spaces and line breaks not
counted) from [서론] to the end of [결론]. Photo slots [사진: ...] and
owner-check markers [확인: ...] are not counted, so the written text alone
has to reach MIN_CHARS.

Usage: python3 check_post.py posts/2026-11-09/1400_f87-m2c.txt
"""

import re
import sys
from pathlib import Path

MIN_CHARS = 2000
SECTIONS = ["[서론]", "[본론]", "[결론]"]
MARKER = re.compile(r"\[(?:사진|확인):[^\]]*\]")


def body_chars(text):
    start = text.index(SECTIONS[0])
    end_match = re.search(r"^태그:", text[start:], flags=re.M)
    body = text[start:start + end_match.start()] if end_match else text[start:]
    for label in SECTIONS:
        body = body.replace(label, "")
    body = MARKER.sub("", body)
    return len(re.sub(r"\s", "", body))


def check(path):
    text = Path(path).read_text(encoding="utf-8")
    problems = []
    if not re.search(r"^제목:\s*\S", text, flags=re.M):
        problems.append("'제목:' 줄이 없음")
    positions = [text.find(s) for s in SECTIONS]
    missing = [s for s, p in zip(SECTIONS, positions) if p < 0]
    if missing:
        problems.append("섹션 없음: " + ", ".join(missing))
    elif positions != sorted(positions):
        problems.append("섹션 순서가 서론 → 본론 → 결론이 아님")
    chars = body_chars(text) if not missing else 0
    if chars < MIN_CHARS:
        problems.append(f"본문 {chars}자(공백 제외) — {MIN_CHARS}자 미만")
    return chars, problems


if __name__ == "__main__":
    chars, problems = check(sys.argv[1])
    if problems:
        print("FAIL: " + "; ".join(problems))
        sys.exit(1)
    print(f"OK: 본문 {chars}자(공백 제외)")
