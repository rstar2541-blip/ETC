# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 빌드 설정 — 실행:  pyinstaller photo_culler.spec
# 빌드 전에 models/ 폴더에 모델 2개가 있어야 합니다 (python photo_cull.py 를 한 번 실행하면 자동으로 받음).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

for m in ("face_detection_yunet_2023mar.onnx", "face_landmarker.task"):
    if not (Path("models") / m).exists():
        raise SystemExit(f"models/{m} 가 없습니다. 먼저 모델을 받아 주세요.")

datas, binaries, hiddenimports = collect_all("mediapipe")
datas += [("models", "models"), ("assets/app.png", "assets")]
hiddenimports += ["pillow_heif"]

a = Analysis(
    ["photo_cull_app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "IPython", "pytest", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
# 쓰지 않는 Qt 구성요소(번역 파일, 소프트웨어 OpenGL, QML/Quick/PDF)는 빼서 용량을 줄임
_DROP = ("opengl32sw", "qt6quick", "qt6qml", "qt6pdf", "qt6virtualkeyboard", "/translations/", "\\translations\\")


def _keep(entry):
    name = (entry[0] + "|" + entry[1]).lower().replace("\\", "/")
    return not any(d.replace("\\", "/") in name for d in _DROP)


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PhotoCuller",
    console=False,
    upx=False,
    icon="assets/app.ico" if sys.platform == "win32" else "assets/app.png",
)
coll = COLLECT(exe, a.binaries, a.datas, name="PhotoCuller", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="PhotoCuller.app",
        icon="assets/app.png",
        bundle_identifier="io.github.photoculler",
        info_plist={
            "CFBundleDisplayName": "사진 정리",
            "CFBundleShortVersionString": "1.0",
            "NSHighResolutionCapable": True,
        },
    )
