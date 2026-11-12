"""Download the blog post's photos and cut them into assets/img/ exactly as the video uses them.

The photos belong to the author of 꼬몽맘의 블로그 and are not committed to this public repository.
Run from the project root:  python3 scripts/fetch_images.py
Needs network access to blog.naver.com and postfiles.pstatic.net, and Pillow.
"""
import html
import io
import os
import re
import sys
import urllib.request

from PIL import Image

POST = "https://blog.naver.com/PostView.naver?blogId=samil5240&logNo=222492558757&redirect=Dlog&widgetTypeCall=true&directAccess=false"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "img")
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://blog.naver.com/"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read()


def body_images(page):
    body = page[page.find("se-main-container"):]
    urls = []
    for m in re.finditer(r'data-lazy-src="([^"]+)"|<img[^>]+src="(https://postfiles[^"]+)"', body):
        u = html.unescape(m.group(1) or m.group(2))
        if u not in urls:
            urls.append(u)
    return urls


def main():
    os.makedirs(OUT, exist_ok=True)
    urls = body_images(get(POST).decode("utf-8", "replace"))
    if len(urls) < 10:
        sys.exit(f"expected 10 photos in the post, found {len(urls)}")
    raw = {i + 1: get(u) for i, u in enumerate(urls[:10])}
    img = {n: Image.open(io.BytesIO(data)) for n, data in raw.items()}
    rgb = lambda n: img[n].convert("RGB")

    img[1].save(os.path.join(OUT, "thumb.png"))                      # the post's thumbnail
    with open(os.path.join(OUT, "paper2.jpg"), "wb") as f:         # 수험자 유의사항 + handwritten values, as posted
        f.write(raw[2])
    rgb(3).save(os.path.join(OUT, "paper.jpg"), quality=92)         # 문제지 header (안동대학교)
    img[4].crop((0, 0, 966, 320)).save(os.path.join(OUT, "groupA.png"))  # A조 seating sketch, top row only
    rgb(5).save(os.path.join(OUT, "scope.jpg"), quality=92)         # 현미경 조작 순서
    rgb(6).save(os.path.join(OUT, "lab.jpg"), quality=92)           # lab bench
    img[7].save(os.path.join(OUT, "scale.png"))                     # scale drawing
    # Q-Net score screen: keep exam rows, pass and score table; drop 수험번호/이름 rows and the greeting line
    s = rgb(9)
    a = s.crop((48, 180, 852, 660))
    b = s.crop((48, 865, 852, 1442))
    score = Image.new("RGB", (a.width, a.height + b.height), "white")
    score.paste(a, (0, 0))
    score.paste(b, (0, a.height))
    score.save(os.path.join(OUT, "score.jpg"), quality=92)
    print("wrote", sorted(os.listdir(OUT)))


if __name__ == "__main__":
    main()
