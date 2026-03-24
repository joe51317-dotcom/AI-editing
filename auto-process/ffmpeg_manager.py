"""
FFmpeg 路徑管理模組 — 自動偵測 bundled / PATH / 引導下載
確保非技術使用者不需要手動安裝 FFmpeg。
"""
import os
import sys
import shutil
import zipfile
import logging
import tempfile
import urllib.request

logger = logging.getLogger(__name__)

# FFmpeg 下載來源（gyan.dev 的 release essentials build）
FFMPEG_DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def _get_bundled_dir():
    """取得 bundled FFmpeg 目錄路徑（相對於此檔案或 PyInstaller frozen）"""
    if getattr(sys, "frozen", False):
        # PyInstaller 打包後
        base = sys._MEIPASS if hasattr(sys, "_MEIPASS") else os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "ffmpeg")


def _find_in_bundled(name):
    """在 bundled 目錄中搜尋執行檔"""
    bundled_dir = _get_bundled_dir()
    direct = os.path.join(bundled_dir, f"{name}.exe")
    if os.path.isfile(direct):
        return direct

    # gyan.dev 解壓後可能在子目錄 ffmpeg-X.X-essentials_build/bin/
    for root, dirs, files in os.walk(bundled_dir):
        for f in files:
            if f.lower() == f"{name}.exe":
                return os.path.join(root, f)
    return None


def get_ffmpeg_path():
    """
    取得 ffmpeg.exe 的完整路徑。
    優先順序：bundled → PATH → None
    """
    # 1. Bundled
    bundled = _find_in_bundled("ffmpeg")
    if bundled:
        return bundled

    # 2. PATH
    path_ffmpeg = shutil.which("ffmpeg")
    if path_ffmpeg:
        return path_ffmpeg

    return None


def get_ffprobe_path():
    """
    取得 ffprobe.exe 的完整路徑。
    優先順序：bundled → PATH → None
    """
    bundled = _find_in_bundled("ffprobe")
    if bundled:
        return bundled

    path_ffprobe = shutil.which("ffprobe")
    if path_ffprobe:
        return path_ffprobe

    return None


def check_ffmpeg():
    """檢查 FFmpeg 是否可用"""
    return get_ffmpeg_path() is not None and get_ffprobe_path() is not None


def download_ffmpeg(progress_callback=None):
    """
    下載 FFmpeg 靜態版到 bundled 目錄。

    Args:
        progress_callback: 可選回呼函式 callback(downloaded_bytes, total_bytes)

    Returns:
        bool: 下載成功與否
    """
    dest_dir = _get_bundled_dir()
    os.makedirs(dest_dir, exist_ok=True)

    logger.info(f"開始下載 FFmpeg: {FFMPEG_DOWNLOAD_URL}")

    try:
        # 下載 zip
        tmp_zip = os.path.join(tempfile.gettempdir(), "ffmpeg_download.zip")

        req = urllib.request.urlopen(FFMPEG_DOWNLOAD_URL)
        total = int(req.headers.get("Content-Length", 0))
        downloaded = 0
        chunk_size = 1024 * 1024  # 1MB

        with open(tmp_zip, "wb") as f:
            while True:
                chunk = req.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback(downloaded, total)

        logger.info("下載完成，解壓中...")

        # 解壓並尋找 ffmpeg.exe / ffprobe.exe
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            for member in zf.namelist():
                basename = os.path.basename(member).lower()
                if basename in ("ffmpeg.exe", "ffprobe.exe"):
                    # 直接解壓到 dest_dir（扁平化，不保留子目錄）
                    target_path = os.path.join(dest_dir, basename)
                    with zf.open(member) as src, open(target_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    logger.info(f"  已解壓: {basename}")

        # 清理 zip
        os.remove(tmp_zip)

        # 驗證
        if check_ffmpeg():
            logger.info("FFmpeg 安裝成功！")
            return True
        else:
            logger.error("解壓後找不到 ffmpeg.exe / ffprobe.exe")
            return False

    except Exception as e:
        logger.error(f"下載 FFmpeg 失敗: {e}")
        return False
