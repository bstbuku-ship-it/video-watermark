"""
视频批量加水印工具 v3
依赖: pip install PyQt6
外部依赖: ffmpeg.exe / ffprobe.exe（放同目录或加入系统 PATH）
运行: pythonw video_watermark.py
"""

APP_VERSION  = "v1.5.2"
REPO         = "bstbuku-ship-it/video-watermark"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
API_URL      = f"https://api.github.com/repos/{REPO}/releases/latest"

import sys, os, subprocess, platform, re, base64, tempfile, hashlib
import urllib.request, urllib.error, json
from PyQt6.QtCore import QSettings
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QLineEdit, QSlider, QFileDialog,
    QProgressBar, QComboBox, QColorDialog, QFrame,
    QGridLayout, QMessageBox, QSizePolicy, QScrollArea,
    QSplashScreen, QDialog, QTextEdit
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QIcon, QDragEnterEvent, QDropEvent, QPixmap, QPainter, QFont, QLinearGradient

NO_WINDOW  = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
SUPPORTED  = {".mp4",".mkv",".mov",".avi",".wmv",".flv",".webm",".m4v",".ts"}
# 统一输出格式：这些容器均可与当前 H.264 编码流程配合使用；默认 MP4。
OUTPUT_FORMATS = [
    ("MP4  (.mp4)", ".mp4"),
    ("MKV  (.mkv)", ".mkv"),
    ("MOV  (.mov)", ".mov"),
    ("M4V  (.m4v)", ".m4v"),
    ("TS  (.ts)", ".ts"),
    ("FLV  (.flv)", ".flv"),
]
TIME_RE    = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d\d)")
SPEED_RE   = re.compile(r"speed=\s*([\d.]+)x")

class NoScrollCombo(QComboBox):
    """禁止鼠标滚轮切换选项"""
    def wheelEvent(self, e): e.ignore()

class NoScrollSlider(QSlider):
    """禁止鼠标滚轮调节值"""
    def wheelEvent(self, e): e.ignore()


FONTS = {
    "Arial":    "C:/Windows/Fonts/arial.ttf",
    "Segoe UI": "C:/Windows/Fonts/segoeui.ttf",
    "Calibri":  "C:/Windows/Fonts/calibri.ttf",
}

ENCODER_PROFILES = {
    "h264_nvenc": {
        "极压  CRF 35":    ["-c:v","h264_nvenc","-preset","p1","-rc","vbr","-cq","35"],
        "压缩  CRF 28":    ["-c:v","h264_nvenc","-preset","p2","-rc","vbr","-cq","28"],
        "快速  CRF 23":    ["-c:v","h264_nvenc","-preset","p2","-rc","vbr","-cq","23"],
        "高质量  CRF 18":  ["-c:v","h264_nvenc","-preset","p4","-rc","vbr","-cq","18"],
        "近乎无损  CRF 10":["-c:v","h264_nvenc","-preset","p6","-rc","vbr","-cq","10"],
    },
    "h264_qsv": {
        "极压  CRF 35":    ["-c:v","h264_qsv","-preset","fast",  "-global_quality","35"],
        "压缩  CRF 28":    ["-c:v","h264_qsv","-preset","fast",  "-global_quality","28"],
        "快速  CRF 23":    ["-c:v","h264_qsv","-preset","fast",  "-global_quality","23"],
        "高质量  CRF 18":  ["-c:v","h264_qsv","-preset","medium","-global_quality","18"],
        "近乎无损  CRF 10":["-c:v","h264_qsv","-preset","slow",  "-global_quality","10"],
    },
    "h264_amf": {
        "极压  CRF 35":    ["-c:v","h264_amf","-quality","speed",   "-rc","vbr_latency","-b:v","3M"],
        "压缩  CRF 28":    ["-c:v","h264_amf","-quality","speed",   "-rc","vbr_latency","-b:v","5M"],
        "快速  CRF 23":    ["-c:v","h264_amf","-quality","speed",   "-rc","vbr_latency","-b:v","8M"],
        "高质量  CRF 18":  ["-c:v","h264_amf","-quality","balanced","-rc","vbr_peak",   "-b:v","12M"],
        "近乎无损  CRF 10":["-c:v","h264_amf","-quality","quality", "-rc","vbr_peak",   "-b:v","20M"],
    },
    "libx264": {
        "极压  CRF 35":    ["-c:v","libx264","-preset","fast",  "-crf","35"],
        "压缩  CRF 28":    ["-c:v","libx264","-preset","fast",  "-crf","28"],
        "快速  CRF 23":    ["-c:v","libx264","-preset","fast",  "-crf","23"],
        "高质量  CRF 18":  ["-c:v","libx264","-preset","medium","-crf","18"],
        "近乎无损  CRF 10":["-c:v","libx264","-preset","medium","-crf","10"],
    },
}
QUALITY_KEYS = ["极压  CRF 35", "压缩  CRF 28", "快速  CRF 23", "高质量  CRF 18", "近乎无损  CRF 10"]

POSITIONS = {
    "左上角": lambda m: (str(m), str(m)),
    "右上角": lambda m: ("W-tw-"+str(m), str(m)),
    "左下角": lambda m: (str(m), "H-th-"+str(m)),
    "右下角": lambda m: ("W-tw-"+str(m), "H-th-"+str(m)),
}

# 主题调色板：日间 / 夜间 各控件用色统一在此维护，避免局部写死颜色导致切主题后失效
PALETTE = {
    True: dict(   # 夜间
        window_bg="#1e1e1e", panel_bg="#252525", bottom_bg="#252525",
        text_title="#ffffff", text_body="#dddddd", text_secondary="#aaaaaa",
        text_muted="#888888", text_faint="#666666",
        border="#3a3a3a", divider="#2a2a2a",
        input_bg="#2a2a2a", input_text="#dddddd",
        row_bg="#272727", row_border="transparent",
        drop_border="#3a3a3a", drop_bg="rgba(255,255,255,0.02)",
        drop_text="#777777", drop_border_active="#3498DB", drop_bg_active="rgba(52,152,219,0.07)",
        scrollbar_bg="#1e1e1e", scrollbar_handle="#3a3a3a",
        tag_bg="#2a2a2a", tag_fg="#777777",
        btn_bg="#2a2a2a", btn_border="#3a3a3a", btn_fg="#666666", btn_fg_hover="#aaaaaa",
        status_wait_bg="#333333", status_wait_fg="#888888",
        pos_idle_bg="#2a2a2a", pos_idle_border="#3a3a3a", pos_idle_fg="#777777", pos_idle_fg_hover="#bbbbbb",
        clear_bg="#4a2020", clear_border="#e74c3c", clear_fg="#ff6b61", clear_hover="#632626",
    ),
    False: dict(  # 日间
        window_bg="#f0ede8", panel_bg="#e8e4de", bottom_bg="#e8e4de",
        text_title="#1a1a1a", text_body="#1a1a1a", text_secondary="#333333",
        text_muted="#555555", text_faint="#666666",
        border="#cccccc", divider="#d6d1c8",
        input_bg="#ffffff", input_text="#1a1a1a",
        row_bg="#ffffff", row_border="#ddd8cf",
        drop_border="#c7c1b6", drop_bg="rgba(0,0,0,0.02)",
        drop_text="#5a5a5a", drop_border_active="#3498DB", drop_bg_active="rgba(52,152,219,0.08)",
        scrollbar_bg="#f0ede8", scrollbar_handle="#cccccc",
        tag_bg="#ddd8cf", tag_fg="#4a4a4a",
        btn_bg="#ffffff", btn_border="#ccc", btn_fg="#4a4a4a", btn_fg_hover="#1a1a1a",
        status_wait_bg="#e2ddd3", status_wait_fg="#5a5a5a",
        pos_idle_bg="#ffffff", pos_idle_border="#ccc", pos_idle_fg="#555555", pos_idle_fg_hover="#1a1a1a",
        clear_bg="#fff0f0", clear_border="#e74c3c", clear_fg="#c0392b", clear_hover="#ffe0e0",
    ),
}


def elide_filename(name, max_chars=26):
    """长文件名中间省略号，保留前后片段与后缀，避免撑破固定宽度布局"""
    p = Path(name)
    stem, suf = p.stem, p.suffix
    full = stem + suf
    if len(full) <= max_chars:
        return full
    keep = max(max_chars - len(suf) - 3, 6)
    head = (keep + 1) // 2
    tail = keep - head
    head = max(head, 3)
    tail = max(tail, 3)
    return f"{stem[:head]}...{stem[-tail:]}{suf}"

_ICON_B64 = "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"


def _find_bin(name):
    exe = name + (".exe" if platform.system() == "Windows" else "")
    candidates = []
    if getattr(sys, "frozen", False):
        # onefile: 解压到临时目录 _MEIPASS
        if hasattr(sys, "_MEIPASS"):
            candidates.append(Path(sys._MEIPASS) / exe)
        # onedir: exe 同目录
        candidates.append(Path(sys.executable).parent / exe)
    else:
        candidates.append(Path(__file__).parent / exe)
    for p in candidates:
        if p.exists():
            return str(p)
    return name  # 回退到系统 PATH


def detect_encoder():
    """
    检测可用硬件编码器。
    第一步：查 ffmpeg -encoders 确认编码器存在。
    第二步：用最小参数实际编码 0.1 秒验证可用。
    RTX 50 / 新驱动 兼容。
    """
    ff = _find_bin("ffmpeg")

    # 先拿到所有可用编码器列表
    try:
        r = subprocess.run([ff, "-encoders"], capture_output=True,
                           text=True, timeout=5, creationflags=NO_WINDOW)
        encoder_list = r.stdout + r.stderr
    except Exception:
        encoder_list = ""

    candidates = []
    if "h264_nvenc" in encoder_list:
        candidates.append("h264_nvenc")
    if "h264_qsv" in encoder_list:
        candidates.append("h264_qsv")
    if "h264_amf" in encoder_list:
        candidates.append("h264_amf")

    for enc in candidates:
        try:
            # 用 nullsrc 避免 lavfi color 在某些版本上的兼容问题
            r = subprocess.run(
                [ff,
                 "-f", "lavfi", "-i", "nullsrc=s=320x240:d=0.1",
                 "-vf", "format=yuv420p",
                 "-c:v", enc,
                 "-frames:v", "1",
                 "-f", "null", "-"],
                capture_output=True, timeout=10, creationflags=NO_WINDOW)
            # returncode 0 = 成功；同时排除 "No NVENC capable devices" 类错误
            stderr_out = r.stderr.decode(errors="replace") if isinstance(r.stderr, bytes) else r.stderr
            if r.returncode == 0 and "No capable" not in stderr_out and "Cannot load" not in stderr_out:
                return enc
        except Exception:
            pass

    return "libx264"


def get_duration_ms(path):
    try:
        r = subprocess.run(
            [_find_bin("ffprobe"),"-v","error",
             "-show_entries","format=duration",
             "-of","default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=3, creationflags=NO_WINDOW)
        val = r.stdout.strip()
        return float(val) * 1000 if val else 0.0
    except Exception:
        return 0.0


def parse_time_ms(line):
    m = TIME_RE.search(line)
    if not m: return None
    h,mi,s,cs = int(m.group(1)),int(m.group(2)),int(m.group(3)),int(m.group(4))
    return (h*3600 + mi*60 + s) * 1000 + cs * 10


def parse_speed(line):
    m = SPEED_RE.search(line)
    return m.group(1) if m else None


class UpdateChecker(QThread):
    # tag, release page, changelog, setup asset url, asset name, sha256 digest
    result = pyqtSignal(str, str, str, str, str, str)
    error  = pyqtSignal(str)

    def run(self):
        try:
            req = urllib.request.Request(
                API_URL, headers={"User-Agent": "video-watermark-updater"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())

            tag  = data.get("tag_name", "")
            url  = data.get("html_url", RELEASES_URL)
            body = (data.get("body") or "").strip()

            setup_url = ""
            setup_name = ""
            setup_digest = ""
            for asset in data.get("assets", []) or []:
                name = str(asset.get("name", ""))
                if name.lower().endswith("-setup.exe") and name.lower().startswith("video-watermark-v"):
                    setup_url = str(asset.get("browser_download_url", "") or "")
                    setup_name = name
                    digest = str(asset.get("digest", "") or "")
                    if digest.lower().startswith("sha256:"):
                        setup_digest = digest.split(":", 1)[1].strip().lower()
                    break

            self.result.emit(tag, url, body, setup_url, setup_name, setup_digest)
        except Exception as e:
            self.error.emit(str(e))


class UpdateDownloadWorker(QThread):
    """后台下载新版安装包，不阻塞主界面。"""
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, url, expected_sha256="", filename="video-watermark-update-Setup.exe"):
        super().__init__()
        self.url = url
        self.expected_sha256 = (expected_sha256 or "").lower().strip()
        self.filename = filename or "video-watermark-update-Setup.exe"

    def run(self):
        try:
            update_dir = Path(tempfile.gettempdir()) / "VideoWatermarkUpdate"
            update_dir.mkdir(parents=True, exist_ok=True)
            target = update_dir / self.filename
            temp_path = target.with_suffix(target.suffix + ".download")

            req = urllib.request.Request(
                self.url, headers={"User-Agent": "video-watermark-updater"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length", "0") or 0)
                downloaded = 0
                digest = hashlib.sha256()

                with open(temp_path, "wb") as f:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        f.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        pct = int(downloaded * 100 / total) if total > 0 else 0
                        self.progress.emit(pct, downloaded)

            actual = digest.hexdigest().lower()
            if self.expected_sha256 and actual != self.expected_sha256:
                try:
                    temp_path.unlink()
                except Exception:
                    pass
                raise RuntimeError("更新文件校验失败，已停止安装。")

            os.replace(str(temp_path), str(target))
            self.progress.emit(100, downloaded)
            self.finished.emit(str(target))
        except Exception as e:
            self.error.emit(str(e))


class StartupCheckWorker(QThread):
    """后台完成 FFmpeg / 编码器检测，避免启动阶段阻塞 Qt 主线程。"""
    finished = pyqtSignal(bool, str, str)  # ffmpeg_ok, version, encoder

    def run(self):
        ff = _find_bin("ffmpeg")
        version = ""
        ffmpeg_ok = False
        try:
            r = subprocess.run(
                [ff, "-version"], capture_output=True, text=True,
                timeout=5, creationflags=NO_WINDOW)
            if r.returncode == 0:
                ffmpeg_ok = True
                first = (r.stdout or "").splitlines()[0] if r.stdout else ""
                m = re.search(r"version\s+([^\s]+)", first)
                version = m.group(1) if m else ""
        except Exception:
            pass

        encoder = "libx264"
        if ffmpeg_ok:
            try:
                encoder = detect_encoder()
            except Exception:
                encoder = "libx264"
        self.finished.emit(ffmpeg_ok, version, encoder)


class WatermarkWorker(QThread):
    progress         = pyqtSignal(int, int, str)
    file_done        = pyqtSignal(int, bool, str)
    all_done         = pyqtSignal()
    encoder_detected = pyqtSignal(str)

    def __init__(self, tasks, params):
        super().__init__()
        self.tasks  = tasks
        self.params = params
        self._stop  = False

    def stop(self): self._stop = True

    def run(self):
        manual = self.params.get("manual_encoder")
        encoder = manual if manual else detect_encoder()
        self.encoder_detected.emit(encoder)

        p = self.params
        # 转义水印文字
        text = p["text"].replace("\\","\\\\").replace("'","\\'").replace(":","\\:")

        color_hex = p["color"].lstrip("#")
        opacity   = p["opacity"] / 100.0
        margin    = p["margin"]
        x_expr, y_expr = POSITIONS[p["position"]](margin)
        enc_args  = ENCODER_PROFILES[encoder][p["quality"]]

        # 字体文件路径（FFmpeg drawtext 冒号需转义）
        font_file = FONTS.get(p["font"], "C:/Windows/Fonts/arial.ttf")
        font_ff   = font_file.replace(":", "\\:")
        fs  = str(p["font_size"])
        op  = f"{opacity:.2f}"

        # 描边参数
        border_part = ""
        if p.get("border_on"):
            bw = p.get("border_w", 2)
            border_part = f":borderw={bw}:bordercolor=0x000000@0.75"

        # 背景块参数（颜色可选白/黑，透明度与水印文字透明度一致）
        bg_part = ""
        if p.get("bg_on"):
            bg_hex = p.get("bg_color", "#000000").lstrip("#")
            bg_part = f":box=1:boxcolor=0x{bg_hex}@{op}:boxborderw=6"

        vf  = (f"drawtext=text='{text}':fontfile='{font_ff}':"
               f"fontsize={fs}:fontcolor=0x{color_hex}@{op}:"
               f"x={x_expr}:y={y_expr}:"
               f"shadowcolor=black@0.45:shadowx=1:shadowy=1"
               + border_part + bg_part)
        for idx, task in enumerate(self.tasks):
            if self._stop: break
            inp, out = task["input"], task["output"]
            out_dir  = os.path.dirname(out)
            if out_dir: os.makedirs(out_dir, exist_ok=True)
            dur_ms = get_duration_ms(inp)
            cmd = [_find_bin("ffmpeg"),"-y","-i",inp,"-vf",vf] + enc_args + ["-c:a","copy",out]
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=NO_WINDOW, bufsize=1)
                last_pct = 0
                for line in proc.stderr:
                    if self._stop: proc.kill(); break
                    t   = parse_time_ms(line)
                    spd = parse_speed(line)
                    if t is not None and dur_ms > 0:
                        pct = min(int(t / dur_ms * 100), 99)
                        if pct != last_pct:
                            last_pct = pct
                            self.progress.emit(idx, pct, spd or "")
                ret = proc.wait()
                if ret == 0:
                    self.progress.emit(idx, 100, "")
                    self.file_done.emit(idx, True, "完成")
                else:
                    self.file_done.emit(idx, False, f"FFmpeg 返回错误码 {ret}")
            except FileNotFoundError:
                self.file_done.emit(idx, False,
                    "未找到 ffmpeg.exe\n请将 ffmpeg.exe 和 ffprobe.exe 放到程序同目录")
                break
            except Exception as e:
                self.file_done.emit(idx, False, str(e))
        self.all_done.emit()


class DropZone(QLabel):
    files_dropped = pyqtSignal(list)
    def __init__(self, dark=True):
        super().__init__()
        self._dark = dark
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText("将视频文件拖拽到此处\n或点击选择文件\n\nMP4  MKV  MOV  AVI  WMV 等格式")
        self.setMinimumHeight(110)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._s(False)
    def set_theme(self, dark):
        self._dark = dark
        self._s(False)
    def _s(self, h):
        pal = PALETTE[self._dark]
        c  = pal["drop_border_active"] if h else pal["drop_border"]
        bg = pal["drop_bg_active"] if h else pal["drop_bg"]
        self.setStyleSheet(f"QLabel{{border:2px dashed {c};border-radius:10px;background:{bg};color:{pal['drop_text']};font-size:13px;padding:14px;}}")
    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls(): e.acceptProposedAction(); self._s(True)
    def dragLeaveEvent(self, e): self._s(False)
    def dropEvent(self, e: QDropEvent):
        self._s(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls()
                 if Path(u.toLocalFile()).suffix.lower() in SUPPORTED]
        if paths: self.files_dropped.emit(paths)
    def mousePressEvent(self, e):
        paths, _ = QFileDialog.getOpenFileNames(self,"选择视频文件","",
            "Videos (*.mp4 *.mkv *.mov *.avi *.wmv *.flv *.webm *.m4v *.ts)")
        valid = [p for p in paths if Path(p).suffix.lower() in SUPPORTED]
        if valid: self.files_dropped.emit(valid)


class FileRowWidget(QWidget):
    remove_clicked = pyqtSignal()
    def __init__(self, filepath, dark=True):
        super().__init__()
        self.filepath = filepath
        self._dark = dark
        self._state = "wait"
        self._build()
    def _build(self):
        p  = Path(self.filepath)
        vl = QVBoxLayout(self)
        vl.setContentsMargins(10,8,10,8); vl.setSpacing(3)
        top = QHBoxLayout(); top.setSpacing(8)
        self.icon = QLabel("▶"); self.icon.setFixedWidth(18)
        self.icon.setStyleSheet("color:#3498DB;font-size:13px;")
        # 长文件名中间省略，前后片段+后缀保留，避免撑破布局需要左右滑动
        self.name = QLabel(elide_filename(p.name))
        self.name.setToolTip(p.name)
        self.name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.name.setMinimumWidth(0)
        self.status = QLabel("等待"); self.status.setFixedWidth(72)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rm = QPushButton("✕"); rm.setFixedSize(18,18)
        self.rm_btn = rm
        rm.clicked.connect(self.remove_clicked)
        top.addWidget(self.icon); top.addWidget(self.name); top.addWidget(self.status); top.addWidget(rm)
        sz   = self._fmt(p.stat().st_size) if p.exists() else ""
        self.info = QLabel(f"{sz}  ·  {p.suffix.upper().lstrip('.')}  ·  {str(p.parent)[:48]}")
        self.info.setStyleSheet("font-size:11px;margin-left:26px;")
        self.bar = QProgressBar()
        self.bar.setRange(0,100); self.bar.setValue(0)
        self.bar.setTextVisible(False); self.bar.setFixedHeight(3)
        self.bar.hide()
        vl.addLayout(top); vl.addWidget(self.info); vl.addWidget(self.bar)
        self._apply_theme_styles()
        self._set_s("wait")
    def set_theme(self, dark):
        self._dark = dark
        self._apply_theme_styles()
        self._set_s(self._state)
    def _apply_theme_styles(self):
        pal = PALETTE[self._dark]
        self.setStyleSheet(f"FileRowWidget{{background:{pal['row_bg']};border-radius:8px;border:1px solid {pal['row_border']};}}")
        self.name.setStyleSheet(f"font-size:13px;font-weight:600;color:{pal['text_body']};")
        self.info.setStyleSheet(f"font-size:11px;color:{pal['text_faint']};margin-left:26px;")
        self.rm_btn.setStyleSheet(f"QPushButton{{border:none;color:{pal['text_faint']};background:transparent;}}QPushButton:hover{{color:#e74c3c;}}")
        bar_track = "#2d2d2d" if self._dark else "#e2ddd3"
        chunk = "#27ae60" if self._state == "done" else "#3498DB"
        self.bar.setStyleSheet(f"QProgressBar{{background:{bar_track};border-radius:1px;border:none;}}QProgressBar::chunk{{background:{chunk};border-radius:1px;}}")
    def _set_s(self, state):
        self._state = state
        pal = PALETTE[self._dark]
        if self._dark:
            d = {"wait":("等待", pal["status_wait_bg"], pal["status_wait_fg"]),
                 "run":("","#1a3a5c","#3498DB"),
                 "done":("✓ 完成","#1a3d2b","#27ae60"),
                 "fail":("✗ 失败","#3d1a1a","#e74c3c")}
        else:
            d = {"wait":("等待", pal["status_wait_bg"], pal["status_wait_fg"]),
                 "run":("","#d6e9f8","#1c6ea4"),
                 "done":("✓ 完成","#d8f0e0","#1e8449"),
                 "fail":("✗ 失败","#f9d9d9","#c0392b")}
        txt,bg,fg = d[state]
        if txt: self.status.setText(txt)
        self.status.setStyleSheet(f"font-size:11px;border-radius:10px;padding:2px 4px;background:{bg};color:{fg};")
    def set_progress(self, pct):
        self.bar.show(); self.bar.setValue(pct)
        self._set_s("run"); self.status.setText(f"{pct}%")
    def set_done(self, ok):
        self.bar.setValue(100 if ok else 0)
        self._set_s("done" if ok else "fail")
        self._apply_theme_styles()
    def reset(self):
        self.bar.hide(); self.bar.setValue(0)
        self._set_s("wait")
        self._apply_theme_styles()
    @staticmethod
    def _fmt(b):
        for u in ["B","KB","MB","GB"]:
            if b < 1024: return f"{b:.1f} {u}"
            b //= 1024
        return f"{b:.1f} TB"


class ColorSwatch(QPushButton):
    color_changed = pyqtSignal(str)
    def __init__(self, color="#FFFFFF"):
        super().__init__()
        self.color = color
        self.setFixedSize(28,28)
        self._a()
        self.clicked.connect(self._pick)
    def _a(self):
        self.setStyleSheet(f"QPushButton{{background:{self.color};border:2px solid #555;border-radius:5px;}}QPushButton:hover{{border-color:#999;}}")
    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self, "选择颜色")
        if c.isValid():
            self.color = c.name().upper(); self._a()
            self.color_changed.emit(self.color)
    def get(self): return self.color


class UpdateDialog(QDialog):
    """展示更新日志，并支持在已安装版本中后台下载新版安装包。"""
    def __init__(self, parent, current_ver, latest_ver, changelog, url,
                 setup_url="", setup_digest="", setup_name="",
                 can_auto_update=False, dark=True):
        super().__init__(parent)
        self.setWindowTitle("发现新版本")
        self.setMinimumSize(440, 410)
        self.resize(460, 450)
        self._url = url
        self._setup_url = setup_url
        self._setup_digest = setup_digest
        self._setup_name = setup_name
        self._can_auto_update = bool(can_auto_update and setup_url)
        self._parent_window = parent
        pal = PALETTE[dark]

        vl = QVBoxLayout(self); vl.setContentsMargins(22,20,22,18); vl.setSpacing(12)
        head = QLabel("🎉  发现新版本")
        head.setStyleSheet(f"font-size:16px;font-weight:700;color:{pal['text_title']};")
        vl.addWidget(head)
        ver_row = QLabel(f"当前版本 {current_ver}   →   最新版本 <span style='color:#3498DB;font-weight:700;'>{latest_ver}</span>")
        ver_row.setTextFormat(Qt.TextFormat.RichText)
        ver_row.setStyleSheet(f"font-size:12.5px;color:{pal['text_secondary']};")
        vl.addWidget(ver_row)
        note = QLabel("更新内容：")
        note.setStyleSheet(f"font-size:12px;font-weight:600;color:{pal['text_secondary']};margin-top:4px;")
        vl.addWidget(note)
        self.changelog_box = QTextEdit()
        self.changelog_box.setReadOnly(True)
        self.changelog_box.setPlainText(changelog if changelog else "本次更新未提供详细说明，可前往下载页查看详情。")
        self.changelog_box.setStyleSheet(f"QTextEdit{{background:{pal['input_bg']};color:{pal['input_text']};border:1px solid {pal['border']};border-radius:8px;padding:10px;font-size:12.5px;}}")
        vl.addWidget(self.changelog_box, stretch=1)
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(f"font-size:11px;color:{pal['text_secondary']};")
        self.progress_label.hide()
        vl.addWidget(self.progress_label)
        self.download_bar = QProgressBar()
        self.download_bar.setRange(0,100); self.download_bar.setValue(0)
        self.download_bar.setTextVisible(False); self.download_bar.setFixedHeight(5); self.download_bar.hide()
        self.download_bar.setStyleSheet("QProgressBar{background:#2d2d2d;border:none;border-radius:2px;}QProgressBar::chunk{background:#3498DB;border-radius:2px;}")
        vl.addWidget(self.download_bar)
        btn_row = QHBoxLayout(); btn_row.setSpacing(10)
        self.later_btn = QPushButton("稍后再说")
        self.later_btn.setFixedHeight(36); self.later_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.later_btn.setStyleSheet(f"QPushButton{{background:{pal['btn_bg']};border:1px solid {pal['btn_border']};border-radius:8px;color:{pal['btn_fg']};font-size:13px;}}QPushButton:hover{{color:{pal['btn_fg_hover']};}}")
        self.later_btn.clicked.connect(self.reject)
        self.go_btn = QPushButton("立即更新" if self._can_auto_update else "前往下载")
        self.go_btn.setFixedHeight(36); self.go_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.go_btn.setStyleSheet("QPushButton{background:#3498DB;color:white;border:none;border-radius:8px;font-size:13px;font-weight:600;}QPushButton:hover{background:#2980b9;}")
        if self._can_auto_update: self.go_btn.clicked.connect(self._start_update)
        else: self.go_btn.clicked.connect(self._open_and_close)
        btn_row.addWidget(self.later_btn); btn_row.addWidget(self.go_btn); vl.addLayout(btn_row)
        self.setStyleSheet(f"QDialog{{background:{pal['panel_bg']};}}")

    def _start_update(self):
        self.go_btn.setEnabled(False); self.later_btn.setEnabled(False); self.go_btn.setText("下载中…")
        self.progress_label.setText("正在后台下载更新安装包，请稍候…"); self.progress_label.show()
        self.download_bar.setValue(0); self.download_bar.show()
        self._parent_window._start_self_update(self, self._setup_url, self._setup_digest, self._setup_name)

    def set_download_progress(self, pct, downloaded):
        self.download_bar.setValue(max(0, min(100, pct)))
        mb = downloaded / 1024 / 1024
        self.progress_label.setText(f"正在后台下载更新… {pct}%  ·  已下载 {mb:.1f} MB")

    def set_download_error(self, message):
        self.go_btn.setEnabled(True); self.later_btn.setEnabled(True); self.go_btn.setText("重试更新")
        self.progress_label.setText(f"更新失败：{message}")

    def _open_and_close(self):
        import webbrowser
        webbrowser.open(self._url); self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频批量加水印")
        self.setMinimumSize(1080, 680)
        self.resize(1200, 740)
        self.file_rows    = []
        self.worker       = None
        self._current_pos = "左上角"
        self._done_count  = 0
        self._total       = 0
        self._task_done = False
        self._checking_silent = True
        self._dark = True
        self._bg_color = "#000000"     # 背景块默认颜色（随水印文字颜色自动联动）
        self._sec_labels = []          # 需要跟随主题重新着色的分区小标题
        self._div_frames = []          # 需要跟随主题重新着色的分隔线
        self._build_ui()
        self._theme()
        self._set_icon()
        # 启动阶段只安排后台任务，绝不在 GUI 线程执行 ffmpeg / 编码器探测。
        QTimer.singleShot(0, self._start_startup_checks)
        QTimer.singleShot(2000, self._auto_check_update)
        QTimer.singleShot(50, self._load_settings)  # 启动后静默检测

    def _toggle_theme(self):
        self._dark = not self._dark
        self._apply_theme()

    def _apply_theme(self):
        pal = PALETTE[self._dark]
        self.setStyleSheet(f"""
            QMainWindow,QWidget{{background:{pal['window_bg']};color:{pal['text_body']};
                font-family:'PingFang SC','Microsoft YaHei','Segoe UI',sans-serif;font-size:13px;}}
            QLineEdit,QComboBox{{background:{pal['input_bg']};border:1px solid {pal['border']};border-radius:6px;padding:6px 9px;color:{pal['input_text']};}}
            QLineEdit:focus,QComboBox:focus{{border-color:#3498DB;}}
            QComboBox::drop-down{{border:none;width:20px;}}
            QComboBox QAbstractItemView{{background:{pal['input_bg']};border:1px solid {pal['border']};selection-background-color:#3498DB;padding:4px;color:{pal['input_text']};}}
            QSlider::groove:horizontal{{height:4px;background:{pal['border']};border-radius:2px;}}
            QSlider::handle:horizontal{{width:16px;height:16px;border-radius:8px;background:#3498DB;margin:-6px 0;}}
            QSlider::sub-page:horizontal{{background:#3498DB;border-radius:2px;}}
            QScrollBar:vertical{{background:{pal['scrollbar_bg']};width:5px;border-radius:2px;}}
            QScrollBar::handle:vertical{{background:{pal['scrollbar_handle']};border-radius:2px;min-height:20px;}}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}
        """)

        if hasattr(self, 'theme_btn'):
            if self._dark:
                self.theme_btn.setText("🌙  夜间")
            else:
                self.theme_btn.setText("☀️  日间")
            self.theme_btn.setStyleSheet(
                f"QPushButton{{background:{pal['btn_bg']};border:1px solid {pal['btn_border']};"
                f"border-radius:13px;color:{pal['btn_fg']};font-size:11px;padding:0 10px;}}"
                f"QPushButton:hover{{color:{pal['btn_fg_hover']};}}")

        # 左右面板 + 顶部/底部条
        if self.centralWidget():
            for w in self.findChildren(QWidget):
                if w.objectName() == "leftPanel": w.setStyleSheet(f"background:{pal['window_bg']};")
                if w.objectName() == "rightPanel": w.setStyleSheet(f"background:{pal['panel_bg']};")
                if w.objectName() == "bottomPanel": w.setStyleSheet(f"background:{pal['bottom_bg']};border-top:1px solid {pal['divider']};")

        # 右侧滚动区域及内容容器（此前主题切换时遗漏，导致日间模式右侧仍是夜间背景）
        if hasattr(self, 'right_scroll'):
            self.right_scroll.setStyleSheet(f"QScrollArea{{background:{pal['panel_bg']};}}")
        if hasattr(self, 'right_inner'):
            self.right_inner.setStyleSheet(f"background:{pal['panel_bg']};")

        if hasattr(self, 'title_lbl'):
            self.title_lbl.setStyleSheet(f"font-size:18px;font-weight:700;color:{pal['text_title']};")
        if hasattr(self, 'tag_lbl'):
            self.tag_lbl.setStyleSheet(f"font-size:11px;background:{pal['tag_bg']};color:{pal['tag_fg']};border-radius:10px;padding:2px 10px;")
        if hasattr(self, 'drop_zone'):
            self.drop_zone.set_theme(self._dark)
        if hasattr(self, 'files_lbl'):
            self.files_lbl.setStyleSheet(f"font-size:11px;color:{pal['text_faint']};letter-spacing:1px;")
        if hasattr(self, 'current_color_lbl'):
            self.current_color_lbl.setStyleSheet(f"font-size:12px;font-weight:600;color:{pal['text_secondary']};")
        if hasattr(self, 'optional_color_lbl'):
            self.optional_color_lbl.setStyleSheet(f"font-size:12px;font-weight:600;color:{pal['text_secondary']};")
        if hasattr(self, 'clear_btn'):
            self.clear_btn.setStyleSheet(
                f"QPushButton{{background:{pal['clear_bg']};border:1.5px solid {pal['clear_border']};"
                f"border-radius:17px;color:{pal['clear_fg']};font-size:13px;font-weight:700;padding:0 16px;}}"
                f"QPushButton:hover{{background:{pal['clear_hover']};border-color:#ff5b50;color:{pal['clear_fg']};}}"
                "QPushButton:pressed{padding-top:1px;}"
            )
        if hasattr(self, 'ver_lbl'):
            self.ver_lbl.setStyleSheet(f"font-size:11px;color:{pal['text_faint']};")
        if hasattr(self, 'info_lbl'):
            self.info_lbl.setStyleSheet(f"font-size:11px;color:{pal['text_faint']};")
        if hasattr(self, 'enc_hint') and not (hasattr(self, 'worker') and self.worker):
            # 编码器提示若非绿/黄的状态色，则跟随主题；已带状态色的场景由 _on_encoder 管理
            pass
        if hasattr(self, 'browse_btn'):
            self.browse_btn.setStyleSheet(
                f"QPushButton{{background:{pal['btn_bg']};border:1px solid {pal['btn_border']};border-radius:6px;color:{pal['btn_fg']};padding:6px;}}"
                f"QPushButton:hover{{color:{pal['btn_fg_hover']};}}")
        if hasattr(self, 'update_btn') and self.update_btn.text() in ("检测更新", "检测中…"):
            self.update_btn.setStyleSheet(self._btn_style_default())

        # 分区小标题 & 分隔线
        for lbl in self._sec_labels:
            lbl.setStyleSheet(f"font-size:12px;font-weight:600;color:{pal['text_secondary']};margin-top:4px;")
        for f in self._div_frames:
            f.setStyleSheet(f"color:{pal['divider']};")
        if hasattr(self, 'sep'):
            self.sep.setStyleSheet(f"color:{pal['divider']};")

        # 开关按钮（描边/背景块）跟随主题重绘未选中状态
        if hasattr(self, 'border_chk'):
            self.border_chk.setStyleSheet(self._toggle_style(self.border_chk.isChecked()))
        if hasattr(self, 'bg_chk'):
            self.bg_chk.setStyleSheet(self._toggle_style(self.bg_chk.isChecked()))

        # 水印位置按钮
        if hasattr(self, 'pos_btns'):
            self._sel_pos(self._current_pos)

        # 文件列表行
        if hasattr(self, 'file_rows'):
            for row in self.file_rows:
                row.set_theme(self._dark)

        # 背景块颜色色块（选中态描边跟随主题）
        if hasattr(self, 'bg_white_btn'):
            self._refresh_bg_swatches()

    def _set_icon(self):
        try:
            data = base64.b64decode(_ICON_B64)
            tmp  = tempfile.NamedTemporaryFile(suffix=".ico", delete=False)
            tmp.write(data); tmp.close()
            self.setWindowIcon(QIcon(tmp.name))
            os.unlink(tmp.name)
        except Exception:
            pass

    def _theme(self):
        self._apply_theme()

    def _build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        hl = QHBoxLayout(root); hl.setContentsMargins(0,0,0,0); hl.setSpacing(0)
        hl.addWidget(self._left_panel(), stretch=1)
        self.sep = QFrame(); self.sep.setFrameShape(QFrame.Shape.VLine)
        hl.addWidget(self.sep)
        hl.addWidget(self._right_panel(), stretch=0)

    def _left_panel(self):
        w  = QWidget(); w.setObjectName("leftPanel")
        vl = QVBoxLayout(w); vl.setContentsMargins(20,18,20,14); vl.setSpacing(10)
        self.title_lbl = QLabel("视频批量加水印")
        self.tag_lbl = QLabel("  检测中…")

        self.theme_btn = QPushButton("🌙  夜间")
        self.theme_btn.setFixedHeight(26)
        self.theme_btn.setFixedWidth(80)
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_btn.clicked.connect(self._toggle_theme)

        hr = QHBoxLayout(); hr.setSpacing(8)
        hr.addWidget(self.title_lbl); hr.addWidget(self.tag_lbl)
        hr.addStretch()
        hr.addWidget(self.theme_btn)
        vl.addLayout(hr)
        self.drop_zone = DropZone(dark=self._dark)
        self.drop_zone.files_dropped.connect(self._add_files)
        vl.addWidget(self.drop_zone)
        lh = QHBoxLayout()
        self.files_lbl = QLabel("已选文件 (0)")
        self.clear_btn = QPushButton("清空列表")
        self.clear_btn.setFixedHeight(34)
        self.clear_btn.setMinimumWidth(108)
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.clicked.connect(self._clear_files)
        lh.addWidget(self.files_lbl); lh.addStretch(); lh.addWidget(self.clear_btn)
        vl.addLayout(lh)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{background:transparent;}")
        self.file_box = QWidget(); self.file_box.setStyleSheet("background:transparent;")
        self.file_vl  = QVBoxLayout(self.file_box)
        self.file_vl.setContentsMargins(0,0,4,0); self.file_vl.setSpacing(6)
        self.file_vl.addStretch()
        scroll.setWidget(self.file_box)
        vl.addWidget(scroll, stretch=1)

        # 左下角版本号 + 检测更新（紧挨在一起）
        bot = QHBoxLayout()
        bot.setContentsMargins(0, 4, 0, 0)
        bot.setSpacing(6)
        self.ver_lbl = QLabel(f"版本  {APP_VERSION}")
        self.update_btn = QPushButton("检测更新")
        self.update_btn.setFixedHeight(22)
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.clicked.connect(self._check_update)
        bot.addWidget(self.ver_lbl)
        bot.addWidget(self.update_btn)
        bot.addStretch()
        vl.addLayout(bot)

        return w

    def _right_panel(self):
        panel = QWidget(); panel.setFixedWidth(320)
        panel.setObjectName("rightPanel")
        outer = QVBoxLayout(panel); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.right_scroll = scroll
        inner = QWidget()
        self.right_inner = inner
        vl = QVBoxLayout(inner); vl.setContentsMargins(18,16,18,12); vl.setSpacing(10)

        vl.addWidget(self._sec("水印文字"))
        self.wm_text = QLineEdit("AI-Generated (Audio & Visuals)")
        vl.addWidget(self.wm_text)

        vl.addWidget(self._sec("水印字体"))
        self.font_cb = QComboBox()
        for fn in FONTS: self.font_cb.addItem(fn)
        self.font_cb.setCurrentIndex(0)
        vl.addWidget(self.font_cb)

        vl.addWidget(self._sec("字体大小"))
        self.font_sl  = QSlider(Qt.Orientation.Horizontal)
        self.font_sl.setRange(8,120); self.font_sl.setValue(20)
        self.font_val = QLabel("20 px"); self.font_val.setFixedWidth(44)
        self.font_val.setStyleSheet("color:#3498DB;font-size:12px;")
        self.font_sl.valueChanged.connect(lambda v: self.font_val.setText(f"{v} px"))
        r = QHBoxLayout(); r.addWidget(self.font_sl); r.addWidget(self.font_val)
        vl.addLayout(r)

        vl.addWidget(self._sec("水印颜色"))

        # 当前颜色单独一行，方便快速确认当前实际使用的颜色
        current_color_row = QHBoxLayout(); current_color_row.setSpacing(8)
        self.current_color_lbl = QLabel("当前颜色")
        self.current_color_lbl.setFixedWidth(58)
        self.current_color_lbl.setStyleSheet(f"font-size:12px;font-weight:600;color:{PALETTE[self._dark]['text_secondary']};")
        current_color_row.addWidget(self.current_color_lbl)
        self.color_sw = ColorSwatch("#FFFFFF")
        self.color_sw.setFixedSize(34,34)
        current_color_row.addWidget(self.color_sw)
        self.current_color_hex = QLabel("#FFFFFF")
        self.current_color_hex.setStyleSheet("font-size:12px;font-weight:600;color:#3498DB;")
        current_color_row.addWidget(self.current_color_hex)
        current_color_row.addStretch()
        vl.addLayout(current_color_row)
        self.color_sw.color_changed.connect(self._on_wm_color_changed)

        # 可选颜色单独一行
        optional_color_row = QHBoxLayout(); optional_color_row.setSpacing(8)
        self.optional_color_lbl = QLabel("可选颜色")
        self.optional_color_lbl.setFixedWidth(58)
        self.optional_color_lbl.setStyleSheet(f"font-size:12px;font-weight:600;color:{PALETTE[self._dark]['text_secondary']};")
        optional_color_row.addWidget(self.optional_color_lbl)
        for c in ["#FFFFFF","#FFD700","#FF4444","#00FF88","#000000"]:
            b = QPushButton(); b.setFixedSize(28,28)
            b.setToolTip(c)
            b.setStyleSheet(f"QPushButton{{background:{c};border:1px solid #555;border-radius:5px;}}QPushButton:hover{{border:2px solid #3498DB;}}")
            b.clicked.connect(lambda _,col=c: self._set_color(col)); optional_color_row.addWidget(b)
        optional_color_row.addStretch()
        vl.addLayout(optional_color_row)

        vl.addWidget(self._sec("透明度"))
        self.op_sl  = QSlider(Qt.Orientation.Horizontal)
        self.op_sl.setRange(10,100); self.op_sl.setValue(100)
        self.op_val = QLabel("100%"); self.op_val.setFixedWidth(36)
        self.op_val.setStyleSheet("color:#3498DB;font-size:12px;")
        self.op_sl.valueChanged.connect(lambda v: self.op_val.setText(f"{v}%"))
        r2 = QHBoxLayout(); r2.addWidget(self.op_sl); r2.addWidget(self.op_val)
        vl.addLayout(r2)

        vl.addWidget(self._div())

        # 描边
        vl.addWidget(self._sec("文字描边"))
        border_row = QHBoxLayout(); border_row.setSpacing(10)
        self.border_chk = QPushButton("描边  OFF")
        self.border_chk.setCheckable(True)
        self.border_chk.setFixedHeight(28)
        self.border_chk.setStyleSheet(self._toggle_style(False))
        self.border_chk.toggled.connect(lambda v: (
            self.border_chk.setText("描边  ON" if v else "描边  OFF"),
            self.border_chk.setStyleSheet(self._toggle_style(v))
        ))
        self.border_sl  = QSlider(Qt.Orientation.Horizontal)
        self.border_sl.setRange(1, 4); self.border_sl.setValue(2)
        self.border_val = QLabel("2 px"); self.border_val.setFixedWidth(36)
        self.border_val.setStyleSheet("color:#3498DB;font-size:12px;")
        self.border_sl.valueChanged.connect(lambda v: self.border_val.setText(f"{v} px"))
        border_row.addWidget(self.border_chk)
        border_row.addWidget(self.border_sl)
        border_row.addWidget(self.border_val)
        vl.addLayout(border_row)

        # 半透明背景块
        vl.addWidget(self._sec("文字背景块"))
        bg_row = QHBoxLayout(); bg_row.setSpacing(10)
        self.bg_chk = QPushButton("背景块  OFF")
        self.bg_chk.setCheckable(True)
        self.bg_chk.setFixedHeight(28)
        self.bg_chk.setStyleSheet(self._toggle_style(False))
        self.bg_chk.toggled.connect(lambda v: (
            self.bg_chk.setText("背景块  ON" if v else "背景块  OFF"),
            self.bg_chk.setStyleSheet(self._toggle_style(v))
        ))
        bg_row.addWidget(self.bg_chk)

        self.bg_white_btn = QPushButton(); self.bg_white_btn.setFixedSize(24,24)
        self.bg_white_btn.setToolTip("背景块：白色")
        self.bg_white_btn.clicked.connect(lambda: self._set_bg_color("#FFFFFF"))
        self.bg_black_btn = QPushButton(); self.bg_black_btn.setFixedSize(24,24)
        self.bg_black_btn.setToolTip("背景块：黑色")
        self.bg_black_btn.clicked.connect(lambda: self._set_bg_color("#000000"))
        bg_row.addWidget(self.bg_white_btn)
        bg_row.addWidget(self.bg_black_btn)
        bg_row.addStretch()
        vl.addLayout(bg_row)
        self._refresh_bg_swatches()

        vl.addWidget(self._div())

        vl.addWidget(self._sec("水印位置"))
        pg = QGridLayout(); pg.setSpacing(6)
        self.pos_btns = {}
        for name,row,col in [("左上角",0,0),("右上角",0,1),("左下角",1,0),("右下角",1,1)]:
            b = QPushButton(name); b.setCheckable(True); b.setFixedHeight(34)
            b.clicked.connect(lambda _,n=name: self._sel_pos(n))
            self.pos_btns[name] = b; pg.addWidget(b,row,col)
        self._sel_pos("左上角"); vl.addLayout(pg)

        vl.addWidget(self._sec("边距（水印距画面边缘 px）"))
        self.mg_sl  = NoScrollSlider(Qt.Orientation.Horizontal)
        self.mg_sl.setRange(0,200); self.mg_sl.setValue(10)
        self.mg_val = QLabel("10 px"); self.mg_val.setFixedWidth(44)
        self.mg_val.setStyleSheet("color:#3498DB;font-size:12px;")
        self.mg_sl.valueChanged.connect(lambda v: self.mg_val.setText(f"{v} px"))
        r3 = QHBoxLayout(); r3.addWidget(self.mg_sl); r3.addWidget(self.mg_val)
        vl.addLayout(r3)

        vl.addWidget(self._div())

        vl.addWidget(self._sec("编码器"))
        self.enc_cb = NoScrollCombo()
        self.enc_cb.addItem("🔍 自动检测")
        self.enc_cb.addItem("🟢 NVIDIA GPU  (h264_nvenc)")
        self.enc_cb.addItem("🟢 Intel GPU   (h264_qsv)")
        self.enc_cb.addItem("🟢 AMD GPU     (h264_amf)")
        self.enc_cb.addItem("🟡 CPU 软件编码 (libx264)")
        self.enc_cb.setCurrentIndex(0)
        self.enc_cb.setToolTip("自动检测失败时可手动指定显卡")
        vl.addWidget(self.enc_cb)
        self.enc_hint = QLabel("编码器检测中…")
        self.enc_hint.setStyleSheet("font-size:12px;color:#888;")
        self.enc_hint.setWordWrap(True)
        vl.addWidget(self.enc_hint)

        vl.addWidget(self._sec("输出质量"))
        self.quality_cb = NoScrollCombo()
        for k in QUALITY_KEYS: self.quality_cb.addItem(k)
        self.quality_cb.setCurrentIndex(4)
        vl.addWidget(self.quality_cb)

        vl.addWidget(self._div())

        vl.addWidget(self._sec("统一输出格式"))
        self.format_cb = NoScrollCombo()
        for label, ext in OUTPUT_FORMATS:
            self.format_cb.addItem(label, ext)
        self.format_cb.setCurrentIndex(0)
        self.format_cb.setToolTip("默认输出 MP4；原视频已经是 MP4 时不做额外格式转换，其他格式统一转换为所选格式")
        vl.addWidget(self.format_cb)

        vl.addWidget(self._sec("输出文件名前缀"))
        self.prefix_edit = QLineEdit("加水印-")
        vl.addWidget(self.prefix_edit)

        vl.addWidget(self._sec("输出目录"))
        or_ = QHBoxLayout(); or_.setSpacing(6)
        self.out_edit = QLineEdit(); self.out_edit.setPlaceholderText("默认：原视频所在目录")
        self.browse_btn = QPushButton("浏览"); self.browse_btn.setFixedWidth(50)
        self.browse_btn.clicked.connect(self._browse_out)
        or_.addWidget(self.out_edit); or_.addWidget(self.browse_btn)
        vl.addLayout(or_)
        vl.addStretch()

        scroll.setWidget(inner)
        outer.addWidget(scroll, stretch=1)

        bottom = QWidget(); bottom.setObjectName("bottomPanel")
        bottom.setStyleSheet("background:#252525;border-top:1px solid #2a2a2a;")
        bl = QVBoxLayout(bottom); bl.setContentsMargins(18,10,18,16); bl.setSpacing(5)
        self.info_lbl = QLabel("")
        self.info_lbl.setStyleSheet("font-size:11px;color:#666;")
        self.info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_lbl.hide()
        self.total_bar = QProgressBar()
        self.total_bar.setRange(0,100); self.total_bar.setValue(0)
        self.total_bar.setTextVisible(False); self.total_bar.setFixedHeight(5)
        self.total_bar.hide()
        self.total_bar.setStyleSheet("QProgressBar{background:#2d2d2d;border-radius:2px;border:none;}QProgressBar::chunk{background:#27ae60;border-radius:2px;}")
        self.start_btn = QPushButton("开始处理")
        self.start_btn.setFixedHeight(44)
        self.start_btn.setStyleSheet(self._bstyle("#3498DB","#2980b9"))
        self.start_btn.clicked.connect(self._start_stop)
        bl.addWidget(self.info_lbl); bl.addWidget(self.total_bar); bl.addWidget(self.start_btn)
        outer.addWidget(bottom)
        return panel

    def _toggle_style(self, on: bool) -> str:
        pal = PALETTE[self._dark]
        if on:
            return ("QPushButton{background:#1a3a5c;border:1.5px solid #3498DB;"
                    "border-radius:6px;color:#3498DB;font-size:12px;padding:0 10px;}"
                    "QPushButton:hover{background:#1e4570;}")
        return (f"QPushButton{{background:{pal['btn_bg']};border:1px solid {pal['btn_border']};"
                f"border-radius:6px;color:{pal['btn_fg']};font-size:12px;padding:0 10px;}}"
                f"QPushButton:hover{{border-color:#555;color:{pal['btn_fg_hover']};}}")

    def _sec(self, t):
        l = QLabel(t)
        l.setStyleSheet(f"font-size:12px;font-weight:600;color:{PALETTE[self._dark]['text_secondary']};margin-top:4px;")
        self._sec_labels.append(l)
        return l

    def _div(self):
        f = QFrame(); f.setFrameShape(QFrame.Shape.HLine); f.setStyleSheet(f"color:{PALETTE[self._dark]['divider']};")
        self._div_frames.append(f)
        return f

    def _bstyle(self, bg, hv):
        return (f"QPushButton{{background:{bg};color:white;border:none;border-radius:8px;font-size:14px;font-weight:600;}}"
                f"QPushButton:hover{{background:{hv};}}QPushButton:pressed{{background:{hv};}}"
                f"QPushButton:disabled{{background:#2d2d2d;color:#555;}}")

    def _sel_pos(self, name):
        self._current_pos = name
        pal = PALETTE[self._dark]
        act = "QPushButton{background:#1a3d2b;border:1.5px solid #27ae60;border-radius:6px;color:#27ae60;font-size:13px;}"
        idl = (f"QPushButton{{background:{pal['pos_idle_bg']};border:1px solid {pal['pos_idle_border']};"
               f"border-radius:6px;color:{pal['pos_idle_fg']};font-size:13px;}}"
               f"QPushButton:hover{{border-color:#555;color:{pal['pos_idle_fg_hover']};}}")
        for n,b in self.pos_btns.items():
            b.setChecked(n==name); b.setStyleSheet(act if n==name else idl)

    def _set_color(self, c):
        self.color_sw.color = c.upper(); self.color_sw._a()
        self._update_current_color_display(self.color_sw.get())
        self._on_wm_color_changed(self.color_sw.get())

    def _update_current_color_display(self, color=None):
        color = (color or self.color_sw.get()).upper()
        if hasattr(self, 'current_color_hex'):
            self.current_color_hex.setText(color)

    def _on_wm_color_changed(self, color):
        """水印文字颜色变化时，更新当前颜色显示，并联动背景块默认颜色。"""
        color = color.upper()
        self._update_current_color_display(color)
        c = color
        if c == "#FFFFFF":
            self._set_bg_color("#000000")
        elif c == "#000000":
            self._set_bg_color("#FFFFFF")

    def _set_bg_color(self, color):
        self._bg_color = color.upper()
        self._refresh_bg_swatches()

    def _refresh_bg_swatches(self):
        if not hasattr(self, 'bg_white_btn'):
            return
        sel_border = "2px solid #3498DB"
        idl_border = "1px solid #777"
        w_border = sel_border if self._bg_color == "#FFFFFF" else idl_border
        b_border = sel_border if self._bg_color == "#000000" else idl_border
        self.bg_white_btn.setStyleSheet(
            f"QPushButton{{background:#FFFFFF;border:{w_border};border-radius:5px;}}QPushButton:hover{{border-color:#3498DB;}}")
        self.bg_black_btn.setStyleSheet(
            f"QPushButton{{background:#000000;border:{b_border};border-radius:5px;}}QPushButton:hover{{border-color:#3498DB;}}")

    def _browse_out(self):
        d = QFileDialog.getExistingDirectory(self,"选择输出目录")
        if d: self.out_edit.setText(d)

    def _add_files(self, paths):
        # 上一批任务已完成，拖入新文件时自动清空旧记录
        if self._task_done:
            self._clear_files()
            self._task_done = False
        existing = {r.filepath for r in self.file_rows}
        for p in paths:
            if p not in existing:
                row = FileRowWidget(p, dark=self._dark)
                row.remove_clicked.connect(lambda r=row: self._remove(r))
                self.file_rows.append(row)
                self.file_vl.insertWidget(self.file_vl.count()-1, row)
        self._refresh()

    def _remove(self, row):
        self.file_rows.remove(row); row.setParent(None); row.deleteLater(); self._refresh()

    def _clear_files(self):
        for r in self.file_rows: r.setParent(None); r.deleteLater()
        self.file_rows.clear(); self._refresh()

    def _set_right_panel_enabled(self, enabled: bool):
        """处理中禁用右侧所有控件（开始按钮/检测更新按钮除外）"""
        for w in [self.wm_text, self.font_cb, self.font_sl, self.color_sw,
                  self.op_sl, self.mg_sl, self.enc_cb, self.quality_cb,
                  self.format_cb, self.prefix_edit, self.out_edit,
                  self.border_chk, self.border_sl, self.bg_chk,
                  self.bg_white_btn, self.bg_black_btn]:
            w.setEnabled(enabled)
        for btn in self.pos_btns.values():
            btn.setEnabled(enabled)

    def _refresh(self):
        n = len(self.file_rows)
        self.files_lbl.setText(f"已选文件 ({n})")
        self.start_btn.setText(f"开始处理  ({n} 个文件)" if n else "开始处理")

    def _start_stop(self):
        # 正在处理中 → 点击变停止
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.start_btn.setText("正在停止…")
            self.start_btn.setEnabled(False)
            return

        # 没有文件
        if not self.file_rows:
            QMessageBox.information(self, "提示", "请先添加视频文件")
            return

        # 重置状态
        for r in self.file_rows: r.reset()
        self.total_bar.setValue(0); self.total_bar.show()
        self.info_lbl.setText("正在检测编码器…"); self.info_lbl.show()

        # 构建任务列表
        prefix  = self.prefix_edit.text().strip() or "加水印-"
        out_dir = self.out_edit.text().strip()
        output_ext = self.format_cb.currentData() or ".mp4"
        tasks   = []
        for r in self.file_rows:
            p = Path(r.filepath)
            d = Path(out_dir) if out_dir else p.parent
            # MP4 输入 + MP4 输出：保持 MP4，不做额外格式转换。
            # 其他输入格式：输出统一改为用户选择的后缀，由当前 FFmpeg 处理流程完成转码。
            output_name = prefix + p.stem + output_ext
            tasks.append({"input": str(p), "output": str(d / output_name)})

        # 编码器和参数
        enc_map = {0: None, 1: "h264_nvenc", 2: "h264_qsv", 3: "h264_amf", 4: "libx264"}
        params = {
            "text":           self.wm_text.text(),
            "font":           self.font_cb.currentText(),
            "font_size":      self.font_sl.value(),
            "color":          self.color_sw.get(),
            "opacity":        self.op_sl.value(),
            "position":       self._current_pos,
            "margin":         self.mg_sl.value(),
            "quality":        self.quality_cb.currentText(),
            "output_format":  output_ext,
            "manual_encoder": enc_map.get(self.enc_cb.currentIndex()),
            "border_on":      self.border_chk.isChecked(),
            "border_w":       self.border_sl.value(),
            "bg_on":          self.bg_chk.isChecked(),
            "bg_color":       self._bg_color,
        }

        self._done_count = 0
        self._total = len(tasks)
        self.worker = WatermarkWorker(tasks, params)
        self.worker.encoder_detected.connect(self._on_encoder)
        self.worker.progress.connect(self._on_prog)
        self.worker.file_done.connect(self._on_done)
        self.worker.all_done.connect(self._on_all_done)
        self.worker.start()

        self.start_btn.setText("停止处理")
        self.start_btn.setStyleSheet(self._bstyle("#c0392b", "#a93226"))
        self._set_right_panel_enabled(False)

    def _on_encoder(self, enc):
        name_map = {"h264_nvenc":"NVIDIA GPU 加速","h264_qsv":"Intel GPU 加速",
                    "h264_amf":"AMD GPU 加速","libx264":"CPU 软件编码"}
        label = name_map.get(enc, enc)
        color = "#27ae60" if enc != "libx264" else "#f39c12"
        self.enc_hint.setText(f"当前编码器：{label}")
        self.enc_hint.setStyleSheet(f"font-size:11px;color:{color};")

    def _on_prog(self, idx, pct, speed):
        if idx < len(self.file_rows): self.file_rows[idx].set_progress(pct)
        if self._total > 0:
            tot = int((self._done_count * 100 + pct) / self._total)
            self.total_bar.setValue(tot)
            spd = f"  速度 {speed}x" if speed else ""
            self.info_lbl.setText(f"第 {idx+1}/{self._total} 个  {pct}%{spd}  —  总进度 {tot}%")

    def _on_done(self, idx, ok, msg):
        if idx < len(self.file_rows): self.file_rows[idx].set_done(ok)
        self._done_count += 1
        if not ok: QMessageBox.warning(self,"处理失败",f"第 {idx+1} 个文件失败:\n{msg}")

    def _on_all_done(self):
        self.worker = None
        self.total_bar.setValue(100)
        self.info_lbl.setText(f"✓ 全部完成  ({self._total} 个文件)")
        self._set_right_panel_enabled(True)
        self._task_done = True
        self.start_btn.setEnabled(True)
        self.start_btn.setText(f"开始处理  ({len(self.file_rows)} 个文件)")
        self.start_btn.setStyleSheet(self._bstyle("#3498DB","#2980b9"))
        QMessageBox.information(self, "完成", f"全部 {self._total} 个文件处理完毕！")

    def _start_startup_checks(self):
        """启动后台环境检测；主窗口无需等待 FFmpeg 探测完成。"""
        self.tag_lbl.setText("  ⏳  正在检测 FFmpeg…")
        self.tag_lbl.setStyleSheet(
            "font-size:11px;background:#2d3440;color:#f1c40f;"
            "border-radius:10px;padding:2px 10px;")
        self.start_btn.setEnabled(False)

        self._startup_worker = StartupCheckWorker()
        self._startup_worker.finished.connect(self._on_startup_checks_finished)
        self._startup_worker.start()

    def _on_startup_checks_finished(self, ok, version, encoder):
        if not ok:
            self.tag_lbl.setText("  ✗  未找到 FFmpeg")
            self.tag_lbl.setStyleSheet(
                "font-size:11px;background:#3d1a1a;color:#e74c3c;"
                "border-radius:10px;padding:2px 10px;")
            self.start_btn.setEnabled(False)
            return

        self.tag_lbl.setText(f"  ✓  FFmpeg {version}" if version else "  ✓  FFmpeg")
        self.tag_lbl.setStyleSheet(
            "font-size:11px;background:#1a3d2b;color:#27ae60;"
            "border-radius:10px;padding:2px 10px;")
        self._on_encoder(encoder)
        self.start_btn.setEnabled(True)
        self._startup_worker = None

    def _check_ffmpeg(self):
        """兼容旧调用入口；实际检测统一转入后台线程。"""
        self._start_startup_checks()

    def _detect_enc_async(self):
        """兼容旧调用入口；编码器探测已包含在 StartupCheckWorker。"""
        self._start_startup_checks()

    # ── 更新检测 ─────────────────────────────────────────────────
    _update_url   = ""
    _setup_url    = ""
    _setup_name   = ""
    _setup_digest = ""
    _latest_ver   = ""
    _latest_body  = ""

    def _is_installed_build(self):
        """只有安装版目录才自动更新；绿色版/源码版仍提供网页下载。"""
        if not getattr(sys, "frozen", False): return False
        try: return (Path(sys.executable).parent / "unins000.exe").exists()
        except Exception: return False

    def _auto_check_update(self):
        self._run_checker(silent=True)

    def _check_update(self):
        if self.update_btn.text() == "有可用更新" and MainWindow._update_url:
            self._show_update_dialog(MainWindow._latest_ver, MainWindow._update_url, MainWindow._latest_body, MainWindow._setup_url, MainWindow._setup_digest, MainWindow._setup_name)
            return
        self._run_checker(silent=False)

    def _run_checker(self, silent):
        self._checking_silent = silent; self.update_btn.setEnabled(False)
        self.update_btn.setText("检测更新" if silent else "检测中…")
        checker = UpdateChecker()
        checker.result.connect(self._on_update_result); checker.error.connect(self._on_update_error)
        checker.start(); self._updater = checker

    def _btn_style_default(self):
        pal = PALETTE[self._dark]
        return (f"QPushButton{{background:transparent;border:1px solid {pal['btn_border']};border-radius:11px;color:{pal['btn_fg']};font-size:11px;padding:0 10px;}}"
                "QPushButton:hover{border-color:#3498DB;color:#3498DB;}")

    def _on_update_result(self, latest, url, body, setup_url, setup_name, setup_digest):
        self.update_btn.setEnabled(True); silent = self._checking_silent
        MainWindow._update_url=url; MainWindow._setup_url=setup_url; MainWindow._setup_name=setup_name; MainWindow._setup_digest=setup_digest
        MainWindow._latest_ver=latest; MainWindow._latest_body=body
        if latest and latest != APP_VERSION:
            self.update_btn.setText("有可用更新")
            self.update_btn.setStyleSheet("QPushButton{background:#e67e22;border:none;border-radius:11px;color:#fff;font-size:11px;font-weight:600;padding:0 12px;}QPushButton:hover{background:#d35400;}QPushButton:pressed{background:#b94600;}")
            self.update_btn.setToolTip(f"新版本 {latest} 可用，点击后可后台下载并直接更新" if setup_url and self._is_installed_build() else f"新版本 {latest} 可用，点击查看下载页")
            if not silent: self._show_update_dialog(latest,url,body,setup_url,setup_digest,setup_name)
        else:
            self.update_btn.setText("当前最新版 ✓")
            self.update_btn.setStyleSheet("QPushButton{background:transparent;border:1px solid #27ae60;border-radius:11px;color:#27ae60;font-size:11px;padding:0 10px;}QPushButton:hover{background:rgba(39,174,96,0.1);}")
            self.update_btn.setToolTip("")
            if not silent: QMessageBox.information(self,"检测更新",f"当前已是最新版本  {APP_VERSION} 🎉")

    def _on_update_error(self, msg):
        self.update_btn.setEnabled(True); silent=self._checking_silent
        self.update_btn.setText("检测更新"); self.update_btn.setStyleSheet(self._btn_style_default()); self.update_btn.setToolTip("")
        if not silent: QMessageBox.warning(self,"检测失败","无法连接更新服务器，请检查网络连接。")

    def _show_update_dialog(self, latest, url, body="", setup_url="", setup_digest="", setup_name=""):
        dlg=UpdateDialog(self,APP_VERSION,latest,body,url,setup_url=setup_url,setup_digest=setup_digest,setup_name=setup_name,can_auto_update=self._is_installed_build(),dark=self._dark)
        dlg.exec()

    def _start_self_update(self, dlg, setup_url, setup_digest="", setup_name=""):
        """后台下载新版 Setup；完成后关闭当前程序并交给安装器覆盖更新。"""
        if not setup_url: dlg.set_download_error("没有找到新版安装包。"); return
        filename=Path(setup_name or "video-watermark-update-Setup.exe").name
        if not filename.lower().endswith(".exe"): filename += ".exe"
        worker=UpdateDownloadWorker(setup_url,setup_digest,filename)
        worker.progress.connect(dlg.set_download_progress)
        def on_error(message):
            dlg.set_download_error(message); self._update_download_worker=None
        def on_finished(installer_path):
            dlg.progress_label.setText("下载完成，正在重启安装更新…"); dlg.go_btn.setText("正在更新…"); dlg.go_btn.setEnabled(False)
            try:
                subprocess.Popen([installer_path,"/VERYSILENT","/SUPPRESSMSGBOXES","/CLOSEAPPLICATIONS","/NORESTART"],close_fds=True)
            except Exception as exc:
                dlg.set_download_error(f"无法启动安装程序：{exc}"); self._update_download_worker=None; return
            self._update_download_worker=None
            QTimer.singleShot(250,QApplication.instance().quit)
        worker.error.connect(on_error); worker.finished.connect(on_finished)
        self._update_download_worker=worker; worker.start()


    def _save_settings(self):
        s = QSettings("VideoWatermark", "Settings")
        s.setValue("wm_text",    self.wm_text.text())
        s.setValue("font",       self.font_cb.currentText())
        s.setValue("font_size",  self.font_sl.value())
        s.setValue("color",      self.color_sw.get())
        s.setValue("opacity",    self.op_sl.value())
        s.setValue("position",   self._current_pos)
        s.setValue("margin",     self.mg_sl.value())
        s.setValue("quality",    self.quality_cb.currentIndex())
        s.setValue("output_format", self.format_cb.currentIndex())
        s.setValue("encoder",    self.enc_cb.currentIndex())
        s.setValue("prefix",     self.prefix_edit.text())
        s.setValue("out_dir",    self.out_edit.text())
        s.setValue("border_on",  self.border_chk.isChecked())
        s.setValue("border_w",   self.border_sl.value())
        s.setValue("bg_on",      self.bg_chk.isChecked())
        s.setValue("bg_color",   self._bg_color)
        s.setValue("dark_theme", self._dark)

    def _load_settings(self):
        s = QSettings("VideoWatermark", "Settings")
        if s.value("wm_text") is None: return   # 首次运行，使用默认值
        self.wm_text.setText(s.value("wm_text", "AI-Generated (Audio & Visuals)"))
        font = s.value("font", "Arial")
        idx = self.font_cb.findText(font)
        if idx >= 0: self.font_cb.setCurrentIndex(idx)
        self.font_sl.setValue(int(s.value("font_size", 20)))
        color = str(s.value("color", "#FFFFFF")).upper()
        self.color_sw.color = color; self.color_sw._a()
        self._update_current_color_display(color)
        self.op_sl.setValue(int(s.value("opacity", 100)))
        pos = s.value("position", "左上角")
        self._sel_pos(pos)
        self.mg_sl.setValue(int(s.value("margin", 10)))
        self.quality_cb.setCurrentIndex(int(s.value("quality", 4)))
        self.format_cb.setCurrentIndex(int(s.value("output_format", 0)))
        self.enc_cb.setCurrentIndex(int(s.value("encoder", 0)))
        self.prefix_edit.setText(s.value("prefix", "加水印-"))
        self.out_edit.setText(s.value("out_dir", ""))
        border_on = s.value("border_on", False)
        border_on = border_on == "true" if isinstance(border_on, str) else bool(border_on)
        self.border_chk.setChecked(border_on)
        self.border_sl.setValue(int(s.value("border_w", 2)))
        bg_on = s.value("bg_on", False)
        bg_on = bg_on == "true" if isinstance(bg_on, str) else bool(bg_on)
        self.bg_chk.setChecked(bg_on)
        default_bg = "#000000" if color.upper() == "#FFFFFF" else ("#FFFFFF" if color.upper() == "#000000" else "#000000")
        self._set_bg_color(s.value("bg_color", default_bg))
        dark = s.value("dark_theme", True)
        dark = dark != "false" if isinstance(dark, str) else bool(dark)
        if dark != self._dark:
            self._dark = dark
            self._apply_theme()

    def closeEvent(self, e):
        self._save_settings()
        if self.worker and self.worker.isRunning():
            self.worker.stop(); self.worker.wait(3000)
        if getattr(self, "_startup_worker", None) and self._startup_worker.isRunning():
            self._startup_worker.requestInterruption()
            self._startup_worker.wait(1000)
        e.accept()


def create_splash():
    """创建启动画面"""
    w, h = 360, 220
    pix = QPixmap(w, h)
    pix.fill(Qt.GlobalColor.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # 深色圆角背景
    from PyQt6.QtGui import QBrush, QPen, QColor as QC
    from PyQt6.QtCore import QRectF
    p.setBrush(QBrush(QC("#1a2035")))
    p.setPen(QPen(QC("#2a3a5a"), 1))
    p.drawRoundedRect(QRectF(1, 1, w-2, h-2), 14, 14)

    # 软件名
    f_title = QFont("Microsoft YaHei", 16, QFont.Weight.Bold)
    p.setFont(f_title)
    p.setPen(QC("#ffffff"))
    p.drawText(QRectF(0, 68, w, 36), Qt.AlignmentFlag.AlignHCenter, "视频批量加水印")

    # 英文副标题
    f_sub = QFont("Segoe UI", 10)
    p.setFont(f_sub)
    p.setPen(QC("#5a7aaa"))
    p.drawText(QRectF(0, 102, w, 24), Qt.AlignmentFlag.AlignHCenter, "Video Watermark Tool")

    # 版本号
    f_ver = QFont("Segoe UI", 9)
    p.setFont(f_ver)
    p.setPen(QC("#3498DB"))
    p.drawText(QRectF(0, 130, w, 20), Qt.AlignmentFlag.AlignHCenter, APP_VERSION)

    # 进度条背景
    bar_x, bar_y, bar_w, bar_h = (w-160)//2, 162, 160, 3
    p.setBrush(QBrush(QC("#1e2d45")))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

    # 提示文字
    f_hint = QFont("Microsoft YaHei", 8)
    p.setFont(f_hint)
    p.setPen(QC("#2a3a5a"))
    p.drawText(QRectF(0, 174, w, 20), Qt.AlignmentFlag.AlignHCenter, "正在加载…")

    p.end()
    return pix, (bar_x, bar_y, bar_w, bar_h)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("视频批量加水印")
    app.setApplicationVersion(APP_VERSION)

    # 启动画面：先显示，再异步创建主窗口。这样打包后的 EXE 即使初始化较慢，
    # 用户也会立即看到反馈，而不是面对长时间空白。
    splash_pix, bar_info = create_splash()
    splash = QSplashScreen(splash_pix, Qt.WindowType.WindowStaysOnTopHint)
    splash.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    splash.show()
    app.processEvents()

    from PyQt6.QtGui import QBrush, QPen, QColor as QC
    from PyQt6.QtCore import QRectF
    bar_x, bar_y, bar_w, bar_h = bar_info

    def update_progress(step):
        pct = max(0.0, min(1.0, step / 10.0))
        cur_w = int(bar_w * pct)
        p2 = QPainter(splash_pix)
        p2.setRenderHint(QPainter.RenderHint.Antialiasing)
        p2.setBrush(QBrush(QC("#1e2d45")))
        p2.setPen(Qt.PenStyle.NoPen)
        p2.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)
        if cur_w > 0:
            p2.setBrush(QBrush(QC("#3498DB")))
            p2.drawRoundedRect(QRectF(bar_x, bar_y, cur_w, bar_h), 2, 2)
        p2.end()
        splash.setPixmap(splash_pix)
        app.processEvents()

    # 只做非常短的视觉反馈，不再人为等待 1.6 秒。
    for i, delay in enumerate((30, 90, 160), start=1):
        QTimer.singleShot(delay, lambda s=i: update_progress(s))

    win_holder = {"win": None}

    def build_main_window():
        try:
            win_holder["win"] = MainWindow()
            # 主窗口 UI 已经建立，立即结束 splash；FFmpeg/编码器检测在后台继续。
            update_progress(10)
            splash.finish(win_holder["win"])
            win_holder["win"].show()
            win_holder["win"].raise_()
            win_holder["win"].activateWindow()
        except Exception as exc:
            splash.close()
            QMessageBox.critical(None, "启动失败", f"程序启动失败：\n{exc}")
            raise

    QTimer.singleShot(0, build_main_window)
    sys.exit(app.exec())
