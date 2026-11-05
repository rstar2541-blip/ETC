#!/usr/bin/env python3
"""
photo_cull.py — 사진 폴더에서 '불완전한 컷'을 골라내는 도구

검사 항목
  · 사진 전체 흐림/흔들림            (Laplacian 분산, 화면에서 가장 선명한 영역 기준)
  · 인물 초점 나감 / 배경에 초점     (눈·눈썹 영역 선명도)
  · 눈 감음 / 반쯤 감김 / 한쪽 감김  (MediaPipe 얼굴 블렌드셰이프)
  · 어색한 표정                      (말하는 중, 찡그림, 입 오므림)
  · 얼굴 잘림 / 역광 / 노출 문제
  · 연사·비슷한 사진 묶기            (묶음에서 가장 좋은 컷만 남김)

사용법
  python photo_cull.py scan  <사진폴더> [-r]      # 분석 + 리포트(HTML/CSV) 생성, 파일은 그대로
  python photo_cull.py apply <사진폴더>           # 리포트(CSV) 판정대로 하위 폴더로 이동
  python photo_cull.py undo  <사진폴더>           # 이동 되돌리기

어떤 사진도 삭제하지 않습니다. 이동만 하며, undo 로 언제든 원위치됩니다.
"""
from __future__ import annotations

import os

os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import argparse
import concurrent.futures as cf
import csv
import html
import json
import re
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

try:
    import pillow_heif  # 아이폰 HEIC 지원 (선택)

    pillow_heif.register_heif_opener()
    HEIF_OK = True
except ImportError:
    HEIF_OK = False

Image.MAX_IMAGE_PIXELS = 300_000_000

# 콘솔 인코딩이 한글을 못 쓰는 환경(영문 Windows, 출력 리다이렉트 등)에서도 죽지 않게
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

HERE = Path(__file__).resolve().parent
# PyInstaller로 묶은 프로그램이면 함께 들어 있는 models 폴더를 사용
MODEL_DIR = Path(getattr(sys, "_MEIPASS", HERE)) / "models"
YUNET_FILE = "face_detection_yunet_2023mar.onnx"
LANDMARKER_FILE = "face_landmarker.task"
MODEL_SOURCES = {
    YUNET_FILE: [
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    ],
    LANDMARKER_FILE: [
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task",
    ],
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
if HEIF_OK:
    IMAGE_EXTS |= {".heic", ".heif"}

OUT_DIR_NAME = "_사진정리"
VERDICT_KO = {"reject": "불량", "review": "확인필요", "burst": "연사중복", "screenshot": "스크린샷", "keep": "정상"}
KO_VERDICT = {v: k for k, v in VERDICT_KO.items()}
MOVE_FOLDERS = {"reject": "1_불량", "review": "2_확인필요", "burst": "3_연사중복", "screenshot": "4_스크린샷"}
RULES_VERSION = 2  # 판정 규칙이 바뀌면 올림 → 이전 결과를 불러올 때 다시 판정

WORK_LONG = 2000     # 분석용 해상도(긴 변). 카메라마다 다른 해상도를 같은 기준으로 맞춤
DETECT_LONG = 1280   # 얼굴 검출용 해상도
NORM_IOD = 100       # 눈 사이 거리를 이 값(px) 이하로 맞춰서 선명도 측정
MIN_FACE_PX = 24     # 이보다 작은 얼굴은 무시
FOCUS_FACE_PX = 36   # 이보다 작은 얼굴은 초점 판정 생략 (사진 전체 선명도로 판단)
DETAIL_FACE_PX = 48  # 이보다 작은 얼굴은 표정/눈 판정 생략 (신뢰도 낮음)


# ----------------------------------------------------------------------------
# 판정 기준
# ----------------------------------------------------------------------------
@dataclass
class Thresholds:
    scene_blur: float = 25.0     # (얼굴 없는 사진) 화면 최대 선명도가 이보다 낮으면 '전체 흐림'
    scene_soft: float = 60.0
    scene_bg: float = 240.0      # 얼굴은 흐린데 화면이 이만큼 선명하면 '배경에 초점'
    face_blur: float = 0.035     # 눈 영역 선명도(정규화)가 이보다 낮으면 '초점 나감'
    face_soft: float = 0.075
    eye_closed: float = 0.70     # 블렌드셰이프 eyeBlink 가 이 이상이면 감음
    ear_closed: float = 0.14     # 눈 세로/가로 비율이 이보다 작으면 감음
    ear_half: float = 0.18       # 이보다 작으면 반쯤 감김
    burst_hash: int = 12         # 연사 묶음: dHash 해밍거리
    burst_gap: float = 10.0      # 연사 묶음: 촬영 시각 차이(초)

    @classmethod
    def for_sensitivity(cls, level: str) -> "Thresholds":
        t = cls()
        k = {"low": 0.6, "normal": 1.0, "high": 1.5}[level]
        t.scene_blur *= k
        t.scene_soft *= k
        t.face_blur *= k
        t.face_soft *= k
        d = {"low": -1, "normal": 0, "high": 1}[level]
        t.eye_closed -= 0.08 * d
        t.ear_closed += 0.02 * d
        t.ear_half += 0.02 * d
        return t


ISSUES = {
    # code: (기본 심각도, 감점, 문구)
    "scene_blur": ("reject", 50, "사진 전체가 흐리거나 흔들림"),
    "scene_soft": ("review", 15, "전체적으로 약간 흐림"),
    "face_blur": ("reject", 45, "인물 초점이 나감"),
    "face_bg": ("reject", 45, "초점이 인물이 아닌 배경에 맞음"),
    "face_soft": ("review", 15, "인물 초점이 약간 부드러움"),
    "main_soft": ("review", 15, "주인공(가장 큰 얼굴)에 초점이 안 맞음"),
    "eyes_closed": ("reject", 40, "눈 감음"),
    "eyes_down": ("review", 20, "눈을 감았거나 아래를 봄"),
    "eyes_laugh": ("review", 12, "웃느라 눈이 감김"),
    "eyes_half": ("review", 15, "눈이 반쯤 감김"),
    "wink": ("review", 12, "한쪽 눈 감김"),
    "talking": ("review", 10, "입이 어중간하게 벌어짐(말하는 중)"),
    "pucker": ("review", 8, "입을 오므린 어색한 입모양"),
    "grimace": ("review", 12, "찡그리거나 일그러진 표정"),
    "face_cut": ("review", 10, "얼굴이 프레임 밖으로 잘림"),
    "face_dark": ("review", 10, "얼굴이 어두움(역광)"),
    "face_bright": ("review", 10, "얼굴이 하얗게 날아감"),
    "too_dark": ("reject", 40, "사진이 너무 어두움"),
    "overexposed": ("review", 15, "과다 노출(하얗게 날아감)"),
    "screenshot": ("screenshot", 0, "스크린샷"),
}


# ----------------------------------------------------------------------------
# 결과 구조
# ----------------------------------------------------------------------------
@dataclass
class FaceInfo:
    box: tuple
    iod: float
    focus: float = 0.0
    blink: tuple | None = None
    ear: tuple | None = None
    look_down: float = 0.0
    smile: float = 0.0
    issues: list = field(default_factory=list)


@dataclass
class PhotoResult:
    path: str
    ok: bool = True
    error: str = ""
    taken: str = ""
    size: tuple = (0, 0)
    work_size: tuple = (0, 0)   # 분석용으로 줄인 크기 (faces 의 box 좌표 기준)
    scene_sharp: float = 0.0
    face_focus: float | None = None
    n_faces: int = 0
    n_main: int = 0
    issues: list = field(default_factory=list)  # [code, severity, text]
    verdict: str = "keep"
    score: float = 100.0
    dhash: str = ""
    burst_group: int = 0
    burst_best: str = ""
    thumb: str = ""
    faces: list = field(default_factory=list)  # 얼굴별 측정값 (results.json 디버깅용)
    manual: bool = False  # 사용자가 판정을 직접 바꿨는지
    v: int = 1            # 이 결과를 만든 판정 규칙 버전 (RULES_VERSION)


# ----------------------------------------------------------------------------
# 모델 준비
# ----------------------------------------------------------------------------
class ModelError(RuntimeError):
    pass


def ensure_models(log=print) -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name, urls in MODEL_SOURCES.items():
        dest = MODEL_DIR / name
        if dest.exists() and dest.stat().st_size > 100_000:
            continue
        log(f"모델 다운로드 중: {name}")
        last_err = None
        for url in urls:
            try:
                tmp = dest.with_suffix(dest.suffix + ".part")
                with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                    shutil.copyfileobj(r, f)
                if tmp.stat().st_size < 100_000:  # Git LFS 포인터 등 잘못된 파일
                    tmp.unlink()
                    raise IOError("파일이 너무 작음")
                tmp.replace(dest)
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
        else:
            raise ModelError(f"모델 다운로드 실패: {name} ({last_err})\n"
                             f"직접 받아서 {dest} 에 넣어 주세요: {urls[0]}")


# ----------------------------------------------------------------------------
# 이미지 로드
# ----------------------------------------------------------------------------
def load_image(path: Path):
    """(BGR 분석용 이미지, 촬영시각, 시각 출처, 원본 크기, {camera, comment}) 를 돌려준다"""
    meta = {"camera": False, "comment": ""}
    with Image.open(path) as im:
        taken = None
        try:
            exif = im.getexif()
            sub = exif.get_ifd(0x8769)
            raw = sub.get(36867) or exif.get(306)
            if raw:
                taken = datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S")
            meta["camera"] = bool(exif.get(271) or exif.get(272))  # 제조사/모델
            c = sub.get(37510) or exif.get(270) or ""                 # UserComment / ImageDescription
            meta["comment"] = c.decode("latin-1", "ignore") if isinstance(c, bytes) else str(c)
        except Exception:  # noqa: BLE001
            pass
        orig_size = im.size
        if im.format == "JPEG":  # 큰 JPEG는 축소 디코딩으로 빠르게
            s = WORK_LONG / max(im.size)
            if s < 1:
                im.draft("RGB", (int(im.size[0] * s), int(im.size[1] * s)))
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        rgb = np.asarray(im)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    s = WORK_LONG / max(h, w)
    if s < 1:
        bgr = cv2.resize(bgr, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    if taken is None:
        taken = datetime.fromtimestamp(path.stat().st_mtime)
        taken_src = "file"
    else:
        taken_src = "exif"
    return bgr, taken, taken_src, orig_size, meta


SHOT_NAME = re.compile(r"screen[\s_-]*shot|screen[\s_-]*capture|스크린\s*샷|캡처|캡쳐", re.IGNORECASE)


def looks_like_screenshot(rel: str, size, camera: bool | None = None, comment: str = "") -> bool:
    """파일/폴더 이름(Screenshot, 스크린샷, 캡처), 아이폰 스크린샷 표시, 또는
    카메라 정보 없는 세로로 긴 PNG(휴대폰 화면 비율)이면 스크린샷으로 본다."""
    p = Path(rel)
    if any(SHOT_NAME.search(part) for part in p.parts):
        return True
    if "screenshot" in (comment or "").lower():
        return True
    try:
        w, h = size
    except (TypeError, ValueError):
        return False
    # 세로로 긴(휴대폰 화면 비율) PNG 만. 가로로 긴 PNG 는 단체사진·파노라마일 수 있어서 제외
    if p.suffix.lower() == ".png" and w and h and h / w >= 1.95 and camera is not True:
        return True
    return False


# 눈 윤곽 6점 (MediaPipe Face Mesh 인덱스): 눈꼬리, 윗꺼풀 2, 눈꼬리, 아랫꺼풀 2
EAR_IDX_A = (33, 160, 158, 133, 153, 144)
EAR_IDX_B = (362, 385, 387, 263, 373, 380)


def eye_aspect(p: np.ndarray, idx) -> float:
    """눈 세로/가로 비율(EAR). 뜬 눈 ~0.3, 감은 눈 ~0.1 이하"""
    p1, p2, p3, p4, p5, p6 = (p[i] for i in idx)
    return float((np.linalg.norm(p2 - p6) + np.linalg.norm(p3 - p5)) / (2 * np.linalg.norm(p1 - p4) + 1e-6))


def dhash(gray: np.ndarray) -> int:
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def sharpness(gray: np.ndarray) -> tuple[float, float]:
    """(Laplacian 분산, 대비로 정규화한 값)"""
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    lv = float(lap.var())
    gv = float(gray.astype(np.float64).var())
    return lv, lv / (gv + 25.0)


# ----------------------------------------------------------------------------
# 분석기
# ----------------------------------------------------------------------------
class Analyzer:
    def __init__(self, th: Thresholds, thumbs_dir: Path | None):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        self.mp = mp
        self.th = th
        self.thumbs_dir = thumbs_dir
        # 모델은 파일 경로 대신 메모리로 넘김: Windows 의 OpenCV 는 한글 등 비ASCII 경로를 못 연다
        yunet = (MODEL_DIR / YUNET_FILE).read_bytes()
        try:
            self.det = cv2.FaceDetectorYN.create("onnx", np.frombuffer(yunet, np.uint8), np.array([], np.uint8),
                                                 (320, 320), 0.7, 0.3, 5000)
        except (TypeError, cv2.error):  # 버퍼를 못 받는 구버전 OpenCV
            self.det = cv2.FaceDetectorYN.create(str(MODEL_DIR / YUNET_FILE), "", (320, 320), 0.7, 0.3, 5000)
        opts = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_buffer=(MODEL_DIR / LANDMARKER_FILE).read_bytes()),
            output_face_blendshapes=True,
            num_faces=1,
            min_face_detection_confidence=0.3,
        )
        self.lm = vision.FaceLandmarker.create_from_options(opts)

    def close(self):
        try:
            self.lm.close()
        except Exception:  # noqa: BLE001
            pass

    # -- 얼굴 검출 --------------------------------------------------------
    def detect_faces(self, bgr: np.ndarray) -> list[tuple]:
        h, w = bgr.shape[:2]
        s = min(1.0, DETECT_LONG / max(h, w))
        small = cv2.resize(bgr, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else bgr
        self.det.setInputSize((small.shape[1], small.shape[0]))
        _, out = self.det.detect(small)
        faces = []
        if out is not None:
            for row in out:
                box = tuple(float(v) / s for v in row[:4])
                pts = (row[4:14].reshape(5, 2) / s).astype(np.float64)
                faces.append((box, pts, float(row[14])))
        faces.sort(key=lambda f: -f[0][2])
        return faces

    def landmark(self, bgr: np.ndarray, box) -> tuple[dict, tuple] | None:
        h, w = bgr.shape[:2]
        x, y, fw, fh = box
        side = max(fw, fh) * 1.9
        cx, cy = x + fw / 2, y + fh / 2
        x0, y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
        x1, y1 = int(min(w, cx + side / 2)), int(min(h, cy + side / 2))
        if x1 - x0 < 16 or y1 - y0 < 16:
            return None
        crop = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
        if max(crop.shape[:2]) < 256:
            k = 256 / max(crop.shape[:2])
            crop = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=np.ascontiguousarray(crop))
        res = self.lm.detect(img)
        if not res.face_blendshapes:
            return None
        ch, cw = crop.shape[:2]
        pts = np.array([[p.x * cw, p.y * ch] for p in res.face_landmarks[0]])
        ears = (eye_aspect(pts, EAR_IDX_A), eye_aspect(pts, EAR_IDX_B))
        return {c.category_name: float(c.score) for c in res.face_blendshapes[0]}, ears

    # -- 사진 1장 분석 ----------------------------------------------------
    def analyze(self, root: Path, path: Path, index: int) -> PhotoResult:
        rel = path.relative_to(root).as_posix()
        r = PhotoResult(path=rel)
        try:
            bgr, taken, taken_src, orig, meta = load_image(path)
        except Exception as e:  # noqa: BLE001
            r.ok, r.error, r.verdict = False, f"열 수 없음: {e}", "review"
            r.issues.append(["open_error", "review", "파일을 열 수 없음"])
            return r
        r.taken = taken.isoformat(timespec="seconds") + ("" if taken_src == "exif" else "~")
        r.size = orig
        h, w = bgr.shape[:2]
        r.work_size = (w, h)
        r.v = RULES_VERSION
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        r.dhash = f"{dhash(gray):016x}"

        # 스크린샷은 사진 품질 검사(흐림·노출·표정)를 하지 않고 따로 분류
        if looks_like_screenshot(rel, orig, meta["camera"], meta["comment"]):
            mark_screenshot(r)
            if self.thumbs_dir is not None:
                r.thumb = self.save_thumb(bgr, [], index)
            return r

        # 1) 화면 전체 선명도: 격자 타일 중 상위 10% 선명도
        lap = cv2.Laplacian(gray, cv2.CV_64F)
        gy, gx = 6, 6
        tv = []
        for i in range(gy):
            for j in range(gx):
                t = lap[i * h // gy:(i + 1) * h // gy, j * w // gx:(j + 1) * w // gx]
                tv.append(t.var())
        r.scene_sharp = float(np.percentile(tv, 90))

        # 2) 노출
        mean = float(gray.mean())
        p25, p99 = (float(v) for v in np.percentile(gray, [25, 99]))
        clip_hi = float((gray >= 250).mean())

        # 3) 얼굴
        faces_raw = self.detect_faces(bgr)
        r.n_faces = len(faces_raw)
        main = []
        if faces_raw:
            max_w = faces_raw[0][0][2]
            main = [f for f in faces_raw if f[0][2] >= max(0.45 * max_w, MIN_FACE_PX)][:12]
        r.n_main = len(main)
        infos: list[FaceInfo] = []
        for box, pts, _score in main:
            x, y, fw, fh = box
            eye_r, eye_l = pts[0], pts[1]
            iod = float(np.linalg.norm(eye_r - eye_l))
            fi = FaceInfo(box=tuple(round(v) for v in box), iod=iod)
            # 눈+눈썹 영역 선명도 (눈 사이 거리 기준으로 크기 정규화)
            if fw >= FOCUS_FACE_PX and iod > 4:
                c = (eye_r + eye_l) / 2 + np.array([0, -0.1 * iod])
                bw, bh = 2.2 * iod, 1.1 * iod
                bx0, by0 = int(max(0, c[0] - bw / 2)), int(max(0, c[1] - bh / 2))
                bx1, by1 = int(min(w, c[0] + bw / 2)), int(min(h, c[1] + bh / 2))
                band = gray[by0:by1, bx0:bx1]
                if band.size > 64:
                    k = min(1.0, NORM_IOD / iod)
                    if k < 1:
                        band = cv2.resize(band, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
                    fi.focus = sharpness(band)[1]
            # 얼굴 노출: 이마~눈 영역만 사용 (마스크·수염·선글라스 영향 줄임)
            ix0, iy0 = int(max(0, x + fw * 0.2)), int(max(0, y + fh * 0.1))
            ix1, iy1 = int(min(w, x + fw * 0.8)), int(min(h, y + fh * 0.5))
            face_px = gray[iy0:iy1, ix0:ix1]
            if face_px.size and fw >= DETAIL_FACE_PX:
                fm = float(np.median(face_px))
                if fm < 35 or (fm < 55 and clip_hi > 0.10):
                    fi.issues.append("face_dark")
                elif float((face_px >= 250).mean()) > 0.30:
                    fi.issues.append("face_bright")
            # 프레임 잘림: 박스가 밖으로 나갔거나, 가장자리에 붙었는데 얼굴 비율이 찌그러진 경우
            out_x = max(0.0, -x) + max(0.0, x + fw - w)
            out_y = max(0.0, -y) + max(0.0, y + fh - h)
            touch_lr = x <= 3 or x + fw >= w - 3
            touch_tb = y <= 3 or y + fh >= h - 3
            cut = (out_x > 0.08 * fw or out_y > 0.08 * fh
                   or (touch_lr and fw / fh < 0.66) or (touch_tb and fh / fw < 1.0))
            if cut:
                fi.issues.append("face_cut")
            # 눈/표정 (흐리거나 잘린 얼굴은 랜드마크가 부정확하므로 생략)
            if fw >= DETAIL_FACE_PX and fi.focus >= self.th.face_blur and not cut:
                got = self.landmark(bgr, box)
                if got:
                    # 코가 두 눈 사이 어디쯤 있는지로 고개 돌림 정도 추정 (정면 ≈ 0.5)
                    span = eye_l[0] - eye_r[0]
                    t = (pts[2][0] - eye_r[0]) / span if abs(span) > 1 else 0.0
                    self.judge_expression(fi, *got, turned=not 0.2 <= t <= 0.8)
            infos.append(fi)

        self.judge(r, infos, mean, p25, p99, clip_hi)
        r.faces = [
            {"box": f.box, "focus": round(f.focus, 4), "blink": f.blink,
             "ear": tuple(round(e, 3) for e in f.ear) if f.ear else None,
             "look_down": round(f.look_down, 2), "smile": round(f.smile, 2), "issues": f.issues}
            for f in infos
        ]
        if self.thumbs_dir is not None:
            r.thumb = self.save_thumb(bgr, infos, index)
        return r

    def judge_expression(self, fi: FaceInfo, bs: dict, ears: tuple, turned: bool = False) -> None:
        g = lambda k: bs.get(k, 0.0)  # noqa: E731
        bl, br = g("eyeBlinkLeft"), g("eyeBlinkRight")
        look = (g("eyeLookDownLeft") + g("eyeLookDownRight")) / 2
        smile = (g("mouthSmileLeft") + g("mouthSmileRight")) / 2
        fi.blink, fi.ear = (round(bl, 2), round(br, 2)), ears
        fi.look_down, fi.smile = look, smile

        # 눈 감음 판정: 블렌드셰이프(eyeBlink) + 눈 세로/가로 비율(EAR) 두 신호를 함께 사용.
        # 아래를 보거나 활짝 웃을 때도 eyeBlink 가 0.4~0.6 까지 올라가므로 EAR 로 구분한다.
        th = self.th
        e_lo, e_hi = min(ears), max(ears)
        b_lo = min(bl, br)
        laugh = smile > 0.6
        down = look > 0.5
        if turned:
            # 옆을 보면 눈 윤곽이 찌그러져 EAR 를 믿을 수 없음 → 블렌드셰이프만 사용
            if b_lo >= th.eye_closed + 0.1:
                fi.issues.append("eyes_closed")
        elif e_hi < th.ear_closed or b_lo >= th.eye_closed + (0.1 if laugh else 0):
            # 웃거나 아래를 보느라 감긴 것처럼 보이는 경우는 '확인필요'로 분리
            fi.issues.append("eyes_laugh" if laugh else "eyes_down" if down else "eyes_closed")
        elif e_lo < th.ear_closed + 0.01 and e_hi >= 0.24:
            fi.issues.append("wink")
        elif e_hi < th.ear_half and not laugh:
            fi.issues.append("eyes_half")

        jaw = g("jawOpen")
        brow = (g("browDownLeft") + g("browDownRight")) / 2
        sneer = (g("noseSneerLeft") + g("noseSneerRight")) / 2
        frown = (g("mouthFrownLeft") + g("mouthFrownRight")) / 2
        stretch = (g("mouthStretchLeft") + g("mouthStretchRight")) / 2
        if smile < 0.3 and (brow >= 0.6 or sneer >= 0.4 or frown >= 0.5 or stretch >= 0.45):
            fi.issues.append("grimace")
        elif smile < 0.35 and 0.25 <= jaw <= 0.65:
            fi.issues.append("talking")
        elif smile < 0.3 and (g("mouthFunnel") >= 0.35 or g("mouthPucker") >= 0.6):
            fi.issues.append("pucker")

    def judge(self, r: PhotoResult, infos: list[FaceInfo], mean, p25, p99, clip_hi) -> None:
        th = self.th
        issues: list[tuple[str, str, str]] = []

        def add(code, severity=None, extra=""):
            sev0, _, text = ISSUES[code]
            issues.append((code, severity or sev0, text + extra))

        # 노출 (흰 배경·눈밭처럼 일부만 하얀 사진은 제외하고, 전체가 허옇게 뜬 경우만)
        if mean < 35 and p99 < 120:
            add("too_dark")
        elif clip_hi > 0.30 and p25 > 190:
            add("overexposed")

        focus_faces = [f for f in infos if f.focus > 0]
        if focus_faces:
            best = max(f.focus for f in focus_faces)
            primary = focus_faces[0]
            r.face_focus = round(best, 4)
            if best < th.face_blur:
                if r.scene_sharp >= th.scene_bg:
                    add("face_bg")
                elif r.scene_sharp < th.scene_soft:
                    add("scene_blur")
                else:
                    add("face_blur")
            elif primary.focus < th.face_blur and len(focus_faces) > 1:
                add("main_soft")
            elif best < th.face_soft:
                add("face_soft")
        else:
            if r.scene_sharp < th.scene_blur:
                add("scene_blur")
            elif r.scene_sharp < th.scene_soft:
                add("scene_soft")

        # 얼굴별 문제 집계
        n = len(infos)
        for code in ("eyes_closed", "eyes_down", "eyes_laugh", "eyes_half", "wink",
                     "grimace", "talking", "pucker",
                     "face_cut", "face_dark", "face_bright"):
            who = [i for i, f in enumerate(infos) if code in f.issues]
            if not who:
                continue
            extra = f" ({len(who)}/{n}명)" if n > 1 else ""
            sev = None
            if code == "eyes_closed" and n > 2 and 0 not in who and len(who) * 2 < n:
                sev = "review"  # 단체사진에서 일부만 감은 경우
            add(code, sev, extra)

        r.issues = [list(i) for i in issues]
        sevs = {s for _, s, _ in issues}
        r.verdict = "reject" if "reject" in sevs else "review" if "review" in sevs else "keep"
        penalty = sum(ISSUES[c][1] * (1 if s == "reject" else 0.6) for c, s, _ in issues)
        # 동점일 때 더 선명한 사진을 위로
        sharp_bonus = min(r.face_focus / th.face_soft, 2) if r.face_focus else min(r.scene_sharp / th.scene_soft, 2)
        r.score = round(max(0.0, 100 - penalty) * 0.9 + 5 * sharp_bonus, 1)  # 0~100

    def save_thumb(self, bgr: np.ndarray, infos: list[FaceInfo], index: int) -> str:
        h, w = bgr.shape[:2]
        s = 480 / max(h, w)
        th = cv2.resize(bgr, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        for f in infos:
            x, y, fw, fh = (v * s for v in f.box)
            bad = any(ISSUES[c][0] == "reject" for c in f.issues)
            color = (60, 60, 230) if bad else (0, 165, 255) if f.issues else (235, 235, 235)
            cv2.rectangle(th, (int(x), int(y)), (int(x + fw), int(y + fh)), color, 2)
        name = f"{index:06d}.jpg"
        # cv2.imwrite 는 Windows 에서 한글 경로에 조용히 실패하므로 인코딩만 하고 저장은 파이썬으로
        ok, buf = cv2.imencode(".jpg", th, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if ok:
            (self.thumbs_dir / name).write_bytes(buf.tobytes())
        return name


# ----------------------------------------------------------------------------
# 병렬 처리
# ----------------------------------------------------------------------------
_WORKER: Analyzer | None = None


def _init_worker(th: Thresholds, thumbs_dir):
    global _WORKER
    _WORKER = Analyzer(th, thumbs_dir)


def _work(args):
    root, path, idx = args
    try:
        return _WORKER.analyze(root, path, idx)
    except Exception as e:  # noqa: BLE001
        r = PhotoResult(path=path.relative_to(root).as_posix(), ok=False, error=str(e), verdict="review")
        r.issues.append(["error", "review", f"분석 오류: {e}"])
        return r


def analyze_threaded(root: Path, files: list[Path], th: Thresholds, thumbs_dir: Path | None,
                     workers: int, on_result, should_stop=lambda: False) -> None:
    """스레드로 병렬 분석 (GUI용). OpenCV·MediaPipe 는 계산 중 GIL 을 풀기 때문에 스레드로도 빨라진다.
    각 스레드가 자기 Analyzer 를 갖고, 결과는 on_result(PhotoResult) 로 하나씩 전달."""
    local = threading.local()
    made: list[Analyzer] = []
    lock = threading.Lock()

    def job(args):
        if should_stop():
            return None
        a = getattr(local, "a", None)
        if a is None:
            a = local.a = Analyzer(th, thumbs_dir)
            with lock:
                made.append(a)
        root_, path, idx = args
        try:
            return a.analyze(root_, path, idx)
        except Exception as e:  # noqa: BLE001
            r = PhotoResult(path=path.relative_to(root_).as_posix(), ok=False, error=str(e), verdict="review")
            r.issues.append(["error", "review", f"분석 오류: {e}"])
            return r

    jobs = [(root, p, i) for i, p in enumerate(files)]
    try:
        with cf.ThreadPoolExecutor(max(1, workers)) as ex:
            futs = [ex.submit(job, j) for j in jobs]
            for f in cf.as_completed(futs):
                r = f.result()
                if r is not None:
                    on_result(r)
                if should_stop():
                    for x in futs:
                        x.cancel()
                    break
    finally:
        for a in made:
            a.close()


def find_images(root: Path, recursive: bool) -> list[Path]:
    it = root.rglob("*") if recursive else root.iterdir()
    files = []
    for p in it:
        parts = p.relative_to(root).parts
        if OUT_DIR_NAME in parts or any(x.startswith(".") for x in parts):  # 숨김 폴더(.thumbnails 등) 제외
            continue
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            files.append(p)
    return sorted(files)


# ----------------------------------------------------------------------------
# 연사 묶기
# ----------------------------------------------------------------------------
def informative_hash(h: str) -> bool:
    """밋밋한 이미지(흰 화면, 검은 화면 등)는 dHash 비트가 한쪽으로 쏠려서 서로 '비슷해' 보이므로 묶지 않는다"""
    ones = bin(int(h, 16)).count("1")
    return 8 <= ones <= 56


def same_shape(a: PhotoResult, b: PhotoResult) -> bool:
    """연사는 같은 카메라·같은 방향으로 찍으므로 크기가 같다"""
    sa = tuple(a.work_size) if a.work_size and a.work_size[0] else tuple(a.size)
    sb = tuple(b.work_size) if b.work_size and b.work_size[0] else tuple(b.size)
    return len(sa) == 2 and len(sb) == 2 and abs(sa[0] - sb[0]) <= 1 and abs(sa[1] - sb[1]) <= 1


def is_burst_pair(a: PhotoResult, b: PhotoResult, th: Thresholds) -> bool:
    if not (same_shape(a, b) and informative_hash(a.dhash) and informative_hash(b.dhash)):
        return False
    dist = bin(int(a.dhash, 16) ^ int(b.dhash, 16)).count("1")
    gap = abs((datetime.fromisoformat(b.taken.rstrip("~")) -
               datetime.fromisoformat(a.taken.rstrip("~"))).total_seconds())
    if not a.taken.endswith("~") and not b.taken.endswith("~"):  # 둘 다 촬영 시각(EXIF) 있음
        return dist <= th.burst_hash and gap <= th.burst_gap
    # 촬영 시각이 없으면 파일 시각만으로는 믿기 어려우므로 훨씬 엄격하게
    return dist <= th.burst_hash // 2 and gap <= th.burst_gap * 3


def group_bursts(results: list[PhotoResult], th: Thresholds) -> None:
    items = [r for r in results if r.ok and r.dhash and r.verdict != "screenshot"]

    def tkey(r):
        return r.taken.rstrip("~")

    items.sort(key=lambda r: (tkey(r), r.path))
    groups: list[list[PhotoResult]] = []
    prev = None
    for r in items:
        if prev is not None:
            try:
                same = is_burst_pair(prev, r, th)
            except (ValueError, TypeError):
                same = False
            if same:
                groups[-1].append(r)
                prev = r
                continue
        groups.append([r])
        prev = r
    gid = 0
    for g in groups:
        if len(g) < 2:
            continue
        gid += 1
        best = max(g, key=lambda r: r.score)
        for r in g:
            r.burst_group = gid
            r.burst_best = best.path
            # 베스트가 아닌 컷은 '연사중복'으로 (명백한 불량, 직접 정한 판정은 유지)
            if r is not best and r.verdict != "reject" and not r.manual:
                r.verdict = "burst"


def mark_screenshot(r: PhotoResult) -> None:
    r.verdict = "screenshot"
    r.issues = [["screenshot", "screenshot", ISSUES["screenshot"][2]]]
    r.faces = []
    r.burst_group, r.burst_best = 0, ""


def base_verdict(r: PhotoResult) -> str:
    sevs = {s for _, s, _ in r.issues}
    return "reject" if "reject" in sevs else "review" if "review" in sevs else "keep"


def rejudge(results: list[PhotoResult], th: Thresholds) -> dict:
    """이전 규칙으로 만든 결과를 다시 분석하지 않고 새 규칙으로 다시 판정한다.
    (스크린샷 분리, 연사 묶기 기준). 직접 바꾼 판정은 그대로 둔다."""
    before = {v: 0 for v in VERDICT_KO}
    for r in results:
        before[r.verdict] = before.get(r.verdict, 0) + 1
    for r in results:
        r.burst_group, r.burst_best = 0, ""
        if not r.manual:
            if r.ok and looks_like_screenshot(r.path, r.size):
                mark_screenshot(r)
            elif r.verdict in ("burst", "screenshot"):
                r.verdict = base_verdict(r)
        r.v = RULES_VERSION
    group_bursts(results, th)
    after = {v: 0 for v in VERDICT_KO}
    for r in results:
        after[r.verdict] = after.get(r.verdict, 0) + 1
    return {"before": before, "after": after}


# ----------------------------------------------------------------------------
# 리포트
# ----------------------------------------------------------------------------
CSV_FIELDS = ["파일", "판정", "점수", "사유", "촬영시각", "얼굴수", "연사그룹", "연사베스트", "전체선명도", "인물선명도"]


def write_csv(out: Path, results: list[PhotoResult]) -> Path:
    p = out / "report.csv"
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(CSV_FIELDS)
        for r in results:
            w.writerow([
                r.path, VERDICT_KO[r.verdict], r.score, " / ".join(i[2] for i in r.issues),
                r.taken, r.n_main, r.burst_group or "", r.burst_best if r.burst_group else "",
                round(r.scene_sharp, 1), r.face_focus if r.face_focus is not None else "",
            ])
    return p


def write_html(out: Path, root: Path, results: list[PhotoResult], th: Thresholds) -> Path:
    order = {"reject": 0, "review": 1, "burst": 2, "screenshot": 3, "keep": 4}
    rs = sorted(results, key=lambda r: (order[r.verdict], r.burst_group or 0, -r.score, r.path))
    counts = {k: sum(1 for r in results if r.verdict == k) for k in order}
    cards = []
    for r in rs:
        reasons = "".join(
            f'<li class="{html.escape(s)}">{html.escape(t)}</li>' for _, s, t in r.issues
        )
        if r.verdict == "burst":
            reasons += f'<li class="burst">더 나은 컷: {html.escape(Path(r.burst_best).name)}</li>'
        elif r.burst_group:
            reasons += f'<li class="info">연사 묶음 #{r.burst_group}' + (
                " · 이 묶음의 베스트</li>" if r.burst_best == r.path else "</li>")
        href = html.escape(urllib.parse.quote(os.path.relpath(root / r.path, out).replace(os.sep, "/")))
        img = (f'<img loading="lazy" src="thumbs/{html.escape(r.thumb)}" alt="">' if r.thumb
               else '<div class="noimg">미리보기 없음</div>')
        cards.append(
            f'<figure class="card" data-v="{r.verdict}">'
            f'<a href="{href}" target="_blank">{img}</a>'
            f'<figcaption><div class="row"><span class="badge {r.verdict}">{VERDICT_KO[r.verdict]}</span>'
            f'<span class="score">{r.score:.0f}점</span></div>'
            f'<div class="name" title="{html.escape(r.path)}">{html.escape(r.path)}</div>'
            f'<ul>{reasons}</ul></figcaption></figure>'
        )
    chips = "".join(
        f'<button data-f="{k}" class="chip {k}">{VERDICT_KO[k]} <b>{counts[k]}</b></button>' for k in order
    )
    page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>사진 정리 리포트</title>
<style>
:root {{ --bg:#f6f6f4; --card:#fff; --fg:#1d1d1f; --mute:#6b6b70; --line:#e3e3e0;
  --reject:#d93a3a; --review:#d98a00; --burst:#3d6fd9; --screenshot:#159a9c; --keep:#2f9a57; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141416; --card:#1f1f22; --fg:#ececee;
  --mute:#9a9aa2; --line:#2e2e33; }} }}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--fg);
  font:14px/1.45 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif }}
header {{ position:sticky; top:0; z-index:2; background:var(--bg); padding:16px; border-bottom:1px solid var(--line) }}
h1 {{ font-size:18px; margin:0 0 4px }}
.sub {{ color:var(--mute); font-size:12px; margin-bottom:10px }}
.chips {{ display:flex; flex-wrap:wrap; gap:8px }}
.chip {{ border:1px solid var(--line); background:var(--card); color:var(--fg); border-radius:999px;
  padding:6px 12px; font:inherit; cursor:pointer }}
.chip b {{ margin-left:4px }}
.chip.on {{ outline:2px solid var(--fg) }}
.chip.reject b {{ color:var(--reject) }} .chip.review b {{ color:var(--review) }}
.chip.burst b {{ color:var(--burst) }} .chip.keep b {{ color:var(--keep) }}
.chip.screenshot b {{ color:var(--screenshot) }}
main {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:12px; padding:16px }}
.card {{ margin:0; background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden }}
.card img, .noimg {{ display:block; width:100%; aspect-ratio:4/3; object-fit:contain; background:#000 }}
.noimg {{ display:grid; place-items:center; color:#888 }}
figcaption {{ padding:8px 10px 10px }}
.row {{ display:flex; justify-content:space-between; align-items:center }}
.badge {{ font-size:12px; font-weight:600; padding:2px 8px; border-radius:6px; color:#fff }}
.badge.reject {{ background:var(--reject) }} .badge.review {{ background:var(--review) }}
.badge.burst {{ background:var(--burst) }} .badge.keep {{ background:var(--keep) }}
.badge.screenshot {{ background:var(--screenshot) }} li.screenshot {{ color:var(--screenshot) }}
.score {{ color:var(--mute); font-size:12px }}
.name {{ margin-top:6px; font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis }}
ul {{ margin:6px 0 0; padding-left:18px; font-size:12px }}
li.reject {{ color:var(--reject) }} li.review {{ color:var(--review) }}
li.burst {{ color:var(--burst) }} li.info {{ color:var(--mute) }}
.hide {{ display:none }}
</style></head><body>
<header><h1>사진 정리 리포트</h1>
<div class="sub">{html.escape(str(root))} · {len(results)}장 · {datetime.now():%Y-%m-%d %H:%M}
 · 사진을 누르면 원본이 열립니다 · 얼굴 테두리: 빨강=눈 감음, 주황=표정·눈·노출 확인, 흰색=문제 없음</div>
<div class="chips"><button data-f="all" class="chip on">전체 <b>{len(results)}</b></button>{chips}</div></header>
<main>{''.join(cards)}</main>
<script>
const chips=[...document.querySelectorAll('.chip')], cards=[...document.querySelectorAll('.card')];
chips.forEach(c=>c.onclick=()=>{{chips.forEach(x=>x.classList.toggle('on',x===c));
  const f=c.dataset.f; cards.forEach(k=>k.classList.toggle('hide',f!=='all'&&k.dataset.v!==f));}});
</script></body></html>"""
    p = out / "report.html"
    p.write_text(page, encoding="utf-8")
    return p


def save_reports(root: Path, results: list[PhotoResult], th: Thresholds) -> Path:
    out = root / OUT_DIR_NAME
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out, results)
    (out / "results.json").write_text(
        json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=1), encoding="utf-8")
    return write_html(out, root, results, th)


def load_results(root: Path) -> list[PhotoResult] | None:
    p = root / OUT_DIR_NAME / "results.json"
    if not p.exists():
        return None
    try:
        names = set(PhotoResult.__dataclass_fields__)
        return [PhotoResult(**{k: v for k, v in d.items() if k in names})
                for d in json.loads(p.read_text(encoding="utf-8"))]
    except Exception:  # noqa: BLE001
        return None


def face_color(issues, rgb=True):
    bad = any(ISSUES.get(c, ("",))[0] == "reject" for c in issues)
    c = (230, 60, 60) if bad else (255, 165, 0) if issues else (235, 235, 235)
    return c if rgb else c[::-1]


def make_thumb(src: Path, dest: Path, faces: list, work_size, size: int = 480) -> bool:
    """분석 결과(얼굴 박스)를 그린 썸네일을 원본에서 다시 만든다. (썸네일이 없는 이전 결과용)"""
    from PIL import ImageDraw
    try:
        with Image.open(src) as im:
            if im.format == "JPEG":
                s = size / max(im.size)
                if s < 1:
                    im.draft("RGB", (int(im.size[0] * s * 2), int(im.size[1] * s * 2)))
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((size, size))
            if faces and work_size and work_size[0]:
                k = im.width / work_size[0]
                d = ImageDraw.Draw(im)
                for f in faces:
                    x, y, w, h = (v * k for v in f["box"])
                    d.rectangle([x, y, x + w, y + h], outline=face_color(f.get("issues", [])), width=2)
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, "JPEG", quality=82)
        return True
    except Exception:  # noqa: BLE001
        return False


def log_deleted(root: Path, entries: list[dict]) -> None:
    """지운 사진 기록 (_사진정리/deleted.json)"""
    p = root / OUT_DIR_NAME / "deleted.json"
    try:
        log = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except Exception:  # noqa: BLE001
        log = []
    log.extend(entries)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")


def forget_moves(root: Path, from_paths) -> None:
    """지운 사진은 이동 기록에서 빼서, 되돌리기 할 때 '되돌리지 못함'으로 남지 않게"""
    p = root / OUT_DIR_NAME / "moves.json"
    if not p.exists():
        return
    drop = {Path(x).as_posix() for x in from_paths}
    log = json.loads(p.read_text(encoding="utf-8"))
    log = [m for m in log if Path(m["from"]).as_posix() not in drop]
    p.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")


# ----------------------------------------------------------------------------
# 파일 이동 / 되돌리기
# ----------------------------------------------------------------------------
def read_report_verdicts(root: Path) -> dict[str, str]:
    report = root / OUT_DIR_NAME / "report.csv"
    if not report.exists():
        raise FileNotFoundError(f"리포트가 없습니다: {report}\n먼저 scan 을 실행하세요.")
    try:
        with open(report, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    except UnicodeDecodeError:  # 엑셀에서 'CSV(쉼표로 분리)'로 저장하면 CP949
        with open(report, encoding="cp949") as f:
            rows = list(csv.DictReader(f))
    return {row["파일"]: KO_VERDICT.get(row["판정"].strip(), "keep") for row in rows}


def apply_moves(root: Path, verdicts: dict[str, str], categories=("reject", "review", "burst")) -> dict:
    """verdicts = {상대경로: 판정}. 지정한 판정의 사진을 _사진정리/<분류폴더> 로 옮긴다.
    반환: {"moved": {판정: 장수}, "map": {원래 상대경로: 새 상대경로}}"""
    out = root / OUT_DIR_NAME
    log_path = out / "moves.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    moved = {k: 0 for k in MOVE_FOLDERS}
    mapping = {}
    try:
        for rel, verdict in verdicts.items():
            if verdict not in MOVE_FOLDERS or verdict not in categories:
                continue
            src = root / rel
            if not src.exists():
                continue
            dst = out / MOVE_FOLDERS[verdict] / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            n = 1
            while dst.exists():
                dst = dst.with_name(f"{dst.stem}_{n}{dst.suffix}")
                n += 1
            shutil.move(str(src), str(dst))
            new_rel = dst.relative_to(root).as_posix()
            log.append({"from": Path(rel).as_posix(), "to": new_rel})
            mapping[rel] = new_rel
            moved[verdict] += 1
    finally:  # 중간에 오류가 나도 지금까지 옮긴 기록은 남겨서 되돌릴 수 있게
        out.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"moved": moved, "map": mapping}


def undo_moves(root: Path) -> tuple[int, int]:
    """이동 기록을 거꾸로 되돌린다. 반환: (되돌린 장수, 되돌리지 못한 장수)"""
    log_path = root / OUT_DIR_NAME / "moves.json"
    if not log_path.exists():
        return 0, 0
    log = json.loads(log_path.read_text(encoding="utf-8"))
    left, n = [], 0
    for m in reversed(log):
        src, dst = root / m["to"], root / m["from"]
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            n += 1
        else:
            left.append(m)
    log_path.write_text(json.dumps(list(reversed(left)), ensure_ascii=False, indent=1), encoding="utf-8")
    # 비어 버린 분류 폴더 정리
    for folder in MOVE_FOLDERS.values():
        base = root / OUT_DIR_NAME / folder
        if base.exists():
            for d in sorted((p for p in base.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
                if not any(d.iterdir()):
                    d.rmdir()
            if not any(base.iterdir()):
                base.rmdir()
    return n, len(left)


# ----------------------------------------------------------------------------
# scan
# ----------------------------------------------------------------------------
def cli_apply(root: Path, include_review: bool, screenshots: bool = False) -> None:
    try:
        verdicts = read_report_verdicts(root)
    except FileNotFoundError as e:
        sys.exit(str(e))
    cats = ("reject", "review", "burst") if include_review else ("reject", "burst")
    if screenshots:
        cats += ("screenshot",)
    res = apply_moves(root, verdicts, cats)
    print("이동 완료: " + ", ".join(f"{MOVE_FOLDERS[k]} {v}장" for k, v in res["moved"].items()))
    print(f"위치: {root / OUT_DIR_NAME}")
    print("되돌리려면: python photo_cull.py undo " + str(root))


def cli_undo(root: Path) -> None:
    n, left = undo_moves(root)
    if n == 0 and left == 0:
        sys.exit("되돌릴 이동 기록이 없습니다.")
    print(f"{n}장을 원래 위치로 되돌렸습니다." + (f" ({left}장은 되돌리지 못함)" if left else ""))


def scan(root: Path, recursive: bool, workers: int, sensitivity: str, move: bool) -> None:
    try:
        ensure_models()
    except ModelError as e:
        sys.exit(str(e))
    th = Thresholds.for_sensitivity(sensitivity)
    out = root / OUT_DIR_NAME
    thumbs = out / "thumbs"
    if thumbs.exists():
        shutil.rmtree(thumbs)
    thumbs.mkdir(parents=True)

    files = find_images(root, recursive)
    if not files:
        sys.exit("사진을 찾지 못했습니다. (하위 폴더까지 보려면 -r)")
    print(f"{len(files)}장 분석 시작 (작업자 {workers}개, 민감도 {sensitivity})"
          + ("" if HEIF_OK else "  ※ HEIC는 pillow-heif 설치 시 지원"))
    jobs = [(root, p, i) for i, p in enumerate(files)]
    results: list[PhotoResult] = []
    t0 = time.time()

    def report_progress(r: PhotoResult):
        results.append(r)
        k = len(results)
        if k == len(files) or k % 10 == 0 or r.verdict == "reject":
            tag = "" if r.verdict == "keep" else f"  → {VERDICT_KO[r.verdict]}: " + ", ".join(i[2] for i in r.issues)
            el = time.time() - t0
            eta = el / k * (len(files) - k)
            print(f"[{k}/{len(files)}] {r.path}{tag}   (남은 시간 ~{eta:.0f}초)")

    if workers <= 1:
        _init_worker(th, thumbs)
        for j in jobs:
            report_progress(_work(j))
        _WORKER.close()
    else:
        with cf.ProcessPoolExecutor(workers, initializer=_init_worker, initargs=(th, thumbs)) as ex:
            for r in ex.map(_work, jobs, chunksize=4):
                report_progress(r)

    group_bursts(results, th)
    page = save_reports(root, results, th)

    c = {k: sum(1 for r in results if r.verdict == k) for k in VERDICT_KO}
    print(f"\n완료 ({time.time() - t0:.0f}초): 불량 {c['reject']} · 확인필요 {c['review']} · "
          f"연사중복 {c['burst']} · 스크린샷 {c['screenshot']} · 정상 {c['keep']}")
    print(f"리포트: {page}")
    if move:
        cli_apply(root, include_review=True)
    else:
        print("판정대로 정리하려면: python photo_cull.py apply " + str(root)
              + "\n(report.csv 의 '판정' 열을 엑셀에서 고친 뒤 apply 하면 고친 대로 이동합니다)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="흐리거나 눈 감은 사진 등 불완전한 컷을 골라냅니다.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="사진 분석 + 리포트 생성")
    s.add_argument("folder")
    s.add_argument("-r", "--recursive", action="store_true", help="하위 폴더 포함")
    s.add_argument("-w", "--workers", type=int, default=max(1, min(4, (os.cpu_count() or 2) - 1)))
    s.add_argument("-s", "--sensitivity", choices=["low", "normal", "high"], default="normal",
                   help="high = 더 엄격하게(더 많이 걸러냄), low = 확실한 것만")
    s.add_argument("--move", action="store_true", help="분석 후 바로 판정대로 이동")
    a = sub.add_parser("apply", help="report.csv 판정대로 사진 이동")
    a.add_argument("folder")
    a.add_argument("--skip-review", action="store_true", help="'확인필요'는 옮기지 않음")
    a.add_argument("--screenshots", action="store_true", help="스크린샷도 4_스크린샷 폴더로 옮김")
    u = sub.add_parser("undo", help="이동한 사진을 원래 자리로")
    u.add_argument("folder")
    args = ap.parse_args(argv)

    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"폴더가 아닙니다: {root}")
    if args.cmd == "scan":
        scan(root, args.recursive, args.workers, args.sensitivity, args.move)
    elif args.cmd == "apply":
        cli_apply(root, include_review=not args.skip_review, screenshots=args.screenshots)
    else:
        cli_undo(root)


if __name__ == "__main__":
    main()
