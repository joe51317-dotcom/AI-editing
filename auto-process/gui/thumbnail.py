"""
縮圖工具 — 用 FFmpeg 從影片抽取單幀作為預覽縮圖
"""
import os
import sys
import subprocess
import logging

logger = logging.getLogger(__name__)

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def extract_frame(video_path: str, time_sec: float, out_path: str) -> bool:
    """
    從影片的指定時間點抽取一幀，存為圖片。
    Returns True on success.
    """
    from ffmpeg_manager import get_ffmpeg_path
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        logger.warning("找不到 FFmpeg，無法抽取縮圖")
        return False

    try:
        cmd = [
            ffmpeg,
            "-y",
            "-ss", str(max(0.0, time_sec)),
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "5",
            out_path,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=20,
            creationflags=_SUBPROCESS_FLAGS,
        )
        return result.returncode == 0 and os.path.isfile(out_path)
    except Exception as e:
        logger.warning(f"縮圖抽取失敗: {e}")
        return False
