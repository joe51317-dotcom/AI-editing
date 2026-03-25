"""
無損影片裁剪模組 — FFmpeg stream copy + concat demuxer
從 auto-cut-agent/video_renderer.py 簡化而來，作為獨立套件使用。
"""
import os
import sys
import glob
import subprocess
import tempfile
import shutil
import logging
import time

from ffmpeg_manager import get_ffmpeg_path

logger = logging.getLogger(__name__)

# Windows 下隱藏 FFmpeg console 視窗
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# 暫存目錄前綴，用於啟動時清理殘留
_TEMP_PREFIX = "auto_process_render_"


def cleanup_stale_temp_dirs(max_age_hours=24):
    """
    清理殘留的暫存目錄（上次異常中斷時未清理的）。
    只刪除超過 max_age_hours 小時的目錄，避免誤刪正在使用中的。
    """
    temp_root = tempfile.gettempdir()
    cutoff = time.time() - (max_age_hours * 3600)
    cleaned = 0
    for path in glob.glob(os.path.join(temp_root, f"{_TEMP_PREFIX}*")):
        try:
            if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                shutil.rmtree(path)
                cleaned += 1
                logger.debug(f"清理殘留暫存目錄: {path}")
        except OSError:
            pass
    if cleaned:
        logger.info(f"已清理 {cleaned} 個殘留暫存目錄")


def render_video(source_video, kept_segments, output_path,
                 progress_callback=None, error_callback=None):
    """
    用 FFmpeg stream copy 切割並合併影片片段（無重新編碼，速度快）。

    Args:
        source_video: 來源影片路徑
        kept_segments: 要保留的片段清單 [{'start': 0.0, 'end': 10.0}, ...]
        output_path: 輸出影片路徑
        progress_callback: 可選回呼 callback(step, current, total) 用於 GUI 進度
        error_callback: 可選回呼 callback(error_msg) 將 FFmpeg 錯誤回傳給 GUI

    Returns:
        bool: 成功與否
    """
    logger.info("開始無損裁剪 (Stream Copy)...")

    temp_dir = tempfile.mkdtemp(prefix=_TEMP_PREFIX)
    segment_files = []

    try:
        # 1. 逐 segment 切割
        for i, segment in enumerate(kept_segments):
            start = segment["start"]
            end = segment["end"]
            duration = end - start

            if duration <= 0:
                continue

            seg_filename = os.path.join(temp_dir, f"seg_{i:04d}.mp4")

            ffmpeg = get_ffmpeg_path() or "ffmpeg"
            cmd = [
                ffmpeg,
                "-y",
                "-ss", str(start),
                "-t", str(duration),
                "-i", source_video,
                "-c", "copy",
                "-avoid_negative_ts", "1",
                seg_filename,
            ]

            result = subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=_SUBPROCESS_FLAGS,
            )

            if result.returncode != 0:
                stderr_text = result.stderr.decode(errors='replace')
                logger.error(f"切割片段 {i} 失敗: {stderr_text}")
                if error_callback:
                    # 取 stderr 最後一行作為使用者可讀摘要
                    summary = stderr_text.strip().split('\n')[-1] if stderr_text.strip() else "未知錯誤"
                    error_callback(f"片段 {i+1} 切割失敗: {summary}")
                continue

            segment_files.append(seg_filename)
            logger.info(f"  切割片段 {i + 1}/{len(kept_segments)} ({duration:.1f}s)")
            if progress_callback:
                progress_callback("cutting", i + 1, len(kept_segments))

        if not segment_files:
            logger.warning("沒有保留的片段，跳過裁剪。")
            return False

        # 2. Concat demuxer 合併
        logger.info(f"合併 {len(segment_files)} 個片段...")

        list_path = os.path.join(temp_dir, "mylist.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for seg in segment_files:
                safe_path = seg.replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        if progress_callback:
            progress_callback("merging", 0, 1)

        concat_cmd = [
            get_ffmpeg_path() or "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            output_path,
        ]

        result = subprocess.run(
            concat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=_SUBPROCESS_FLAGS,
        )

        if result.returncode != 0:
            stderr_text = result.stderr.decode(errors='replace')
            logger.error(f"合併失敗: {stderr_text}")
            if error_callback:
                summary = stderr_text.strip().split('\n')[-1] if stderr_text.strip() else "未知錯誤"
                error_callback(f"合併失敗: {summary}")
            return False

        logger.info(f"裁剪完成: {output_path}")
        return True

    except Exception as e:
        logger.error(f"裁剪失敗: {e}")
        return False

    finally:
        try:
            shutil.rmtree(temp_dir)
        except OSError:
            pass
