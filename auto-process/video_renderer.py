"""
無損影片裁剪模組 — FFmpeg stream copy + concat demuxer
從 auto-cut-agent/video_renderer.py 簡化而來，作為獨立套件使用。
"""
import os
import subprocess
import tempfile
import shutil
import logging

from ffmpeg_manager import get_ffmpeg_path

logger = logging.getLogger(__name__)


def render_video(source_video, kept_segments, output_path, progress_callback=None):
    """
    用 FFmpeg stream copy 切割並合併影片片段（無重新編碼，速度快）。

    Args:
        source_video: 來源影片路徑
        kept_segments: 要保留的片段清單 [{'start': 0.0, 'end': 10.0}, ...]
        output_path: 輸出影片路徑
        progress_callback: 可選回呼 callback(step, current, total) 用於 GUI 進度

    Returns:
        bool: 成功與否
    """
    logger.info("開始無損裁剪 (Stream Copy)...")

    temp_dir = tempfile.mkdtemp(prefix="auto_process_render_")
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
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )

            if result.returncode != 0:
                logger.error(f"切割片段 {i} 失敗: {result.stderr.decode(errors='replace')}")
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
            concat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )

        if result.returncode != 0:
            logger.error(f"合併失敗: {result.stderr.decode(errors='replace')}")
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
