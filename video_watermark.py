"""
视频批量加水印工具 v3
依赖: pip install -r requirements.txt
外部依赖: ffmpeg.exe / ffprobe.exe（程序目录、third_party/ffmpeg 或系统 PATH）
运行: pythonw video_watermark.py
"""

APP_VERSION  = "v1.5.4"
REPO         = "bstbuku-ship-it/video-watermark"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
API_URL      = f"https://api.github.com/repos/{REPO}/releases/latest"

import sys, os, subprocess, platform, re, base64, tempfile, hashlib, time, html
import urllib.request, urllib.error, urllib.parse, json
from PyQt6.QtCore import QSettings, QUrl
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QLineEdit, QSlider, QFileDialog,
    QProgressBar, QComboBox, QColorDialog, QFrame,
    QGridLayout, QMessageBox, QSizePolicy, QScrollArea,
    QSplashScreen, QDialog, QTextEdit, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QIcon, QDragEnterEvent, QDropEvent, QPixmap, QPainter, QFont, QLinearGradient, QDesktopServices

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
SEMVER_RE  = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

def _parse_semver(value):
    """仅接受严格的 x.y.z 版本，避免更新检查被异常 tag 误导。"""
    m = SEMVER_RE.match(str(value or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def _escape_drawtext_text(value):
    """按 FFmpeg filtergraph 的多层转义规则处理用户可控水印文字。"""
    text = str(value or "")
    escaped = []
    saw_quote = False
    # FFmpeg 先解析 filtergraph，再解析 drawtext 选项；单引号后的冒号需要额外转义。
    # 使用逐字符处理，避免连续 replace 让新加入的反斜杠再次被转义。
    for ch in text:
        if ch == "\\":
            escaped.append("\\\\")
        elif ch == "'":
            escaped.append("\\\\\\'")
            saw_quote = True
        elif ch == ":":
            escaped.append("\\\\:" if saw_quote else "\\:")
        elif ch in (",", ";", "[", "]"):
            escaped.append("\\" + ch)
        else:
            escaped.append(ch)
    return "".join(escaped)


def _is_trusted_github_asset_url(url):
    """自动更新只接受官方 GitHub HTTPS 下载地址。"""
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
        path = urllib.parse.unquote(parsed.path)
        return (
            parsed.scheme.lower() == "https"
            and parsed.hostname == "github.com"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and path.startswith(f"/{REPO}/releases/download/")
            and "\\" not in path
            and all(part not in (".", "..") for part in path.split("/"))
        )
    except Exception:
        return False


def _is_trusted_github_download_response_url(url):
    """GitHub Releases 资产允许跳转到 GitHub 官方资产域名，不接受任意外部重定向。"""
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
        return (
            parsed.scheme.lower() == "https"
            and parsed.hostname in {
                "github.com",
                "release-assets.githubusercontent.com",
                "objects.githubusercontent.com",
                "github-releases.githubusercontent.com",
            }
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
        )
    except Exception:
        return False


def _is_trusted_github_release_page_url(url):
    """只允许打开当前项目的 GitHub Releases 页面，拒绝 API 返回的外部跳转地址。"""
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
        path = urllib.parse.unquote(parsed.path)
        return (
            parsed.scheme.lower() == "https"
            and parsed.hostname == "github.com"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and path.startswith(f"/{REPO}/releases/")
            and "\\" not in path
            and all(part not in (".", "..") for part in path.split("/"))
        )
    except Exception:
        return False


def _canonical_path(path):
    """解析符号链接后规范化路径，避免通过别名把输出写回输入视频。"""
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


def _show_plain_message(parent, icon, title, message):
    """以纯文本显示含有用户文件名/路径的错误，避免 QLabel/QMessageBox 自动解析富文本。"""
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(str(title))
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(str(message))
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()


def _validate_output_prefix(prefix):
    """输出名前缀只能是文件名片段，禁止路径分隔符、控制字符和 Windows 特殊字符。"""
    value = str(prefix or "").strip()
    if not value:
        return "加水印-"
    if any(ord(ch) < 32 for ch in value) or any(ch in value for ch in '<>:"/\\|?*'):
        raise ValueError("输出文件名前缀包含非法字符；请勿使用路径分隔符或 Windows 特殊字符。")
    if value.endswith((".", " ")):
        raise ValueError("输出文件名前缀不能以空格或句点结尾。")
    return value

class NoScrollCombo(QComboBox):
    """禁止鼠标滚轮切换选项"""
    def wheelEvent(self, e): e.ignore()

class NoScrollSlider(QSlider):
    """禁止鼠标滚轮调节值"""
    def wheelEvent(self, e): e.ignore()


# 字体下拉框采用中文说明；括号保留原字体名，方便识别与排查。
# 新增的三款字体来自 Google Fonts，采用 SIL Open Font License。
FONTS = {
    "通用无衬线（Arial）":       "C:/Windows/Fonts/arial.ttf",
    "微软界面字体（Segoe UI）": "C:/Windows/Fonts/segoeui.ttf",
    "办公风格（Calibri）":       "C:/Windows/Fonts/calibri.ttf",
    "现代简洁（Roboto）":        "",
    "清晰易读（Open Sans）":     "",
    "醒目标题（Montserrat）":    "",
}

LEGACY_FONT_LABELS = {
    "Arial": "通用无衬线（Arial）",
    "Segoe UI": "微软界面字体（Segoe UI）",
    "Calibri": "办公风格（Calibri）",
}

FONT_DOWNLOADS = {
    # 固定到 Google Fonts METADATA.pb 所引用的上游提交，避免运行时跟随可变 main 分支。
    "现代简洁（Roboto）": (
        "Roboto[wdth,wght].ttf",
        "https://raw.githubusercontent.com/googlefonts/roboto-classic/91d5d3e5b81efa04a77925cc609fdcdd7ee663d1/web/split/Roboto%5Bwdth,wght%5D.ttf",
        "https://raw.githubusercontent.com/googlefonts/roboto-classic/91d5d3e5b81efa04a77925cc609fdcdd7ee663d1/OFL.txt",
    ),
    "清晰易读（Open Sans）": (
        "OpenSans[wdth,wght].ttf",
        "https://raw.githubusercontent.com/googlefonts/opensans/bd7e37632246368c60fdcbd374dbf9bad11969b6/fonts/variable/OpenSans%5Bwdth,wght%5D.ttf",
        "https://raw.githubusercontent.com/googlefonts/opensans/bd7e37632246368c60fdcbd374dbf9bad11969b6/OFL.txt",
    ),
    "醒目标题（Montserrat）": (
        "Montserrat[wght].ttf",
        "https://raw.githubusercontent.com/JulietaUla/Montserrat/cc8daf2e7085006b9c112542fc82b58afc13521d/fonts/variable/Montserrat%5Bwght%5D.ttf",
        "https://raw.githubusercontent.com/JulietaUla/Montserrat/cc8daf2e7085006b9c112542fc82b58afc13521d/OFL.txt",
    ),
}


def _font_cache_dir():
    '将按需下载的开放字体放到用户目录，避免写入安装目录需要管理员权限。'
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    base = Path(root) / "VideoWatermark" / "fonts" if root else Path.home() / ".video_watermark" / "fonts"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _is_trusted_font_resource_url(url):
    """字体和许可证只允许访问源码中明确固定提交的官方上游文件。"""
    value = str(url or "")
    try:
        parsed = urllib.parse.urlparse(value)
        allowed = {
            resource_url
            for _filename, font_url, license_url in FONT_DOWNLOADS.values()
            for resource_url in (font_url, license_url)
        }
        return (
            value in allowed
            and parsed.scheme.lower() == "https"
            and parsed.hostname == "raw.githubusercontent.com"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
    except Exception:
        return False


def _is_valid_cached_font(path, max_bytes=18 * 1024 * 1024):
    """缓存字体必须是普通文件、大小合理并具有合法的 TTF/OTF 字体签名。"""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        size = path.stat().st_size
        if size < 4 or size > max_bytes:
            return False
        with path.open("rb") as f:
            signature = f.read(4)
        return signature in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")
    except OSError:
        return False


def _atomic_write_bytes(destination, payload):
    """在目标目录创建独占临时文件，再原子替换，避免可预测临时路径的符号链接风险。"""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=destination.name + ".", suffix=".tmp", dir=str(destination.parent)
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
        os.replace(str(temp), str(destination))
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _download_open_font(url, destination, max_bytes=18 * 1024 * 1024):
    """从固定提交的官方字体源码下载；限制响应体大小并校验响应地址与字体签名。"""
    if not _is_trusted_font_resource_url(url):
        raise RuntimeError("字体下载地址不是已固定版本的受信任官方文件地址。")
    req = urllib.request.Request(url, headers={"User-Agent": "video-watermark-font-loader"})
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=destination.name + ".", suffix=".download", dir=str(destination.parent)
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            with urllib.request.urlopen(req, timeout=25) as response:
                if response.geturl() != url:
                    raise RuntimeError("字体下载发生意外重定向，已取消。")
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        if int(content_length) > max_bytes:
                            raise RuntimeError("字体文件大小超出允许范围，已取消下载。")
                    except ValueError:
                        raise RuntimeError("字体服务器返回了无效的文件长度。")
                total = 0
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise RuntimeError("字体文件大小超出允许范围，已取消下载。")
                    out.write(chunk)
                out.flush()
        if not _is_valid_cached_font(temp, max_bytes=max_bytes):
            raise RuntimeError("下载内容不是有效的 TTF/OTF 字体文件。")
        os.replace(str(temp), str(destination))
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _resolve_font_file(font_name):
    '解析水印字体路径；新增免费字体在首次使用时从官方源下载并缓存。'
    font_name = LEGACY_FONT_LABELS.get(str(font_name or ""), str(font_name or ""))
    static_path = FONTS.get(font_name)
    if static_path:
        path = Path(static_path)
        if path.is_file():
            return str(path.resolve())
        win_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        candidate = win_fonts / Path(static_path).name
        if candidate.is_file():
            return str(candidate.resolve())
        raise FileNotFoundError(f"找不到字体文件：{font_name}（{Path(static_path).name}）。")

    spec = FONT_DOWNLOADS.get(font_name)
    if spec:
        filename, font_url, license_url = spec
        cache = _font_cache_dir()
        dest = cache / filename
        if not _is_valid_cached_font(dest):
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            _download_open_font(font_url, dest)
        # 一并保存 OFL 许可文本；仅在来源固定、响应地址匹配且大小受限时写入。
        license_path = cache / (Path(filename).stem + "-OFL.txt")
        if not license_path.is_file():
            try:
                if not _is_trusted_font_resource_url(license_url):
                    raise RuntimeError("字体许可证地址未通过校验。")
                req = urllib.request.Request(license_url, headers={"User-Agent": "video-watermark-font-loader"})
                with urllib.request.urlopen(req, timeout=10) as response:
                    if response.geturl() != license_url:
                        raise RuntimeError("字体许可证下载发生意外重定向。")
                    license_data = response.read(512 * 1024 + 1)
                if len(license_data) > 512 * 1024:
                    raise RuntimeError("字体许可证文件大小超出允许范围。")
                license_text = license_data.decode("utf-8", errors="strict")
                if "SIL OPEN FONT LICENSE" not in license_text.upper():
                    raise RuntimeError("下载的文本不是预期的 SIL Open Font License。")
                _atomic_write_bytes(license_path, license_data)
            except Exception:
                # 字体本身可用时，许可证副本下载失败不阻断渲染；发布包应另附许可证清单。
                pass
        return str(dest.resolve())
    raise FileNotFoundError(f"未知水印字体：{font_name}")


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
    """查找 FFmpeg/FFprobe：优先使用程序目录中的文件，其次 third_party/ffmpeg，最后使用系统 PATH。

    不再锁定特定版本或 SHA-256，方便用户替换为自己原先使用的 FFmpeg。
    """
    exe = name + (".exe" if platform.system() == "Windows" else "")
    candidates = []
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            base = Path(sys._MEIPASS)
            candidates.extend([base / exe, base / "third_party" / "ffmpeg" / exe])
        app_dir = Path(sys.executable).parent
    else:
        app_dir = Path(__file__).resolve().parent
    candidates.extend([
        app_dir / exe,
        app_dir / "third_party" / "ffmpeg" / exe,
        app_dir.parent / "third_party" / "ffmpeg" / exe,
    ])
    seen = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_file():
                return str(candidate.resolve())
        except OSError:
            continue

    # 本地没有时，回退到 PATH（与 1.5.2 的行为一致）。
    import shutil
    found = shutil.which(exe) or shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(
        f"未找到 {exe}。请将 ffmpeg.exe / ffprobe.exe 放到程序目录或 third_party\\ffmpeg，"
        f"也可以将其所在目录加入系统 PATH。"
    )


def detect_gpu_info():
    """读取 Windows 图形适配器名称；仅用于界面状态展示，不参与编码器选择。"""
    if platform.system() != "Windows":
        return ""

    commands = [
        [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-Command",
            "(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join \"`n\"",
        ],
        [
            "wmic.exe", "path", "win32_VideoController", "get", "name",
        ],
    ]
    for cmd in commands:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=4,
                               creationflags=NO_WINDOW)
            if r.returncode != 0:
                continue
            names = []
            for line in (r.stdout or "").splitlines():
                name = line.strip()
                if not name or name.lower() in {"name", "名称"}:
                    continue
                if name not in names:
                    names.append(name)
            if names:
                return " / ".join(names[:3])
        except Exception:
            continue
    return ""

def detect_encoder():
    """按 NVIDIA → Intel → AMD 的顺序，使用与原 1.5.2 相同的参数实际测试硬件编码器。

    关键兼容点：测试输入使用 nullsrc，并显式转换为 yuv420p；部分 FFmpeg/NVENC
    环境下，使用 color 输入或省略像素格式转换会导致 NVENC 测试失败，进而错误回退到 Intel。
    """
    ff = _find_bin("ffmpeg")
    detect_encoder.last_errors = []

    try:
        r = subprocess.run([ff, "-encoders"], capture_output=True, text=True,
                           timeout=8, creationflags=NO_WINDOW)
        encoder_list = (r.stdout or "") + (r.stderr or "")
    except Exception as exc:
        detect_encoder.last_errors.append(f"无法读取 FFmpeg 编码器列表：{exc}")
        return "libx264"

    candidates = [name for name in ("h264_nvenc", "h264_qsv", "h264_amf")
                  if name in encoder_list]
    if not candidates:
        detect_encoder.last_errors.append(
            "当前 FFmpeg 未列出 h264_nvenc / h264_qsv / h264_amf。")

    # 保持与已验证可识别 NVIDIA 的 1.5.2 相同的核心测试命令。
    for enc in candidates:
        cmd = [ff,
               "-f", "lavfi", "-i", "nullsrc=s=320x240:d=0.1",
               "-vf", "format=yuv420p",
               "-c:v", enc,
               "-frames:v", "1",
               "-f", "null", "-"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=12, creationflags=NO_WINDOW)
            if r.returncode == 0:
                return enc
            detail = (r.stderr or r.stdout or "").strip()
            detect_encoder.last_errors.append(
                f"{enc} 测试失败（退出码 {r.returncode}）："
                f"{detail[-900:] if detail else 'FFmpeg 未返回详细错误信息'}")
        except subprocess.TimeoutExpired:
            detect_encoder.last_errors.append(f"{enc} 初始化测试超时。")
        except Exception as exc:
            detect_encoder.last_errors.append(f"{enc} 测试异常：{exc}")

    # 额外收集 NVIDIA 驱动信息，供界面悬停提示排查；不影响编码器选择。
    try:
        smi = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=4, creationflags=NO_WINDOW)
        if smi.returncode == 0 and (smi.stdout or "").strip():
            detect_encoder.last_errors.append("nvidia-smi：" + smi.stdout.strip()[:500])
        elif smi.stderr and smi.stderr.strip():
            detect_encoder.last_errors.append("nvidia-smi：" + smi.stderr.strip()[:300])
    except Exception as exc:
        detect_encoder.last_errors.append(
            f"nvidia-smi 不可用（不一定代表驱动有问题）：{exc}")

    return "libx264"

detect_encoder.last_errors = []


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


def build_drawtext_filter(params):
    "正式输出与预览共用完全相同的水印滤镜，保持字号、位置、边距和描边比例一致。"
    text = _escape_drawtext_text(params.get("text", ""))
    color_hex = str(params.get("color", "#FFFFFF")).lstrip("#")
    opacity = max(0.0, min(1.0, float(params.get("opacity", 100)) / 100.0))
    margin = max(0, int(params.get("margin", 10)))
    position = params.get("position", "左上角")
    x_expr, y_expr = POSITIONS.get(position, POSITIONS["左上角"])(margin)
    font_file = _resolve_font_file(params.get("font", "通用无衬线（Arial）"))
    # FFmpeg filtergraph 中 Windows 盘符冒号必须转义；统一使用正斜杠减少路径解析歧义。
    font_ff = font_file.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    font_size = max(1, int(params.get("font_size", 20)))
    opacity_text = f"{opacity:.2f}"

    border_part = ""
    if params.get("border_on"):
        border_width = max(0, int(params.get("border_w", 2)))
        border_part = f":borderw={border_width}:bordercolor=0x000000@0.75"

    bg_part = ""
    if params.get("bg_on"):
        bg_hex = str(params.get("bg_color", "#000000")).lstrip("#")
        bg_part = f":box=1:boxcolor=0x{bg_hex}@{opacity_text}:boxborderw=6"

    drawtext = (f"drawtext=text='{text}':expansion=none:fontfile='{font_ff}':"
                f"fontsize={font_size}:fontcolor=0x{color_hex}@{opacity_text}:"
                f"x={x_expr}:y={y_expr}:"
                f"shadowcolor=black@0.45:shadowx=1:shadowy=1"
                + border_part + bg_part)
    return drawtext

class UpdateChecker(QThread):
    # tag, release page, changelog, setup asset url, asset name, sha256 digest
    result = pyqtSignal(str, str, str, str, str, str)
    error  = pyqtSignal(str)

    def run(self):
        try:
            req = urllib.request.Request(
                API_URL, headers={"User-Agent": "video-watermark-updater"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.geturl() != API_URL:
                    raise RuntimeError("更新 API 发生意外重定向，已取消检查。")
                payload = resp.read(4 * 1024 * 1024 + 1)
            if len(payload) > 4 * 1024 * 1024:
                raise RuntimeError("更新 API 响应过大，已取消检查。")
            data = json.loads(payload.decode("utf-8"))
            if not isinstance(data, dict):
                raise RuntimeError("更新 API 返回的数据格式无效。")

            tag = str(data.get("tag_name", "") or "")[:128]
            candidate_page = str(data.get("html_url", "") or "")
            url = candidate_page if _is_trusted_github_release_page_url(candidate_page) else RELEASES_URL
            body = str(data.get("body") or "").strip()[:1_000_000]

            setup_url = ""
            setup_name = ""
            setup_digest = ""
            for asset in data.get("assets", []) or []:
                name = str(asset.get("name", ""))
                candidate_url = str(asset.get("browser_download_url", "") or "")
                if (
                    name.lower().endswith("-setup.exe")
                    and name.lower().startswith("video-watermark-v")
                    and _is_trusted_github_asset_url(candidate_url)
                ):
                    setup_url = candidate_url
                    setup_name = Path(name).name
                    digest = str(asset.get("digest", "") or "")
                    if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
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
        temp_path = None
        try:
            if not _is_trusted_github_asset_url(self.url):
                raise RuntimeError("更新地址不是受信任的 GitHub 官方下载地址。")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", self.expected_sha256):
                raise RuntimeError("新版安装包缺少有效 SHA-256 校验值，已停止自动更新。")

            filename = str(self.filename or "video-watermark-update-Setup.exe")
            # 不接受路径分隔符或 Windows 文件名控制字符，避免 API 资产名影响落盘位置。
            if (Path(filename).name != filename or any(ch in filename for ch in '<>:\"/\\|?*')
                    or not filename.lower().endswith("-setup.exe")):
                raise RuntimeError("更新安装包文件名不符合安全要求。")

            update_dir = Path(tempfile.gettempdir()) / "VideoWatermarkUpdate"
            update_dir.mkdir(parents=True, exist_ok=True)
            if update_dir.is_symlink() or not update_dir.is_dir():
                raise RuntimeError("更新临时目录无效或不安全。")
            target = update_dir / filename
            fd, temp_name = tempfile.mkstemp(
                prefix=filename + ".", suffix=".download", dir=str(update_dir)
            )
            temp_path = Path(temp_name)
            req = urllib.request.Request(
                self.url, headers={"User-Agent": "video-watermark-updater"})
            downloaded = 0
            digest = hashlib.sha256()
            max_bytes = 1024 * 1024 * 1024  # 1 GiB 防止异常响应导致磁盘耗尽。

            with os.fdopen(fd, "wb") as f:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if not _is_trusted_github_download_response_url(resp.geturl()):
                        raise RuntimeError("更新下载发生到非受信任主机的重定向，已取消。")
                    raw_total = resp.headers.get("Content-Length", "0") or "0"
                    try:
                        total = int(raw_total)
                    except ValueError:
                        raise RuntimeError("更新服务器返回了无效的文件长度。")
                    if total < 0 or total > max_bytes:
                        raise RuntimeError("更新安装包大小超出允许范围，已取消下载。")
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            raise RuntimeError("更新安装包大小超出允许范围，已取消下载。")
                        f.write(chunk)
                        digest.update(chunk)
                        pct = int(downloaded * 100 / total) if total > 0 else 0
                        self.progress.emit(pct, downloaded)
                    f.flush()

            actual = digest.hexdigest().lower()
            if actual != self.expected_sha256:
                raise RuntimeError("更新文件校验失败，已停止安装。")

            os.replace(str(temp_path), str(target))
            temp_path = None
            self.progress.emit(100, downloaded)
            self.finished.emit(str(target))
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass


class StartupCheckWorker(QThread):
    """后台完成 FFmpeg 完整性 / 版本 / 编码器检测，避免启动阶段阻塞 Qt 主线程。"""
    finished = pyqtSignal(bool, str, str, str, str)  # ffmpeg_ok, version, encoder, gpu_name, error

    def run(self):
        version = ""
        ffmpeg_ok = False
        error = ""
        try:
            ff = _find_bin("ffmpeg")
            r = subprocess.run(
                [ff, "-version"], capture_output=True, text=True,
                timeout=5, creationflags=NO_WINDOW)
            if r.returncode == 0:
                ffmpeg_ok = True
                first = (r.stdout or "").splitlines()[0] if r.stdout else ""
                m = re.search(r"version\s+([^\s]+)", first)
                version = m.group(1) if m else ""
            else:
                error = "FFmpeg 启动失败。"
        except Exception as exc:
            error = str(exc)

        encoder = "libx264"
        gpu_name = ""
        if ffmpeg_ok:
            try:
                gpu_name = detect_gpu_info()
            except Exception:
                gpu_name = ""
            try:
                encoder = detect_encoder()
            except Exception:
                encoder = "libx264"
        self.finished.emit(ffmpeg_ok, version, encoder, gpu_name, error)


class WatermarkWorker(QThread):
    progress         = pyqtSignal(int, int, str)
    file_done        = pyqtSignal(int, bool, str)
    all_done         = pyqtSignal()
    encoder_detected = pyqtSignal(str)
    fatal_error      = pyqtSignal(str)

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
        try:
            enc_args = ENCODER_PROFILES[encoder][p["quality"]]
            # 预览与实际输出使用同一滤镜生成函数，避免样式参数出现偏差。
            vf = build_drawtext_filter(p)
        except Exception as exc:
            # 新增字体首次使用时需要下载；若网络或字体加载失败，明确报告并正常恢复界面。
            self.fatal_error.emit(f"无法加载水印字体或生成水印滤镜：{exc}")
            self.all_done.emit()
            return
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
        self.name.setTextFormat(Qt.TextFormat.PlainText)
        self.name.setToolTip(html.escape(p.name))
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
        self.info.setTextFormat(Qt.TextFormat.PlainText)
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


class Toast(QWidget):
    """现代卡片式非阻塞提示：单条显示、队列串行、主题自适应。"""
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowFlags(Qt.WindowType.Widget | Qt.WindowType.FramelessWindowHint)

        self.title_label = QLabel(self)
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setWordWrap(True)
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.subtitle_label = QLabel(self)
        self.subtitle_label.setTextFormat(Qt.TextFormat.PlainText)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 9, 16, 9)
        layout.setSpacing(2)
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        self._shadow = shadow
        self.setGraphicsEffect(shadow)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._finish)
        self.setMinimumWidth(300)
        self.setMaximumWidth(560)
        self.hide()

    def show_message(self, text, duration=2500, kind="info", dark=True):
        palettes = {
            "info": {
                "bg": "#202a33" if dark else "#ffffff",
                "title": "#69b7ff" if dark else "#1769aa",
                "body": "#edf5fb" if dark else "#2f3b43",
                "accent": "#3498db",
                "shadow": "#000000",
            },
            "success": {
                "bg": "#202c26" if dark else "#ffffff",
                "title": "#63d39a" if dark else "#18764e",
                "body": "#eef8f2" if dark else "#2f3934",
                "accent": "#2d9b68",
                "shadow": "#000000",
            },
            "warning": {
                "bg": "#30291d" if dark else "#ffffff",
                "title": "#f3bd63" if dark else "#99600b",
                "body": "#fff7e8" if dark else "#3f372b",
                "accent": "#d9942b",
                "shadow": "#000000",
            },
            "error": {
                "bg": "#321f20" if dark else "#ffffff",
                "title": "#ff8585" if dark else "#b42318",
                "body": "#fff0f0" if dark else "#402d2d",
                "accent": "#d94b4b",
                "shadow": "#000000",
            },
        }
        pal = palettes.get(kind, palettes["info"])

        # Toast 只是轻量提醒：只显示第一行标题，不展示详细正文。
        title = str(text).split("\n", 1)[0].strip() or "提示"
        subtitle = ""

        self.title_label.setText(title)
        self.title_label.setStyleSheet(
            f"QLabel{{background:transparent;color:{pal['title']};font-size:13px;"
            "font-weight:700;padding:0;margin:0;line-height:1.2;}}"
        )
        self.subtitle_label.setText(subtitle)
        self.subtitle_label.setVisible(bool(subtitle))
        self.subtitle_label.setStyleSheet(
            f"QLabel{{background:transparent;color:{pal['body']};font-size:12px;"
            "font-weight:400;padding:0;margin:0;line-height:1.25;}}"
        )

        self.setStyleSheet(
            f"QWidget{{background:{pal['bg']};border:1px solid "
            f"{'#39433e' if dark else '#e0e5e2'};border-left:5px solid {pal['accent']};"
            "border-radius:16px;}}"
        )
        self._shadow.setColor(QColor(0, 0, 0, 105 if dark else 55))

        parent = self.parentWidget()
        available = parent.width() if parent else 900
        # Toast 不宜过宽：保持截图中“右上角通知卡片”的紧凑比例，
        # 同时给长版本号/错误信息留出足够的自动换行空间。
        width = min(520, max(300, int(available * 0.34)))
        if available < 600:
            width = max(300, available - 32)
        self.setFixedWidth(width)

        # 根据内容重新计算高度。字体保持桌面应用常规字号，
        # 长文本通过换行增加高度，而不是放大字体。
        self.title_label.setFixedWidth(width - 36)
        self.subtitle_label.setFixedWidth(width - 36)
        self.title_label.adjustSize()
        self.subtitle_label.adjustSize()
        self.adjustSize()
        height = max(48, min(96, self.sizeHint().height()))
        self.setFixedHeight(height)
        self._reposition()
        self.raise_()
        self.show()
        self._timer.start(max(800, int(duration)))

    def _finish(self):
        self._timer.stop()
        self.hide()
        self.closed.emit()

    def _reposition(self):
        parent = self.parentWidget()
        if not parent:
            return
        margin_right = 18
        margin_top = 18
        x = max(8, parent.width() - self.width() - margin_right)
        y = max(8, margin_top)
        self.setGeometry(x, y, self.width(), self.height())

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)


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
        self._can_auto_update = bool(can_auto_update and setup_url and setup_digest)
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
        self.progress_label.setTextFormat(Qt.TextFormat.PlainText)
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


class PreviewFrameWorker(QThread):
    """后台抽取带水印的静态预览帧，防止读取大视频时卡住主界面。"""
    result = pyqtSignal(str, bytes, str)

    def __init__(self, filepath, params, timestamp=1):
        super().__init__()
        self.filepath = str(filepath)
        self.params = dict(params)
        self.timestamp = max(0, float(timestamp))
        self._process = None

    def stop(self):
        """关闭预览窗口时终止正在运行的 FFmpeg 子进程。"""
        self.requestInterruption()
        proc = self._process
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass

    def run(self):
        try:
            ff = _find_bin("ffmpeg")
            vf = build_drawtext_filter(self.params)
            attempts = [self.timestamp]
            if self.timestamp > 0:
                attempts.append(0.0)
            last_error = "未能从该视频提取画面。"
            for seek in attempts:
                if self.isInterruptionRequested():
                    return
                cmd = [
                    ff, "-hide_banner", "-loglevel", "error", "-ss", f"{seek:g}",
                    "-i", self.filepath, "-frames:v", "1", "-an",
                    "-vf", vf,
                    "-q:v", "2", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"
                ]
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=NO_WINDOW
                )
                self._process = proc
                try:
                    stdout, stderr = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    self._process = None
                    last_error = "预览取帧超时，请尝试较短的视频或检查 FFmpeg。"
                    break
                finally:
                    self._process = None
                if self.isInterruptionRequested():
                    return
                if proc.returncode == 0 and stdout:
                    self.result.emit(self.filepath, stdout, "")
                    return
                detail = (stderr or b"").decode("utf-8", errors="replace").strip()
                if detail:
                    last_error = detail[-700:]
            if not self.isInterruptionRequested():
                self.result.emit(self.filepath, b"", last_error)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.result.emit(self.filepath, b"", str(exc))


class PreviewDialog(QDialog):
    """多视频静态预览；胶囊按钮切换整体/局部显示，不播放视频。"""
    def __init__(self, parent, files, params, timestamp=1, dark=True):
        super().__init__(parent)
        self.setWindowTitle("水印效果预览")
        self.resize(900, 650)
        self.setMinimumSize(720, 500)
        self._files = list(files)
        self._params = dict(params)
        self._timestamp = max(0, float(timestamp))
        self._dark = dark
        self._worker = None
        self._requested_path = ""
        self._closing = False
        pal = PALETTE[dark]

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("预览视频"))
        self.file_cb = QComboBox()
        self.file_cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for path in self._files:
            self.file_cb.addItem(Path(path).name, path)
        self.file_cb.currentIndexChanged.connect(self._on_file_changed)
        controls.addWidget(self.file_cb, 1)
        self.prev_btn = QPushButton("上一条")
        self.next_btn = QPushButton("下一条")
        self.prev_btn.clicked.connect(lambda: self.file_cb.setCurrentIndex(max(0, self.file_cb.currentIndex() - 1)))
        self.next_btn.clicked.connect(lambda: self.file_cb.setCurrentIndex(min(self.file_cb.count() - 1, self.file_cb.currentIndex() + 1)))
        controls.addWidget(self.prev_btn)
        controls.addWidget(self.next_btn)

        # 单个胶囊按钮切换预览方式，默认整体预览，减少界面占用。
        # 局部模式只裁剪显示区域，水印坐标始终来自正式输出共用的滤镜。
        self._view_mode = "overall"
        self.view_mode_btn = QPushButton("整体预览")
        self.view_mode_btn.setFixedSize(116, 32)
        self.view_mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_mode_btn.setToolTip("点击切换整体预览 / 水印局部预览；不会更改实际输出。")
        self.view_mode_btn.setStyleSheet(
            "QPushButton{background:#263747;color:#8ecbff;border:1px solid #3498DB;"
            "border-radius:16px;padding:3px 12px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:#30495d;}"
        )
        self.view_mode_btn.clicked.connect(self._toggle_view_mode)
        controls.addWidget(self.view_mode_btn)
        root.addLayout(controls)

        self.image_label = QLabel("正在生成静态预览…")
        self.image_label.setTextFormat(Qt.TextFormat.PlainText)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(620, 360)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.image_label.setStyleSheet(
            f"QLabel{{background:{'#171717' if dark else '#ffffff'};border:1px solid {pal['border']};"
            f"border-radius:8px;color:{pal['text_muted']};padding:0px;margin:0px;}}"
        )
        root.addWidget(self.image_label, 1)

        self.status_lbl = QLabel("预览为静态画面，不播放视频。")
        self.status_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.status_lbl.setStyleSheet(f"font-size:11px;color:{pal['text_muted']};")
        root.addWidget(self.status_lbl)

        bottom = QHBoxLayout()
        bottom.addStretch()
        self.refresh_btn = QPushButton("重新生成")
        close_btn = QPushButton("关闭")
        self.refresh_btn.clicked.connect(self._load_current)
        close_btn.clicked.connect(self.accept)
        self.refresh_btn.setFixedHeight(32)
        close_btn.setFixedHeight(32)
        bottom.addWidget(self.refresh_btn)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)
        self.setStyleSheet(f"QDialog{{background:{pal['panel_bg']};}} QLabel{{background:transparent;}}")

        if self._files:
            self._load_current()

    def _toggle_view_mode(self):
        self._view_mode = "local" if self._view_mode == "overall" else "overall"
        self.view_mode_btn.setText("局部预览" if self._view_mode == "local" else "整体预览")
        self.view_mode_btn.setToolTip(
            "点击切换为整体预览" if self._view_mode == "local" else "点击切换为水印局部预览"
        )
        self._fit_pixmap()

    def _on_file_changed(self, _index):
        self.prev_btn.setEnabled(self.file_cb.currentIndex() > 0)
        self.next_btn.setEnabled(self.file_cb.currentIndex() < self.file_cb.count() - 1)
        self._load_current()

    def _load_current(self):
        path = self.file_cb.currentData()
        if not path:
            self.image_label.setText("没有可预览的视频。")
            return
        self._requested_path = str(path)
        self._source_pixmap = None
        self.prev_btn.setEnabled(self.file_cb.currentIndex() > 0)
        self.next_btn.setEnabled(self.file_cb.currentIndex() < self.file_cb.count() - 1)
        self.image_label.clear()
        self.image_label.setText("正在生成带水印的静态画面…")
        self.status_lbl.setText(f"正在读取：{Path(path).name}")
        if self._worker and self._worker.isRunning():
            # 不并发启动多个 FFmpeg：当前读取完成后会自动为最新选中的视频重新取帧。
            return
        self._start_worker(path)

    def _start_worker(self, path):
        worker = PreviewFrameWorker(path, self._params, self._timestamp)
        self._worker = worker
        worker.result.connect(self._on_frame_result)
        worker.finished.connect(lambda w=worker: self._on_worker_finished(w))
        worker.start()

    def _on_frame_result(self, path, data, error):
        if self._closing or str(path) != str(self.file_cb.currentData()):
            return
        if data:
            pix = QPixmap()
            if pix.loadFromData(data):
                self._source_pixmap = pix
                self._fit_pixmap()
                mode = self._view_mode
                mode_note = (
                    "当前为水印角落局部放大，仅用于看清文字样式；切换到整体预览可核对整张画面中的实际位置。"
                    if mode == "local" else
                    "整体画面：水印位置、字号和边距均按正式输出的原始分辨率计算。"
                )
                self.status_lbl.setText(
                    f"预览：{Path(path).name} · 源画面 {pix.width()}×{pix.height()} · {mode_note}"
                )
            else:
                self.image_label.setText("预览图片解码失败。")
                self.status_lbl.setText("请检查 FFmpeg 输出或尝试重新生成。")
        else:
            self.image_label.setText("无法生成预览画面。")
            self.status_lbl.setText(f"错误：{error}")

    def _on_worker_finished(self, worker):
        if self._worker is worker:
            self._worker = None
        wanted = str(self.file_cb.currentData() or "")
        if not self._closing and wanted and wanted != str(worker.filepath):
            QTimer.singleShot(0, lambda p=wanted: self._start_worker(p) if p == str(self.file_cb.currentData() or "") else None)
        worker.deleteLater()

    def _fit_pixmap(self):
        """整体模式显示完整帧；局部模式放大显示水印所在角落的裁剪区域。

        水印始终先按正式输出相同的 FFmpeg 滤镜绘制到原始分辨率帧上，
        局部模式只裁剪已生成的预览图，因此不会改变实际水印的坐标、字号或边距。
        """
        pix = getattr(self, "_source_pixmap", None)
        if not pix or pix.isNull():
            return

        display_pix = pix
        if self._view_mode == "local":
            width, height = pix.width(), pix.height()
            position = str(self._params.get("position", "左上角"))
            text = str(self._params.get("text", ""))
            font_size = max(1, int(self._params.get("font_size", 20)))
            margin = max(0, int(self._params.get("margin", 10)))

            # 估算水印所需宽度，以便长文字/大字号时扩大局部区域，尽量避免裁掉文字。
            # 估算只决定预览裁剪窗口，不参与正式 FFmpeg 滤镜。
            estimated_text_width = sum(
                font_size * (1.0 if ord(ch) > 0x2E80 else 0.62) for ch in text
            )
            estimated_fraction = (estimated_text_width + margin * 2 + font_size * 0.8) / max(1, width)
            crop_fraction = max(0.58, min(0.97, estimated_fraction + 0.06))
            crop_w = max(1, min(width, int(width * crop_fraction)))
            crop_h = max(1, min(height, int(height * crop_fraction)))

            x = width - crop_w if "右" in position else 0
            y = height - crop_h if "下" in position else 0
            display_pix = pix.copy(x, y, crop_w, crop_h)
            if display_pix.isNull():
                display_pix = pix

        # 关键修复：按 QLabel 真正可绘制的内容区域缩放，不使用 QWidget 外框尺寸。
        # 之前按 image_label.size() 缩放、但控件样式另有 padding，边缘水印会被控件裁掉；
        # 10px 源画面边距缩小后只有少量显示像素，最容易误判为水印跑到画面外。
        target_size = self.image_label.contentsRect().size()
        if target_size.width() < 2 or target_size.height() < 2:
            target_size = self.image_label.size()
        scaled_pix = display_pix.scaled(
            target_size, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.image_label.setPixmap(scaled_pix)

        if hasattr(self, "status_lbl") and self._source_pixmap is not None:
            path = self.file_cb.currentData()
            if self._view_mode == "local":
                self.status_lbl.setText(
                    f"局部预览：{Path(str(path)).name} · 放大显示“{self._params.get('position', '左上角')}”区域；"
                    "切换“整体预览”可检查水印在整张视频中的真实位置。"
                )
            else:
                self.status_lbl.setText(
                    f"整体预览：{Path(str(path)).name} · 原始画面 {pix.width()}×{pix.height()} · "
                    "水印按正式输出的原始坐标绘制。"
                )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_pixmap()

    def closeEvent(self, event):
        self._closing = True
        worker = self._worker
        if worker and worker.isRunning():
            worker.stop()
            worker.wait(2500)
        event.accept()


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
        self._toast = None
        self._toast_queue = []
        self._toast_active = False
        self._toast_started_at = 0.0
        self._toast_deadline = 0.0
        self._toast_duration_ms = 0
        self._load_app_preferences()
        self._toast_watchdog_timer = QTimer(self)
        self._toast_watchdog_timer.setInterval(1200)
        self._toast_watchdog_timer.timeout.connect(self._check_toast_watchdog)
        self._toast_watchdog_timer.start()
        self._bg_color = "#000000"     # 背景块默认颜色（随水印文字颜色自动联动）
        self._sec_labels = []          # 需要跟随主题重新着色的分区小标题
        self._div_frames = []          # 需要跟随主题重新着色的分隔线
        self._build_ui()
        self._theme()
        self._set_icon()
        # 启动阶段只安排后台任务，绝不在 GUI 线程执行 ffmpeg / 编码器探测。
        QTimer.singleShot(0, self._start_startup_checks)
        QTimer.singleShot(2000, self._auto_check_update)
        QTimer.singleShot(50, self._load_settings)  # 恢复上次使用的水印参数

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
        if hasattr(self, 'preview_btn'):
            self.preview_btn.setStyleSheet(
                f"QPushButton{{background:{pal['btn_bg']};border:1px solid {pal['btn_border']};"
                f"border-radius:8px;color:{pal['btn_fg']};font-size:13px;font-weight:600;}}"
                "QPushButton:hover{border-color:#3498DB;color:#3498DB;}"
                "QPushButton:disabled{color:#777;border-color:#888;}")
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
        self.tag_lbl.hide()

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
        for fn in FONTS:
            self.font_cb.addItem(fn)
        self.font_cb.setCurrentIndex(0)
        self.font_cb.setToolTip("新增 Roboto、Open Sans、Montserrat 三款免费开放字体；首次使用时会从 Google Fonts 官方仓库下载。")
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
        self.enc_hint.setTextFormat(Qt.TextFormat.PlainText)
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
        self.info_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.info_lbl.setStyleSheet("font-size:11px;color:#666;")
        self.info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_lbl.hide()
        self.total_bar = QProgressBar()
        self.total_bar.setRange(0,100); self.total_bar.setValue(0)
        self.total_bar.setTextVisible(False); self.total_bar.setFixedHeight(5)
        self.total_bar.hide()
        self.total_bar.setStyleSheet("QProgressBar{background:#2d2d2d;border-radius:2px;border:none;}QProgressBar::chunk{background:#27ae60;border-radius:2px;}")
        self.preview_btn = QPushButton("预览水印")
        self.preview_btn.setFixedSize(94, 44)
        self.preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview_btn.setStyleSheet(
            f"QPushButton{{background:{PALETTE[self._dark]['btn_bg']};border:1px solid {PALETTE[self._dark]['btn_border']};"
            f"border-radius:8px;color:{PALETTE[self._dark]['btn_fg']};font-size:13px;font-weight:600;}}"
            "QPushButton:hover{border-color:#3498DB;color:#3498DB;}")
        self.preview_btn.clicked.connect(self._open_preview)
        self.start_btn = QPushButton("开始处理")
        self.start_btn.setFixedHeight(44)
        self.start_btn.setStyleSheet(self._bstyle("#3498DB","#2980b9"))
        self.start_btn.clicked.connect(self._start_stop)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addWidget(self.preview_btn)
        action_row.addWidget(self.start_btn, 1)
        bl.addWidget(self.info_lbl); bl.addWidget(self.total_bar); bl.addLayout(action_row)
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

    def _show_toast(self, message, duration=2000, kind="info"):
        """提示统一入队，保证同时触发的多个提示按顺序展示，不互相覆盖。"""
        item = (str(message), max(500, int(duration)), kind)
        self._toast_queue.append(item)

        # 限制极端情况下的队列长度，避免异常循环导致无限增长。
        if len(self._toast_queue) > 50:
            del self._toast_queue[:-50]
        self._show_next_toast()

    def _show_next_toast(self):
        if self._toast_active or not self._toast_queue:
            return
        if self._toast is None:
            self._toast = Toast(self)
            self._toast.closed.connect(self._on_toast_closed)
        message, duration, kind = self._toast_queue.pop(0)
        self._toast_active = True
        self._toast_started_at = time.monotonic()
        self._toast_duration_ms = duration
        self._toast_deadline = self._toast_started_at + duration / 1000.0
        self._toast.show_message(message, duration=duration, kind=kind, dark=self._dark)

    def _on_toast_closed(self):
        self._toast_active = False
        self._toast_started_at = 0.0
        self._toast_deadline = 0.0
        self._toast_duration_ms = 0
        QTimer.singleShot(0, self._show_next_toast)

    def _check_toast_watchdog(self):
        """定期巡检气泡状态；发现计时器失效或异常残留时恢复队列。"""
        # 后台自检固定开启，不提供用户开关。
        # 最小化/关闭主窗口时，子控件的可见状态会变化，不应误判为气泡故障。
        if not self.isVisible() or self.isMinimized():
            return
        toast = self._toast
        now = time.monotonic()
        if not self._toast_active:
            if toast and toast.isVisible():
                toast._timer.stop()
                toast.hide()
            return
        if toast is None:
            self._recover_toast_state()
            return
        if not toast.isVisible():
            self._recover_toast_state()
            return
        deadline = self._toast_deadline
        if deadline and now > deadline + 3.0:
            self._recover_toast_state()

    def _recover_toast_state(self):
        '隐藏卡住的气泡并恢复提醒队列；不修改视频任务状态，也不伪造完成提示。'
        toast = self._toast
        self._toast_active = False
        self._toast_started_at = 0.0
        self._toast_deadline = 0.0
        self._toast_duration_ms = 0
        if toast:
            toast._timer.stop()
            toast.hide()
        # 当前卡住的那条提醒作废，后续队列继续正常显示。
        QTimer.singleShot(0, self._show_next_toast)

    def _reposition_toast(self):
        if self._toast and self._toast.isVisible():
            self._toast._reposition()

    def _refresh(self):
        n = len(self.file_rows)
        self.files_lbl.setText(f"已选文件 ({n})")
        self.start_btn.setText(f"开始处理  ({n} 个文件)" if n else "开始处理")
        if hasattr(self, "preview_btn"):
            self.preview_btn.setEnabled(n > 0 and not (self.worker and self.worker.isRunning()))

    def _load_app_preferences(self):
        """固定默认行为：开始前总是确认；启动检测静默显示；气泡自恢复与更新检测常开。"""
        self._confirm_before_start = True
        self._startup_toast_enabled = False
        self._toast_watchdog_enabled = True
        self._auto_update_enabled = True
        self._preview_time_sec = 1.0

    def _current_watermark_params(self):
        return {
            "text": self.wm_text.text(),
            "font": self.font_cb.currentText(),
            "font_size": self.font_sl.value(),
            "color": self.color_sw.get(),
            "opacity": self.op_sl.value(),
            "position": self._current_pos,
            "margin": self.mg_sl.value(),
            "border_on": self.border_chk.isChecked(),
            "border_w": self.border_sl.value(),
            "bg_on": self.bg_chk.isChecked(),
            "bg_color": self._bg_color,
        }

    def _open_preview(self):
        paths = [row.filepath for row in self.file_rows]
        if not paths:
            self._show_toast("请先添加视频文件，再预览水印效果", 2500, "info")
            return
        if self.worker and self.worker.isRunning():
            self._show_toast("视频处理中，暂时无法打开水印预览", 2500, "warning")
            return
        dlg = PreviewDialog(
            self, paths, self._current_watermark_params(),
            timestamp=self._preview_time_sec, dark=self._dark
        )
        dlg.exec()

    def _start_stop(self):
        # 正在处理中 → 点击变停止
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.start_btn.setText("正在停止…")
            self.start_btn.setEnabled(False)
            return

        # 没有文件
        if not self.file_rows:
            self._show_toast("请先添加视频文件", 2500, "info")
            return

        # 每次开始批量处理前都必须确认当前水印和位置，不提供关闭选项。
        confirm_box = QMessageBox(self)
        confirm_box.setWindowTitle("确认开始处理")
        confirm_box.setIcon(QMessageBox.Icon.Question)
        confirm_box.setTextFormat(Qt.TextFormat.PlainText)
        confirm_box.setText("是否开始批量处理？")
        wm_text = self.wm_text.text().strip() or "（空水印文字）"
        confirm_box.setInformativeText(
            f"待处理视频：{len(self.file_rows)} 个\n"
            f"当前水印：{wm_text}\n"
            f"水印位置：{self._current_pos}\n\n"
            "请确认水印内容和位置无误后再开始。"
        )
        start_now = confirm_box.addButton("开始处理", QMessageBox.ButtonRole.AcceptRole)
        confirm_box.addButton("再检查一下", QMessageBox.ButtonRole.RejectRole)
        confirm_box.setDefaultButton(start_now)
        confirm_box.exec()
        if confirm_box.clickedButton() is not start_now:
            return

        # 重置状态
        for r in self.file_rows: r.reset()
        self.total_bar.setValue(0); self.total_bar.show()
        self.info_lbl.setText("正在检测编码器…"); self.info_lbl.show()

        # 构建任务列表
        try:
            prefix = _validate_output_prefix(self.prefix_edit.text())
        except ValueError as exc:
            self._show_toast(f"输出文件名不安全\n{exc}", 4000, "warning")
            return

        out_dir = self.out_edit.text().strip()
        output_ext = self.format_cb.currentData() or ".mp4"
        tasks   = []
        output_paths = set()
        input_paths = {_canonical_path(r.filepath) for r in self.file_rows}
        for r in self.file_rows:
            p = Path(r.filepath)
            d = Path(out_dir) if out_dir else p.parent
            # MP4 输入 + MP4 输出：保持 MP4，不做额外格式转换。
            # 其他输入格式：输出统一改为用户选择的后缀，由当前 FFmpeg 处理流程完成转码。
            output_name = prefix + p.stem + output_ext
            output_path = d / output_name
            normalized_output = _canonical_path(output_path)
            if normalized_output in input_paths:
                _show_plain_message(
                    self, QMessageBox.Icon.Warning, "输出文件冲突",
                    f"输出文件不能覆盖输入文件：\n{output_path}"
                )
                return
            if normalized_output in output_paths:
                _show_plain_message(
                    self, QMessageBox.Icon.Warning, "输出文件冲突",
                    f"多个输入文件会写入同一个输出文件：\n{output_path}"
                )
                return
            output_paths.add(normalized_output)
            tasks.append({"input": str(p), "output": str(output_path)})

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
        self._failed_count = 0
        self._total = len(tasks)
        self.worker = WatermarkWorker(tasks, params)
        self.worker.encoder_detected.connect(self._on_encoder)
        self.worker.progress.connect(self._on_prog)
        self.worker.file_done.connect(self._on_done)
        self.worker.fatal_error.connect(self._on_worker_fatal_error)
        self.worker.all_done.connect(self._on_all_done)
        self.worker.start()

        self.start_btn.setText("停止处理")
        self.start_btn.setStyleSheet(self._bstyle("#c0392b", "#a93226"))
        self.preview_btn.setEnabled(False)
        self._set_right_panel_enabled(False)

    def _on_encoder(self, enc):
        name_map = {"h264_nvenc":"NVIDIA GPU 加速","h264_qsv":"Intel GPU 加速",
                    "h264_amf":"AMD GPU 加速","libx264":"CPU 软件编码"}
        label = name_map.get(enc, enc)
        color = "#27ae60" if enc != "libx264" else "#f39c12"
        self.enc_hint.setText(f"当前编码器：{label}")
        probe_errors = getattr(detect_encoder, "last_errors", [])
        if probe_errors:
            self.enc_hint.setToolTip("硬件编码器自检详情：\n" + "\n".join(probe_errors))
        elif enc == "h264_nvenc":
            self.enc_hint.setToolTip("NVIDIA NVENC 实际编码测试通过。")
        self.enc_hint.setStyleSheet(f"font-size:11px;color:{color};")

    def _on_worker_fatal_error(self, message):
        self._done_count = self._total
        self._failed_count = self._total
        for row in self.file_rows:
            row.set_done(False)
        self._show_toast(str(message), 6000, "error")

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
        if not ok:
            self._failed_count += 1
        if not ok: self._show_toast(f"第 {idx+1} 个文件处理失败：{msg}", 4000, "error")

    def _on_all_done(self):
        self.worker = None
        self.total_bar.setValue(100)
        self.info_lbl.setText(f"✓ 全部完成  ({self._total} 个文件)")
        self._set_right_panel_enabled(True)
        self._task_done = True
        self.start_btn.setEnabled(True)
        self.start_btn.setText(f"开始处理  ({len(self.file_rows)} 个文件)")
        self.start_btn.setStyleSheet(self._bstyle("#3498DB","#2980b9"))
        self.preview_btn.setEnabled(bool(self.file_rows))
        failed = self._failed_count
        if failed:
            self._show_toast(f"批量处理完成：成功 {self._total - failed} 个，失败 {failed} 个", 4000, "warning")
        else:
            self._show_toast(f"全部 {self._total} 个视频处理完成", 4000, "success")

    def _start_startup_checks(self):
        """启动后台环境检测；主窗口无需等待 FFmpeg 探测完成。"""
        self.tag_lbl.hide()
        self.start_btn.setEnabled(False)

        self._startup_worker = StartupCheckWorker()
        self._startup_worker.finished.connect(self._on_startup_checks_finished)
        self._startup_worker.start()

    def _on_startup_checks_finished(self, ok, version, encoder, gpu_name, error=""):
        if not ok:
            # 启动时先隐藏状态胶囊，失败时必须重新显示；否则用户只能看到 Toast，
            # 左上角原有的 FFmpeg 状态胶囊会一直处于隐藏状态。
            self.tag_lbl.setText("  ✗  未检出 FFmpeg")
            self.tag_lbl.setStyleSheet(
                "font-size:11px;background:#3d1a1a;color:#e74c3c;"
                "border-radius:10px;padding:2px 10px;")
            self.tag_lbl.show()
            self.tag_lbl.raise_()
            self.start_btn.setEnabled(False)
            if error and self._startup_toast_enabled:
                self._show_toast(f"FFmpeg 检测失败：{error}", 5000, "error")
            self._startup_worker = None
            return

        self.tag_lbl.hide()
        self._on_encoder(encoder)
        # 启动时只提醒 FFmpeg 和实际检测到的 GPU；编码器名称保留在主界面状态文字中，
        # 不再单独弹 Toast，避免启动阶段连续弹出过多通知。
        if self._startup_toast_enabled:
            self._show_toast("✓ FFmpeg 检测正常", 1800, "success")
        if encoder in ("h264_nvenc", "h264_qsv", "h264_amf"):
            # GPU 名称与硬件编码器分开检测，再按实际编码器选择对应显卡，
            # 避免机器同时存在核显+独显时把“第一块显卡”误报成当前加速设备。
            vendor_map = {
                "h264_nvenc": ("NVIDIA", "NVIDIA GPU 加速"),
                "h264_qsv": ("Intel", "Intel GPU 加速"),
                "h264_amf": ("AMD", "AMD GPU 加速"),
            }
            vendor, gpu_title = vendor_map[encoder]
            selected_gpu = ""
            for item in str(gpu_name or "").split(" / "):
                if vendor.lower() in item.lower():
                    selected_gpu = item.strip()
                    break
            if self._startup_toast_enabled:
                self._show_toast(f"⚡ {gpu_title}", 1800, "success")
            if selected_gpu:
                self.enc_hint.setToolTip(f"当前加速设备：{selected_gpu}")
        elif gpu_name:
            if self._startup_toast_enabled:
                self._show_toast("⚠ 已检测到 GPU，但硬件编码不可用", 2200, "warning")
            self.enc_hint.setToolTip(f"检测到：{gpu_name}")
        else:
            if self._startup_toast_enabled:
                self._show_toast("⚠ 未检测到可用 GPU 加速", 2200, "warning")
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
        '启动时必须检查软件更新，不提供取消选项。'
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
        current_semver = _parse_semver(APP_VERSION)
        latest_semver = _parse_semver(latest)
        if latest_semver and current_semver and latest_semver > current_semver:
            self.update_btn.setText("有可用更新")
            self.update_btn.setStyleSheet("QPushButton{background:#e67e22;border:none;border-radius:11px;color:#fff;font-size:11px;font-weight:600;padding:0 12px;}QPushButton:hover{background:#d35400;}QPushButton:pressed{background:#b94600;}")
            auto_ok = bool(setup_url and setup_digest and self._is_installed_build())
            self.update_btn.setToolTip(f"新版本 {latest} 可用，点击后可后台下载并直接更新" if auto_ok else f"新版本 {latest} 可用，点击查看下载页")
            if not silent:
                self._show_toast(f"发现新版本 {latest} · 点击左下角「有可用更新」查看", 2000, "info")
        else:
            self.update_btn.setText("当前最新版 ✓")
            self.update_btn.setStyleSheet("QPushButton{background:transparent;border:1px solid #27ae60;border-radius:11px;color:#27ae60;font-size:11px;padding:0 10px;}QPushButton:hover{background:rgba(39,174,96,0.1);}")
            self.update_btn.setToolTip("")
            if not silent:
                self._show_toast(f"当前已是最新版本 {APP_VERSION}", 2000, "success")

    def _on_update_error(self, msg):
        self.update_btn.setEnabled(True)
        silent = self._checking_silent
        raw = str(msg or "未知错误").strip()
        lower = raw.lower()

        # 将常见网络/API 错误翻译成可操作的提示，同时保留原始错误便于排查。
        if isinstance(getattr(self, "_last_update_exception", None), TimeoutError) or "timed out" in lower or "timeout" in lower:
            reason = "连接 GitHub 超时，网络可能不稳定或当前网络无法访问 GitHub。"
            advice = "请检查网络或代理设置后重试。"
        elif "urlerror" in lower or "name or service not known" in lower or "getaddrinfo failed" in lower or "11001" in lower or "nodename nor servname" in lower:
            reason = "无法连接 GitHub，可能是网络连接或 DNS 解析问题。"
            advice = "请确认可以正常打开 github.com，并检查网络、DNS 或代理设置。"
        elif "404" in lower or "not found" in lower:
            reason = "GitHub 更新仓库或最新 Release 不存在（HTTP 404）。"
            advice = "请检查程序中的仓库地址是否正确，以及 GitHub 上是否已发布正式版本。"
        elif "403" in lower or "rate limit" in lower:
            reason = "GitHub 暂时限制了更新查询请求（HTTP 403）。"
            advice = "请稍等一段时间后重试，或打开项目的 Releases 页面手动查看。"
        elif "ssl" in lower or "certificate" in lower or "cert_verify_failed" in lower:
            reason = "与 GitHub 建立安全连接失败，可能与系统证书、代理或 HTTPS 检查有关。"
            advice = "请检查系统日期时间、证书和代理设置。"
        elif "json" in lower or "decode" in lower:
            reason = "GitHub 返回的数据无法识别，可能是网络代理拦截或接口响应异常。"
            advice = "请稍后重试，或打开项目的 Releases 页面确认发布信息。"
        else:
            reason = "暂时无法获取 GitHub 的最新版本信息。"
            advice = "请检查网络后重试；也可以打开项目的 Releases 页面手动检查更新。"

        self.update_btn.setText("检测更新")
        self.update_btn.setStyleSheet(self._btn_style_default())
        detail = f"{reason}\n建议：{advice}\n原始错误：{raw}"
        self.update_btn.setToolTip(detail)

        if silent:
            # 静默启动检测只更新按钮状态和悬停详情，不弹出右上角气泡。
            pass
        else:
            # 检测失败时提供直达 Releases 页面的按钮，便于绕过 GitHub API 限流手动查看。
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setWindowTitle("版本检测失败")
            dialog.setTextFormat(Qt.TextFormat.PlainText)
            dialog.setText(f"{reason}\n\n{advice}\n\n详细错误信息：\n{raw}")
            releases_button = dialog.addButton("前往 Releases", QMessageBox.ButtonRole.ActionRole)
            dialog.addButton(QMessageBox.StandardButton.Ok)
            dialog.setDefaultButton(QMessageBox.StandardButton.Ok)
            dialog.exec()
            if dialog.clickedButton() is releases_button:
                QDesktopServices.openUrl(QUrl(RELEASES_URL))

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
        font = str(s.value("font", "通用无衬线（Arial）"))
        font = LEGACY_FONT_LABELS.get(font, font)
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

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._reposition_toast()

    def closeEvent(self, e):
        self._save_settings()
        if self.worker and self.worker.isRunning():
            self.worker.stop(); self.worker.wait(3000)
        if getattr(self, "_startup_worker", None) and self._startup_worker.isRunning():
            self._startup_worker.requestInterruption()
            self._startup_worker.wait(1000)
        e.accept()


def create_splash(dark=True):
    """创建跟随已保存主题的启动画面。"""
    w, h = 440, 260
    pix = QPixmap(w, h)
    pix.fill(Qt.GlobalColor.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # 根据上次保存的主题选择启动画面配色，避免主界面切换主题后启动画面仍固定深色。
    from PyQt6.QtGui import QBrush, QPen, QColor as QC
    from PyQt6.QtCore import QRectF
    bg = "#1a2035" if dark else "#f7f8fa"
    border = "#2a3a5a" if dark else "#d8dde5"
    title_color = "#ffffff" if dark else "#1f2937"
    sub_color = "#7f9ac4" if dark else "#667085"
    version_color = "#3498DB" if dark else "#1677c8"
    track_color = "#1e2d45" if dark else "#dfe5ec"
    hint_color = "#7b8aa6" if dark else "#98a2b3"
    progress_color = "#3498DB"
    p.setBrush(QBrush(QC(bg)))
    p.setPen(QPen(QC(border), 1))
    p.drawRoundedRect(QRectF(1, 1, w-2, h-2), 14, 14)

    # 软件名
    f_title = QFont("Microsoft YaHei", 22, QFont.Weight.Bold)
    p.setFont(f_title)
    p.setPen(QC(title_color))
    p.drawText(QRectF(12, 70, w-24, 44), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, "视频批量加水印")

    # 英文副标题
    f_sub = QFont("Segoe UI", 12)
    p.setFont(f_sub)
    p.setPen(QC(sub_color))
    p.drawText(QRectF(0, 118, w, 28), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, "Video Watermark Tool")

    # 版本号
    f_ver = QFont("Segoe UI", 11, QFont.Weight.DemiBold)
    p.setFont(f_ver)
    p.setPen(QC(version_color))
    p.drawText(QRectF(0, 148, w, 24), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, APP_VERSION)

    # 进度条背景
    bar_x, bar_y, bar_w, bar_h = (w-190)//2, 184, 190, 4
    p.setBrush(QBrush(QC(track_color)))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

    # 提示文字
    f_hint = QFont("Microsoft YaHei", 10)
    p.setFont(f_hint)
    p.setPen(QC(hint_color))
    p.drawText(QRectF(0, 198, w, 26), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, "正在加载…")

    p.end()
    return pix, (bar_x, bar_y, bar_w, bar_h)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("视频批量加水印")
    app.setApplicationVersion(APP_VERSION)

    # 启动画面跟随上次保存的主题；若尚无设置则默认深色。
    _settings = QSettings("VideoWatermark", "Settings")
    _saved_dark = _settings.value("dark_theme", True)
    if isinstance(_saved_dark, str):
        splash_dark = _saved_dark.strip().lower() not in ("false", "0", "no", "off")
    else:
        splash_dark = bool(_saved_dark)

    # 启动画面：先显示，再异步创建主窗口。这样打包后的 EXE 即使初始化较慢，
    # 用户也会立即看到反馈，而不是面对长时间空白。
    splash_pix, bar_info = create_splash(splash_dark)
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
        track_color = "#1e2d45" if splash_dark else "#dfe5ec"
        p2.setBrush(QBrush(QC(track_color)))
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
            _show_plain_message(None, QMessageBox.Icon.Critical, "启动失败", f"程序启动失败：\n{exc}")
            raise

    QTimer.singleShot(0, build_main_window)
    sys.exit(app.exec())
