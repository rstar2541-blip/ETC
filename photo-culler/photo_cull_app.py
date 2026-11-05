#!/usr/bin/env python3
"""
사진 정리 — 흐리거나 눈 감은 사진을 골라 주는 데스크톱 프로그램
(분석 엔진은 photo_cull.py, 이 파일은 화면)

  python photo_cull_app.py
"""
from __future__ import annotations

import os
import sys

if sys.stdout is None:  # 창 모드로 묶인 exe 에는 콘솔이 없음
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import json
import shutil
import time
import traceback
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps
from PySide6.QtCore import (QAbstractListModel, QFile, QItemSelectionModel, QModelIndex, QObject, QRect, QSettings,
                            QSize, Qt, QThread, QThreadPool, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QImage, QKeySequence, QPainter,
                           QPainterPath, QPen, QPixmap, QPixmapCache, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QListView, QMainWindow,
                               QMessageBox, QProgressBar, QProgressDialog, QPushButton, QSizePolicy, QSplitter,
                               QStackedWidget,
                               QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

import photo_cull as pc

APP_TITLE = "사진 정리"
VERDICTS = ["reject", "review", "burst", "keep"]
ORDER = {v: i for i, v in enumerate(VERDICTS)}
COLORS = {"reject": "#d93a3a", "review": "#d98a00", "burst": "#3d6fd9", "keep": "#2f9a57"}
SEV_COLORS = {"reject": "#d93a3a", "review": "#c27c00", "burst": "#3d6fd9", "info": "#888888"}
SENSITIVITY = [("느슨하게 — 확실한 것만", "low"), ("보통", "normal"), ("엄격하게 — 더 많이 걸러냄", "high")]
VERDICT_KEYS = {"reject": "1", "review": "2", "burst": "3", "keep": "4"}
VERDICT_BUTTON = {"reject": "불량", "review": "확인필요", "burst": "연사중복", "keep": "남기기(정상)"}


def resource(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


def load_qimage(path: Path, max_side: int = 1600) -> QImage | None:
    try:
        with Image.open(path) as im:
            if im.format == "JPEG":
                s = max_side / max(im.size)
                if s < 1:
                    im.draft("RGB", (int(im.size[0] * s), int(im.size[1] * s)))
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((max_side, max_side))
            data = im.tobytes()
            return QImage(data, im.width, im.height, im.width * 3, QImage.Format.Format_RGB888).copy()
    except Exception:  # noqa: BLE001
        return None


def fmt_secs(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}초"
    return f"{s // 60}분 {s % 60}초"


# ----------------------------------------------------------------------------
# 분석 작업 (백그라운드 스레드)
# ----------------------------------------------------------------------------
class ScanWorker(QThread):
    status = Signal(str)
    progress = Signal(int, int)
    result = Signal(object)
    done = Signal(list, bool)
    failed = Signal(str)

    def __init__(self, root: Path, recursive: bool, sensitivity: str, workers: int):
        super().__init__()
        self.root, self.recursive, self.sensitivity, self.workers = root, recursive, sensitivity, workers
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            self.status.emit("준비 중…")
            pc.ensure_models(log=self.status.emit)
            files = pc.find_images(self.root, self.recursive)
            if not files:
                self.failed.emit("이 폴더에서 사진을 찾지 못했습니다.\n"
                                 "사진이 하위 폴더에 있다면 '하위 폴더 포함'을 체크하세요.")
                return
            th = pc.Thresholds.for_sensitivity(self.sensitivity)
            thumbs = self.root / pc.OUT_DIR_NAME / "thumbs"
            if thumbs.exists():
                shutil.rmtree(thumbs)
            thumbs.mkdir(parents=True)
            results: list[pc.PhotoResult] = []
            total = len(files)
            self.progress.emit(0, total)

            def on_result(r):
                results.append(r)
                self.result.emit(r)
                self.progress.emit(len(results), total)

            pc.analyze_threaded(self.root, files, th, thumbs, self.workers, on_result, lambda: self._stop)
            self.status.emit("비슷한 사진 묶는 중…")
            pc.group_bursts(results, th)
            pc.save_reports(self.root, results, th)
            self.done.emit(results, self._stop)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{e}\n\n{traceback.format_exc(limit=4)}")


class ThumbMaker(QObject):
    """썸네일 파일이 없으면(이전 버전 결과 등) 원본에서 백그라운드로 다시 만든다.
    가장 최근에 요청한 것(= 지금 화면에 보이는 것)부터 만든다."""
    ready = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max(2, min(4, (os.cpu_count() or 2) - 1)))
        self.pending: set[str] = set()
        self.failed: set[str] = set()
        self.seq = 0

    def request(self, src: Path, dest: Path, faces, work_size):
        key = str(dest)
        if key in self.pending or key in self.failed:
            return
        self.pending.add(key)
        self.seq += 1

        def job():
            self.ready.emit(key, pc.make_thumb(src, dest, faces, work_size))

        self.pool.start(job, min(self.seq, 2_000_000_000))

    def clear(self):
        self.pool.clear()
        self.pending.clear()
        self.failed.clear()


# ----------------------------------------------------------------------------
# 목록 모델 + 카드 그리기
# ----------------------------------------------------------------------------
class ResultModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.all: list[pc.PhotoResult] = []
        self.rows: list[int] = []
        self.filter = "all"

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        if not idx.isValid() or idx.row() >= len(self.rows):
            return None
        r = self.all[self.rows[idx.row()]]
        if role == Qt.ItemDataRole.UserRole:
            return r
        if role == Qt.ItemDataRole.DisplayRole:
            return r.path
        if role == Qt.ItemDataRole.ToolTipRole:
            return "\n".join([r.path] + [i[2] for i in r.issues])
        return None

    def matches(self, r):
        return self.filter == "all" or r.verdict == self.filter

    def _build(self, sort: bool):
        rows = [i for i, r in enumerate(self.all) if self.matches(r)]
        if sort:
            a = self.all
            rows.sort(key=lambda i: (ORDER[a[i].verdict], a[i].burst_group or 0, -a[i].score, a[i].path))
        return rows

    def set_results(self, results, sort=True):
        self.beginResetModel()
        self.all = results
        self.rows = self._build(sort)
        self.endResetModel()

    def set_filter(self, f):
        self.beginResetModel()
        self.filter = f
        self.rows = self._build(True)
        self.endResetModel()

    def append(self, r):
        self.all.append(r)
        if self.matches(r):
            n = len(self.rows)
            self.beginInsertRows(QModelIndex(), n, n)
            self.rows.append(len(self.all) - 1)
            self.endInsertRows()

    def drop_unmatched(self):
        """판정을 바꾼 뒤 현재 필터에 안 맞는 항목을 빼되, 나머지 순서는 유지"""
        self.beginResetModel()
        self.rows = [i for i in self.rows if self.matches(self.all[i])]
        self.endResetModel()

    def remove(self, gone: set):
        """gone = 지울 결과 객체들의 id() 집합. 화면에 보이던 순서는 유지"""
        self.beginResetModel()
        shown = [self.all[i] for i in self.rows if id(self.all[i]) not in gone]
        self.all = [r for r in self.all if id(r) not in gone]
        pos = {id(r): k for k, r in enumerate(self.all)}
        self.rows = [pos[id(r)] for r in shown]
        self.endResetModel()

    def counts(self):
        c = {v: 0 for v in VERDICTS}
        for r in self.all:
            c[r.verdict] += 1
        return c


class CardDelegate(QStyledItemDelegate):
    W, H = 220, 248
    TH = 156

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(self.W, self.H)

    def paint(self, p: QPainter, opt, idx):
        r: pc.PhotoResult = idx.data(Qt.ItemDataRole.UserRole)
        if r is None:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = opt.palette
        rect = opt.rect.adjusted(5, 5, -5, -5)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        card = QPainterPath()
        card.addRoundedRect(rect, 8, 8)
        p.fillPath(card, pal.base())
        p.setPen(QPen(pal.highlight().color(), 3) if selected else QPen(pal.mid().color(), 1))
        p.drawPath(card)

        th = QRect(rect.x() + 6, rect.y() + 6, rect.width() - 12, self.TH)
        p.fillRect(th, QColor("#151515"))
        pm = self.win.thumb_pixmap(r)
        if pm is None and r.thumb:
            p.setPen(QColor("#777777"))
            p.drawText(th, Qt.AlignmentFlag.AlignCenter, "미리보기 만드는 중…")
        if pm is not None and not pm.isNull():
            sc = pm.scaled(th.size() * p.device().devicePixelRatioF(), Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            sc.setDevicePixelRatio(p.device().devicePixelRatioF())
            w = sc.width() / sc.devicePixelRatio()
            h = sc.height() / sc.devicePixelRatio()
            p.drawPixmap(int(th.x() + (th.width() - w) / 2), int(th.y() + (th.height() - h) / 2), sc)

        f = QFont(opt.font)
        f.setBold(True)
        fm = QFontMetrics(f)
        y = th.bottom() + 8
        text = pc.VERDICT_KO[r.verdict] + (" ✎" if r.manual else "")
        badge = QRect(th.x(), y, fm.horizontalAdvance(text) + 14, fm.height() + 4)
        bp = QPainterPath()
        bp.addRoundedRect(badge, 4, 4)
        p.fillPath(bp, QColor(COLORS[r.verdict]))
        p.setFont(f)
        p.setPen(Qt.GlobalColor.white)
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)

        p.setFont(opt.font)
        fm2 = QFontMetrics(opt.font)
        p.setPen(pal.placeholderText().color())
        right = "이동됨" if self.win.is_moved(r) else f"{r.score:.0f}점"
        p.drawText(QRect(th.x(), y, th.width(), badge.height()),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, right)

        y = badge.bottom() + 6
        p.setPen(pal.text().color())
        name = fm2.elidedText(Path(r.path).name, Qt.TextElideMode.ElideMiddle, th.width())
        p.drawText(QRect(th.x(), y, th.width(), fm2.height()), Qt.AlignmentFlag.AlignLeft, name)

        y += fm2.height() + 2
        if r.issues:
            sev, reason = r.issues[0][1], r.issues[0][2]
            if len(r.issues) > 1:
                reason += f" 외 {len(r.issues) - 1}"
        elif r.verdict == "burst":
            sev, reason = "burst", "비슷한 사진 중 더 나은 컷 있음"
        else:
            sev, reason = "info", ""
        if r.verdict == "burst" and r.issues:
            sev = "burst"
        p.setPen(QColor(SEV_COLORS.get(sev, "#888888")))
        p.drawText(QRect(th.x(), y, th.width(), fm2.height()), Qt.AlignmentFlag.AlignLeft,
                   fm2.elidedText(reason, Qt.TextElideMode.ElideRight, th.width()))
        p.restore()


# ----------------------------------------------------------------------------
# 큰 미리보기 (얼굴 테두리 포함)
# ----------------------------------------------------------------------------
class PreviewView(QWidget):
    def __init__(self):
        super().__init__()
        self.img: QImage | None = None
        self.r: pc.PhotoResult | None = None
        self.msg = ""
        self.setMinimumSize(280, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_photo(self, img: QImage | None, r: pc.PhotoResult | None, msg: str = ""):
        self.img, self.r, self.msg = img, r, msg
        self.update()

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#151515"))
        if self.img is None:
            p.setPen(QColor("#999999"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.msg)
            return
        iw, ih = self.img.width(), self.img.height()
        s = min(self.width() / iw, self.height() / ih)
        tw, th = iw * s, ih * s
        tx, ty = (self.width() - tw) / 2, (self.height() - th) / 2
        p.drawImage(QRect(int(tx), int(ty), int(tw), int(th)), self.img)
        r = self.r
        if r and r.faces and r.work_size and r.work_size[0]:
            k = tw / r.work_size[0]
            for f in r.faces:
                x, y, w, h = f["box"]
                bad = any(pc.ISSUES.get(c, ("",))[0] == "reject" for c in f["issues"])
                color = QColor("#ff4040") if bad else QColor("#ffa500") if f["issues"] else QColor(255, 255, 255, 180)
                p.setPen(QPen(color, 2))
                p.drawRect(int(tx + x * k), int(ty + y * k), int(w * k), int(h * k))


# ----------------------------------------------------------------------------
# 정리(이동) 확인 창
# ----------------------------------------------------------------------------
class ApplyDialog(QDialog):
    def __init__(self, parent, counts: dict, root: Path):
        super().__init__(parent)
        self.setWindowTitle("사진 정리하기")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        info = QLabel(
            f"선택한 분류의 사진을 아래 폴더 안으로 <b>옮깁니다</b>.<br>"
            f"<span style='color:gray'>{root / pc.OUT_DIR_NAME}</span><br><br>"
            "사진은 <b>삭제되지 않으며</b>, 언제든 [되돌리기]로 원래 자리로 돌릴 수 있습니다.")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.boxes = {}
        for v in ("reject", "review", "burst"):
            cb = QCheckBox(f"{pc.VERDICT_KO[v]} {counts.get(v, 0)}장  →  {pc.MOVE_FOLDERS[v]}")
            cb.setChecked(counts.get(v, 0) > 0)
            cb.setEnabled(counts.get(v, 0) > 0)
            lay.addWidget(cb)
            self.boxes[v] = cb
        keep = QLabel(f"정상 {counts.get('keep', 0)}장은 그대로 둡니다.")
        keep.setStyleSheet("color: gray")
        lay.addWidget(keep)
        bb = QDialogButtonBox()
        ok = bb.addButton("옮기기", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton("취소", QDialogButtonBox.ButtonRole.RejectRole)
        ok.setDefault(True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def categories(self):
        return tuple(v for v, cb in self.boxes.items() if cb.isChecked())


# ----------------------------------------------------------------------------
# 메인 창
# ----------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        icon = resource("assets/app.png")
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.settings = QSettings("PhotoCuller", "PhotoCuller")
        self.root: Path | None = None
        self.worker: ScanWorker | None = None
        self.moved: dict[str, str] = {}
        self.t0 = 0.0
        self.current: pc.PhotoResult | None = None
        self.preview_cache: dict[str, QImage] = {}
        self.permanent_ok: set[str] = set()  # 휴지통 없는 드라이브에서 영구 삭제를 허락한 폴더
        self.quiet = False  # 자가 점검 때는 확인 창을 띄우지 않음
        self.thumbs = ThumbMaker()
        self.thumbs.ready.connect(self.on_thumb_ready)
        self.flash_timer = QTimer(self, singleShot=True, interval=5000, timeout=self.unflash)
        self.save_timer = QTimer(self, singleShot=True, interval=1500, timeout=self.save_now)
        QPixmapCache.setCacheLimit(256 * 1024)
        self.setAcceptDrops(True)
        self._build_ui()
        self.resize(1280, 820)
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        last = self.settings.value("last_folder", "")
        if last and Path(last).is_dir():
            self.set_folder(Path(last), ask_load=False)

    # -- UI --------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        top = QHBoxLayout()
        self.btn_folder = QPushButton("📁  폴더 선택")
        self.btn_folder.clicked.connect(self.choose_folder)
        self.lbl_folder = QLabel("사진 폴더를 선택하세요")
        self.lbl_folder.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_folder.setStyleSheet("color: gray")
        self.chk_recursive = QCheckBox("하위 폴더 포함")
        self.chk_recursive.setChecked(self.settings.value("recursive", "true") == "true")
        self.cmb_sens = QComboBox()
        for label, _ in SENSITIVITY:
            self.cmb_sens.addItem(label)
        self.cmb_sens.setCurrentIndex(int(self.settings.value("sensitivity", 1)))
        self.btn_scan = QPushButton("▶  분석 시작")
        self.btn_scan.setEnabled(False)
        self.btn_scan.setStyleSheet("QPushButton { font-weight: 600; padding: 6px 16px; }")
        self.btn_scan.clicked.connect(self.toggle_scan)
        top.addWidget(self.btn_folder)
        top.addWidget(self.lbl_folder, 1)
        top.addWidget(self.chk_recursive)
        top.addWidget(QLabel("민감도"))
        top.addWidget(self.cmb_sens)
        top.addWidget(self.btn_scan)
        outer.addLayout(top)

        prog = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(8)
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: gray")
        prog.addWidget(self.progress, 1)
        prog.addWidget(self.lbl_status)
        self.prog_row = QWidget()
        self.prog_row.setLayout(prog)
        prog.setContentsMargins(0, 0, 0, 0)
        self.prog_row.hide()
        outer.addWidget(self.prog_row)

        # 분류 탭
        tabs = QHBoxLayout()
        self.filter_group = QButtonGroup(self)
        self.filter_btns = {}
        for key in ["all"] + VERDICTS:
            b = QPushButton()
            b.setCheckable(True)
            b.setProperty("fkey", key)
            color = COLORS.get(key, "palette(text)")
            b.setStyleSheet(
                "QPushButton { border: 1px solid palette(mid); border-radius: 14px; padding: 4px 14px; }"
                f"QPushButton:checked {{ border: 2px solid {color}; font-weight: 600; }}")
            self.filter_group.addButton(b)
            self.filter_btns[key] = b
            tabs.addWidget(b)
            b.clicked.connect(lambda _=False, k=key: self.set_filter(k))
        self.filter_btns["all"].setChecked(True)
        tabs.addStretch(1)
        hint = QLabel("단축키  1 불량 · 2 확인필요 · 3 연사중복 · 4 남기기 · Del 삭제(휴지통) · Enter 원본 열기")
        hint.setStyleSheet("color: gray")
        tabs.addWidget(hint)
        outer.addLayout(tabs)

        # 가운데: 시작 화면 / 결과 화면
        self.stack = QStackedWidget()
        welcome = QWidget()
        wl = QVBoxLayout(welcome)
        wl.addStretch(1)
        t1 = QLabel("흐리거나, 눈 감았거나, 표정이 어색한 사진을 골라 드려요")
        t1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = t1.font()
        f.setPointSizeF(f.pointSizeF() * 1.5)
        f.setBold(True)
        t1.setFont(f)
        t2 = QLabel("사진 폴더를 선택하거나 이 창에 끌어다 놓으세요.\n"
                    "분석은 이 컴퓨터에서만 이루어지고, 사진은 직접 지우기 전에는 그대로 있습니다.")
        t2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t2.setStyleSheet("color: gray")
        big = QPushButton("📁  사진 폴더 선택")
        big.setStyleSheet("QPushButton { font-size: 15px; padding: 12px 28px; }")
        big.clicked.connect(self.choose_folder)
        wl.addWidget(t1)
        wl.addSpacing(8)
        wl.addWidget(t2)
        wl.addSpacing(20)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(big)
        row.addStretch(1)
        wl.addLayout(row)
        wl.addStretch(2)
        self.stack.addWidget(welcome)

        self.model = ResultModel()
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setUniformItemSizes(True)
        self.view.setSpacing(2)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setItemDelegate(CardDelegate(self))
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.verticalScrollBar().setSingleStep(30)
        self.view.selectionModel().currentChanged.connect(lambda *_: self.show_current())
        self.view.selectionModel().selectionChanged.connect(lambda *_: self.update_detail_buttons())
        self.view.doubleClicked.connect(lambda *_: self.open_original())

        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(8, 0, 0, 0)
        self.preview = PreviewView()
        dl.addWidget(self.preview, 3)
        self.lbl_detail = QLabel()
        self.lbl_detail.setWordWrap(True)
        self.lbl_detail.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_detail.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.lbl_detail.linkActivated.connect(self.on_link)
        self.lbl_detail.setMinimumHeight(110)
        dl.addWidget(self.lbl_detail, 1)
        vb = QHBoxLayout()
        self.verdict_btns = {}
        for v in VERDICTS:
            b = QPushButton(f"{VERDICT_BUTTON[v]}  {VERDICT_KEYS[v]}")
            b.setStyleSheet(f"QPushButton {{ color: white; background: {COLORS[v]}; border: none;"
                            " border-radius: 6px; padding: 7px 6px; font-weight: 600; }"
                            "QPushButton:disabled { background: palette(mid); }")
            b.clicked.connect(lambda _=False, vv=v: self.set_verdict(vv))
            vb.addWidget(b)
            self.verdict_btns[v] = b
        dl.addLayout(vb)
        row2 = QHBoxLayout()
        self.btn_open = QPushButton("원본 열기  ↵")
        self.btn_open.clicked.connect(self.open_original)
        self.btn_delete = QPushButton("🗑  삭제  Del")
        self.btn_delete.setToolTip("선택한 사진을 휴지통으로 보냅니다 (휴지통에서 복원 가능)")
        self.btn_delete.clicked.connect(self.delete_selected)
        row2.addWidget(self.btn_open, 2)
        row2.addWidget(self.btn_delete, 1)
        dl.addLayout(row2)

        split = QSplitter()
        split.addWidget(self.view)
        split.addWidget(detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([760, 480])
        self.stack.addWidget(split)
        outer.addWidget(self.stack, 1)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: palette(mid)")
        outer.addWidget(line)
        bottom = QHBoxLayout()
        self.lbl_summary = QLabel("")
        self.btn_report = QPushButton("리포트(HTML) 열기")
        self.btn_report.clicked.connect(self.open_report)
        self.btn_undo = QPushButton("↩  되돌리기")
        self.btn_undo.clicked.connect(self.undo)
        self.btn_apply = QPushButton("정리하기  →")
        self.btn_apply.setStyleSheet("QPushButton { font-weight: 600; padding: 6px 18px; }")
        self.btn_apply.clicked.connect(self.apply)
        bottom.addWidget(self.lbl_summary, 1)
        bottom.addWidget(self.btn_report)
        bottom.addWidget(self.btn_undo)
        bottom.addWidget(self.btn_apply)
        outer.addLayout(bottom)
        self.setCentralWidget(central)

        for v, key in VERDICT_KEYS.items():
            QShortcut(QKeySequence(key), self, activated=lambda vv=v: self.set_verdict(vv))
        QShortcut(QKeySequence(Qt.Key.Key_Return), self.view, activated=self.open_original)
        QShortcut(QKeySequence(Qt.Key.Key_Enter), self.view, activated=self.open_original)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self, activated=self.delete_selected)
        if sys.platform == "darwin":  # 맥 키보드의 delete 키
            QShortcut(QKeySequence(Qt.Key.Key_Backspace), self, activated=self.delete_selected)
        self.refresh_counts()
        self.update_detail_buttons()

    # -- 폴더 --------------------------------------------------------------
    def choose_folder(self):
        start = str(self.root) if self.root else str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "사진 폴더 선택", start)
        if d:
            self.set_folder(Path(d))

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        for u in e.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.is_file():
                p = p.parent
            if p.is_dir():
                self.set_folder(p)
                break

    def set_folder(self, folder: Path, ask_load: bool = True):
        if self.worker is not None:
            return
        self.save_now()
        self.root = folder
        self.settings.setValue("last_folder", str(folder))
        self.lbl_folder.setText(str(folder))
        self.lbl_folder.setToolTip(str(folder))
        self.lbl_folder.setStyleSheet("")
        self.btn_scan.setEnabled(True)
        self.model.set_results([])
        self.moved = {}
        self.current = None
        self.preview.set_photo(None, None, "")
        self.lbl_detail.setText("")
        QPixmapCache.clear()
        self.preview_cache.clear()
        self.thumbs.clear()
        prev = pc.load_results(folder)
        if prev:
            load = True
            if ask_load:
                ans = QMessageBox.question(
                    self, APP_TITLE,
                    f"이 폴더를 전에 분석한 결과({len(prev)}장)가 있습니다.\n불러올까요?\n\n"
                    "[아니요]를 누르면 새로 분석합니다.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                load = ans == QMessageBox.StandardButton.Yes
            if load:
                self.load_existing(folder, prev)
                return
            self.start_scan()
            return
        self.stack.setCurrentIndex(0)
        self.refresh_counts()
        if ask_load:
            self.start_scan()

    def load_existing(self, folder: Path, results=None):
        results = results if results is not None else (pc.load_results(folder) or [])
        self.root = folder
        log = folder / pc.OUT_DIR_NAME / "moves.json"
        self.moved = {}
        if log.exists():
            try:
                for m in json.loads(log.read_text(encoding="utf-8")):
                    self.moved[Path(m["from"]).as_posix()] = Path(m["to"]).as_posix()
            except Exception:  # noqa: BLE001
                pass
        self.model.set_results(results)
        self.stack.setCurrentIndex(1)
        self.refresh_counts()
        self.select_row(0)

    # -- 분석 --------------------------------------------------------------
    def toggle_scan(self):
        if self.worker is not None:
            self.worker.stop()
            self.btn_scan.setEnabled(False)
            self.lbl_status.setText("멈추는 중…")
        else:
            self.start_scan()

    def start_scan(self):
        if self.root is None or self.worker is not None:
            return
        if self.moved:
            ans = QMessageBox.question(
                self, APP_TITLE,
                "이미 정리(이동)한 사진이 있습니다. 새로 분석하기 전에 원래 자리로 되돌릴까요?\n\n"
                "[아니요]를 누르면 남아 있는 사진만 분석합니다.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel)
            if ans == QMessageBox.StandardButton.Cancel:
                return
            if ans == QMessageBox.StandardButton.Yes:
                pc.undo_moves(self.root)
            self.moved = {}
        self.settings.setValue("recursive", "true" if self.chk_recursive.isChecked() else "false")
        self.settings.setValue("sensitivity", self.cmb_sens.currentIndex())
        QPixmapCache.clear()
        self.preview_cache.clear()
        self.thumbs.clear()
        self.model.set_results([], sort=False)
        self.set_filter("all")
        self.stack.setCurrentIndex(1)
        self.current = None
        self.preview.set_photo(None, None, "분석 중…")
        self.lbl_detail.setText("")
        workers = max(2, min(6, os.cpu_count() or 2))
        self.worker = ScanWorker(self.root, self.chk_recursive.isChecked(),
                                 SENSITIVITY[self.cmb_sens.currentIndex()][1], workers)
        self.worker.status.connect(self.lbl_status.setText)
        self.worker.progress.connect(self.on_progress)
        self.worker.result.connect(self.on_result)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.on_worker_finished)
        self.t0 = time.time()
        self.prog_row.show()
        self.progress.setRange(0, 0)
        self.btn_scan.setText("■  중지")
        for w in (self.btn_folder, self.chk_recursive, self.cmb_sens, self.btn_apply, self.btn_undo):
            w.setEnabled(False)
        self.worker.start()

    def on_progress(self, done, total):
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        if done:
            el = time.time() - self.t0
            eta = el / done * (total - done)
            self.lbl_status.setText(f"{done} / {total}장  ·  남은 시간 약 {fmt_secs(eta)}")
        else:
            self.lbl_status.setText(f"0 / {total}장")

    def on_result(self, r):
        self.model.append(r)
        if len(self.model.all) % 5 == 0:
            self.refresh_counts()
        if self.current is None and self.model.rowCount():
            self.select_row(0)

    def on_done(self, results, stopped):
        self.model.set_results(results)
        self.refresh_counts()
        c = self.model.counts()
        el = time.time() - self.t0
        msg = (f"{'중지됨 · ' if stopped else ''}{len(results)}장 분석 완료 ({fmt_secs(el)})")
        self.lbl_status.setText(msg)
        self.select_row(0)
        if not stopped and (c["reject"] + c["review"] + c["burst"]) == 0:
            QMessageBox.information(self, APP_TITLE, "문제가 있는 사진을 찾지 못했습니다. 모두 정상이에요!")

    def on_failed(self, msg):
        self.lbl_status.setText("")
        if not self.model.all:
            self.stack.setCurrentIndex(0)
        QMessageBox.warning(self, APP_TITLE, msg)

    def on_worker_finished(self):
        self.worker = None
        self.btn_scan.setText("▶  다시 분석" if self.model.all else "▶  분석 시작")
        self.btn_scan.setEnabled(True)
        for w in (self.btn_folder, self.chk_recursive, self.cmb_sens):
            w.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        QTimer.singleShot(2500, lambda: self.worker is None and self.prog_row.hide())
        self.refresh_counts()

    # -- 목록 / 상세 ------------------------------------------------------
    def thumb_pixmap(self, r: pc.PhotoResult) -> QPixmap | None:
        if not r.thumb or self.root is None:
            return None
        dest = self.root / pc.OUT_DIR_NAME / "thumbs" / r.thumb
        key = str(dest)
        pm = QPixmapCache.find(key)
        if pm is None or pm.isNull():
            pm = QPixmap(key)
            if pm.isNull():
                self.thumbs.request(self.photo_path(r), dest, r.faces, r.work_size)
                return None
            QPixmapCache.insert(key, pm)
        return pm

    def on_thumb_ready(self, key, ok):
        self.thumbs.pending.discard(key)
        if not ok:
            self.thumbs.failed.add(key)
        QPixmapCache.remove(key)
        self.view.viewport().update()

    def is_moved(self, r: pc.PhotoResult) -> bool:
        return r.path in self.moved

    def photo_path(self, r: pc.PhotoResult) -> Path:
        return self.root / self.moved.get(r.path, r.path)

    def set_filter(self, key):
        cur = self.current
        self.filter_btns[key].setChecked(True)
        self.model.set_filter(key)
        if cur is not None and self.model.matches(cur):
            self.select_result(cur)
        else:
            self.select_row(0)

    def refresh_counts(self):
        c = self.model.counts()
        total = len(self.model.all)
        self.filter_btns["all"].setText(f"전체  {total}")
        for v in VERDICTS:
            self.filter_btns[v].setText(f"{pc.VERDICT_KO[v]}  {c[v]}")
        if total:
            moved = len(self.moved)
            self.lbl_summary.setText(
                f"불량 {c['reject']} · 확인필요 {c['review']} · 연사중복 {c['burst']} · 정상 {c['keep']}"
                + (f"   |   {moved}장 정리됨" if moved else ""))
        else:
            self.lbl_summary.setText("")
        has = total > 0 and self.worker is None
        pending = sum(1 for r in self.model.all if r.verdict != "keep" and not self.is_moved(r))
        self.btn_apply.setEnabled(has and pending > 0)
        self.btn_undo.setEnabled(self.worker is None and bool(self.moved))
        self.btn_report.setEnabled(has)

    def select_row(self, row):
        n = self.model.rowCount()
        if n == 0:
            self.current = None
            self.preview.set_photo(None, None, "이 분류에 사진이 없습니다" if self.model.all else "")
            self.lbl_detail.setText("")
            self.update_detail_buttons()
            return
        row = max(0, min(row, n - 1))
        idx = self.model.index(row)
        self.view.selectionModel().setCurrentIndex(
            idx, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.view.scrollTo(idx)

    def select_result(self, r):
        for row, i in enumerate(self.model.rows):
            if self.model.all[i] is r:
                self.select_row(row)
                return

    def selected_results(self):
        rs = [ix.data(Qt.ItemDataRole.UserRole) for ix in self.view.selectionModel().selectedIndexes()]
        if not rs and self.current is not None:
            rs = [self.current]
        return [r for r in rs if r is not None]

    def update_detail_buttons(self):
        on = bool(self.selected_results())
        for b in self.verdict_btns.values():
            b.setEnabled(on)
        self.btn_open.setEnabled(self.current is not None)
        self.btn_delete.setEnabled(on and self.worker is None)

    def show_current(self):
        idx = self.view.currentIndex()
        r = idx.data(Qt.ItemDataRole.UserRole) if idx.isValid() else None
        self.current = r
        self.update_detail_buttons()
        if r is None:
            return
        path = self.photo_path(r)
        key = str(path)
        img = self.preview_cache.get(key)
        if img is None:
            img = load_qimage(path)
            if img is not None:
                if len(self.preview_cache) > 12:
                    self.preview_cache.pop(next(iter(self.preview_cache)))
                self.preview_cache[key] = img
        self.preview.set_photo(img, r, "미리보기를 열 수 없습니다")
        self.lbl_detail.setText(self.detail_html(r))

    def detail_html(self, r: pc.PhotoResult) -> str:
        esc = __import__("html").escape
        meta = []
        if r.taken:
            meta.append("촬영 " + r.taken.rstrip("~").replace("T", " ")[:16])
        if r.n_main:
            meta.append(f"얼굴 {r.n_main}명")
        meta.append(f"점수 {r.score:.0f}")
        if self.is_moved(r):
            meta.append("이동됨 → " + esc(self.moved[r.path]))
        badge = (f"<span style='background:{COLORS[r.verdict]}; color:white; font-weight:600;'>"
                 f"&nbsp;{pc.VERDICT_KO[r.verdict]}&nbsp;</span>")
        if r.manual:
            badge += " <span style='color:gray'>(직접 변경)</span>"
        items = []
        for _, sev, text in r.issues:
            items.append(f"<li style='color:{SEV_COLORS.get(sev, '#888')}'>{esc(text)}</li>")
        if r.burst_group:
            if r.burst_best == r.path:
                items.append(f"<li style='color:gray'>연사 묶음 #{r.burst_group} · 이 묶음의 베스트</li>")
            else:
                items.append(f"<li style='color:{SEV_COLORS['burst']}'>연사 묶음 #{r.burst_group} · "
                             f"<a href='best'>더 나은 컷 보기: {esc(Path(r.burst_best).name)}</a></li>")
        if not items:
            items.append("<li style='color:gray'>문제를 찾지 못했습니다</li>")
        return (f"<div style='font-size:14px'><b>{esc(r.path)}</b> &nbsp;{badge}</div>"
                f"<div style='color:gray'>{' · '.join(meta)}</div>"
                f"<ul style='margin-left:-20px'>{''.join(items)}</ul>")

    def on_link(self, link):
        r = self.current
        if link == "best" and r is not None:
            best = next((x for x in self.model.all if x.path == r.burst_best), None)
            if best is not None:
                if not self.model.matches(best):
                    self.set_filter("all")
                self.select_result(best)

    def set_verdict(self, v):
        if self.worker is not None:
            return
        rs = self.selected_results()
        if not rs:
            return
        row = self.view.currentIndex().row()
        for r in rs:
            if r.verdict != v:
                r.verdict = v
                r.manual = True
        if self.model.filter != "all" and any(not self.model.matches(r) for r in rs):
            self.model.drop_unmatched()
            self.select_row(row)  # 다음 사진으로 자동 이동
        else:
            self.view.viewport().update()
            if len(rs) == 1:
                self.select_row(row + 1)
            self.show_current()
        self.refresh_counts()
        self.save_timer.start()

    def open_original(self):
        if self.current is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.photo_path(self.current))))

    def open_report(self):
        if self.root is None:
            return
        self.save_now()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root / pc.OUT_DIR_NAME / "report.html")))

    def save_now(self):
        if self.root is not None and self.model.all and self.worker is None:
            try:
                pc.save_reports(self.root, self.model.all, pc.Thresholds())
            except Exception:  # noqa: BLE001
                pass

    # -- 정리 / 되돌리기 --------------------------------------------------
    def apply(self):
        if self.root is None or not self.model.all:
            return
        counts = {v: 0 for v in VERDICTS}
        for r in self.model.all:
            if not self.is_moved(r):
                counts[r.verdict] += 1
        dlg = ApplyDialog(self, counts, self.root)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.categories():
            return
        self.save_now()
        verdicts = {r.path: r.verdict for r in self.model.all if not self.is_moved(r)}
        try:
            res = pc.apply_moves(self.root, verdicts, dlg.categories())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_TITLE, f"옮기는 중 문제가 생겼습니다:\n{e}\n\n"
                                "옮겨진 사진은 [되돌리기]로 원래대로 돌릴 수 있습니다.")
            self.load_existing(self.root, self.model.all)
            return
        self.moved.update(res["map"])
        self.preview_cache.clear()
        self.view.viewport().update()
        self.refresh_counts()
        self.show_current()
        n = sum(res["moved"].values())
        box = QMessageBox(self)
        box.setWindowTitle(APP_TITLE)
        box.setText(f"{n}장을 정리했습니다.\n"
                    + "\n".join(f"  {pc.MOVE_FOLDERS[k]}: {v}장" for k, v in res["moved"].items() if v))
        open_btn = box.addButton("폴더 열기", QMessageBox.ButtonRole.ActionRole)
        box.addButton("확인", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root / pc.OUT_DIR_NAME)))

    def undo(self):
        if self.root is None or not self.moved:
            return
        ans = QMessageBox.question(self, APP_TITLE, f"정리한 사진 {len(self.moved)}장을 원래 자리로 되돌릴까요?")
        if ans != QMessageBox.StandardButton.Yes:
            return
        n, left = pc.undo_moves(self.root)
        self.load_existing(self.root, self.model.all)
        self.preview_cache.clear()
        QMessageBox.information(self, APP_TITLE, f"{n}장을 되돌렸습니다."
                                + (f"\n{left}장은 되돌리지 못했습니다(같은 이름의 파일이 이미 있음)." if left else ""))

    # -- 삭제 (Del) -------------------------------------------------------
    def can_trash(self, path: Path) -> bool:
        """이 드라이브에 휴지통이 있는지. Windows 에서 USB 메모리·SD 카드·네트워크 드라이브는
        휴지통이 없어서 지우면 바로 영구 삭제된다."""
        if sys.platform != "win32":
            return True
        try:
            import ctypes
            drive = os.path.splitdrive(str(path))[0]
            if not drive:
                return True
            return ctypes.windll.kernel32.GetDriveTypeW(drive.rstrip("\\") + "\\") == 3  # DRIVE_FIXED
        except Exception:  # noqa: BLE001
            return True

    def delete_selected(self):
        if self.worker is not None or self.root is None:
            return
        rs = self.selected_results()
        if not rs:
            return
        trash = self.can_trash(self.photo_path(rs[0]))
        if not trash and str(self.root) not in self.permanent_ok:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(APP_TITLE)
            box.setText("이 드라이브는 휴지통을 쓸 수 없습니다 (USB 메모리·SD 카드·네트워크 드라이브).\n"
                        "삭제하면 바로 <b>영구 삭제</b>되어 되돌릴 수 없습니다.".replace("\n", "<br>"))
            box.setInformativeText(f"{len(rs)}장을 영구 삭제할까요?")
            again = QCheckBox("이 폴더에서는 다시 묻지 않기")
            box.setCheckBox(again)
            yes = box.addButton("영구 삭제", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not yes:
                return
            if again.isChecked():
                self.permanent_ok.add(str(self.root))
        elif len(rs) > 1:
            ans = QMessageBox.question(self, APP_TITLE, f"선택한 사진 {len(rs)}장을 휴지통으로 보낼까요?")
            if ans != QMessageBox.StandardButton.Yes:
                return
        self.delete_results(rs)

    def delete_results(self, rs) -> int:
        """사진 파일을 휴지통으로 보내고 목록에서 뺀다. 지운 장수를 돌려준다."""
        row = self.view.currentIndex().row()
        permanent = not self.can_trash(self.photo_path(rs[0]))
        done, failed, entries = [], [], []
        prog = None
        if len(rs) > 10:
            prog = QProgressDialog("사진을 지우는 중…", "중지", 0, len(rs), self)
            prog.setWindowModality(Qt.WindowModality.WindowModal)
            prog.setMinimumDuration(300)
        for k, r in enumerate(rs):
            if prog is not None:
                prog.setValue(k)
                if prog.wasCanceled():
                    break
            path = self.photo_path(r)
            ok = True
            if path.exists():
                res = QFile.moveToTrash(str(path))
                ok = bool(res[0] if isinstance(res, tuple) else res)
                if not ok and permanent:  # 휴지통이 없는 드라이브 (사용자가 영구 삭제를 허락함)
                    try:
                        path.unlink()
                        ok = True
                    except OSError:
                        ok = False
            if ok:
                done.append(r)
                entries.append({"path": r.path, "file": str(path), "verdict": r.verdict,
                                "deleted_at": datetime.now().isoformat(timespec="seconds"),
                                "to_trash": not permanent})
            else:
                failed.append(r)
        if prog is not None:
            prog.setValue(len(rs))
        if done:
            self.remove_results(done)
            try:
                pc.log_deleted(self.root, entries)
            except Exception:  # noqa: BLE001
                pass
            self.select_row(row)
            where = "영구 삭제했습니다" if permanent else "휴지통으로 보냈습니다 (휴지통에서 복원할 수 있어요)"
            self.flash(f"🗑  {len(done)}장을 {where}")
        if failed and not self.quiet:
            QMessageBox.warning(self, APP_TITLE, f"{len(failed)}장은 지우지 못했습니다.\n"
                                "다른 프로그램에서 열려 있지 않은지 확인해 주세요.\n\n"
                                + "\n".join(r.path for r in failed[:10]))
        return len(done)

    def remove_results(self, done):
        paths = {r.path for r in done}
        best_gone = {r.burst_group for r in done if r.burst_group and r.burst_best == r.path}
        self.model.remove({id(r) for r in done})
        # 연사 묶음의 베스트를 지웠으면 남은 것 중 가장 좋은 컷을 새 베스트로
        for g in best_gone:
            members = [x for x in self.model.all if x.burst_group == g]
            if not members:
                continue
            best = max(members, key=lambda x: x.score)
            for m in members:
                m.burst_best = best.path
            if best.verdict == "burst" and not best.manual:
                best.verdict = "keep"
        moved = [p for p in paths if p in self.moved]
        for p in moved:
            self.moved.pop(p)
        if moved:
            try:
                pc.forget_moves(self.root, moved)
            except Exception:  # noqa: BLE001
                pass
        if self.current is not None and self.current.path in paths:
            self.current = None
        self.preview_cache.clear()
        self.refresh_counts()
        self.save_timer.start()

    def flash(self, msg):
        self.lbl_summary.setText(msg)
        self.lbl_summary.setStyleSheet("color: #2f9a57; font-weight: 600;")
        self.flash_timer.start()

    def unflash(self):
        self.lbl_summary.setStyleSheet("")
        self.refresh_counts()

    def closeEvent(self, e):  # noqa: N802
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(15000)
        self.thumbs.clear()
        self.thumbs.pool.waitForDone(5000)
        self.save_now()
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(e)


# ----------------------------------------------------------------------------
# 자가 점검 (빌드 검증용): 창을 띄우고 폴더를 분석한 뒤 요약을 JSON으로 저장
# ----------------------------------------------------------------------------
def selftest(folder: str, out: str) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    w = MainWindow()
    w.quiet = True
    w.show()
    app.processEvents()
    root = Path(folder).resolve()
    summary = {"ok": False}
    try:
        pc.ensure_models(log=lambda *_: None)
        files = pc.find_images(root, True)
        th = pc.Thresholds()
        thumbs = root / pc.OUT_DIR_NAME / "thumbs"
        thumbs.mkdir(parents=True, exist_ok=True)
        results = []
        pc.analyze_threaded(root, files, th, thumbs, 2, results.append)
        pc.group_bursts(results, th)
        pc.save_reports(root, results, th)
        w.load_existing(root, results)
        app.processEvents()
        errors = [r.error for r in results if not r.ok]
        thumbs_ok = all((thumbs / r.thumb).exists() for r in results if r.ok)
        # 썸네일이 없을 때 다시 만들어지는지
        r0 = results[0]
        (thumbs / r0.thumb).unlink()
        QPixmapCache.clear()
        w.thumb_pixmap(r0)
        w.thumbs.pool.waitForDone(20000)
        app.processEvents()
        regen_ok = (thumbs / r0.thumb).exists() and w.thumb_pixmap(r0) is not None
        # 삭제(휴지통)가 되는지
        victim = results[-1]
        vpath = w.photo_path(victim)
        n_before = len(w.model.all)
        deleted = w.delete_results([victim])
        delete_ok = deleted == 1 and not vpath.exists() and len(w.model.all) == n_before - 1
        summary = {"ok": len(results) == len(files) > 0 and not errors and thumbs_ok and regen_ok and delete_ok,
                   "photos": len(results), "counts": w.model.counts(), "errors": errors,
                   "faces": sum(r.n_main for r in results), "thumbs_ok": thumbs_ok,
                   "thumb_regen_ok": regen_ok, "delete_ok": delete_ok, "trash_drive": w.can_trash(vpath)}
    except Exception as e:  # noqa: BLE001
        summary = {"ok": False, "error": f"{e}\n{traceback.format_exc()}"}
    Path(out).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    w.close()
    return 0 if summary.get("ok") else 1


def main():
    if len(sys.argv) >= 4 and sys.argv[1] == "--selftest":
        sys.exit(selftest(sys.argv[2], sys.argv[3]))
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
