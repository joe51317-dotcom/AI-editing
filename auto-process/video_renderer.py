"""
無損影片裁剪模組 — FFmpeg stream copy + concat demuxer
從 auto-cut-agent/video_renderer.py 簡化而來，作為獨立套件使用。
"""
import os
import re
import sys
import glob
import subprocess
import tempfile
import shutil
import logging
import time

import json

from ffmpeg_manager import get_ffmpeg_path, get_ffprobe_path

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


# ── 片頭/片尾功能 ─────────────────────────────────────


def probe_video(video_path):
    """
    用 FFprobe 取得影片的視訊/音訊屬性。

    Returns:
        dict: {'width', 'height', 'fps', 'pix_fmt', 'audio_sample_rate', 'audio_channels'}
        失敗回傳 None
    """
    ffprobe = get_ffprobe_path() or "ffprobe"

    # 視訊資訊
    cmd_v = [
        ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,pix_fmt",
        "-of", "json",
        video_path,
    ]
    result_v = subprocess.run(cmd_v, capture_output=True,
                              creationflags=_SUBPROCESS_FLAGS)
    if result_v.returncode != 0:
        logger.error(f"FFprobe 視訊失敗: {result_v.stderr.decode(errors='replace')}")
        return None

    try:
        data_v = json.loads(result_v.stdout)
        stream = data_v["streams"][0]
        # r_frame_rate 格式如 "30000/1001"
        num, den = stream["r_frame_rate"].split("/")
        fps = round(float(num) / float(den), 2)
        props = {
            "width": stream["width"],
            "height": stream["height"],
            "fps": fps,
            "pix_fmt": stream.get("pix_fmt", "yuv420p"),
        }
    except (json.JSONDecodeError, KeyError, IndexError, ValueError) as e:
        logger.error(f"解析視訊資訊失敗: {e}")
        return None

    # 音訊資訊
    cmd_a = [
        ffprobe, "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels",
        "-of", "json",
        video_path,
    ]
    result_a = subprocess.run(cmd_a, capture_output=True,
                              creationflags=_SUBPROCESS_FLAGS)
    if result_a.returncode == 0:
        try:
            data_a = json.loads(result_a.stdout)
            astream = data_a["streams"][0]
            props["audio_sample_rate"] = int(astream["sample_rate"])
            props["audio_channels"] = int(astream["channels"])
        except (json.JSONDecodeError, KeyError, IndexError, ValueError):
            props["audio_sample_rate"] = None
            props["audio_channels"] = None
    else:
        props["audio_sample_rate"] = None
        props["audio_channels"] = None

    logger.info(f"影片屬性: {props['width']}x{props['height']} @ {props['fps']}fps")
    return props


def create_image_video(image_path, output_path, duration, fade_duration, video_props):
    """
    將靜態圖片轉換為帶淡入淡出效果的影片（含靜音音軌）。
    輸出的 codec/解析度/fps 會匹配來源影片，以便後續用 concat demuxer 合併。
    """
    ffmpeg = get_ffmpeg_path() or "ffmpeg"
    w = video_props["width"]
    h = video_props["height"]
    fps = video_props["fps"]
    pix_fmt = video_props.get("pix_fmt", "yuv420p")
    sample_rate = video_props.get("audio_sample_rate") or 48000
    channels = video_props.get("audio_channels") or 2
    cl = "stereo" if channels >= 2 else "mono"

    # 視訊濾鏡：縮放 + 置中 + 淡入淡出
    vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
    if fade_duration > 0:
        fade_out_start = max(0, duration - fade_duration)
        vf += f",fade=t=in:d={fade_duration},fade=t=out:st={fade_out_start}:d={fade_duration}"
    vf += f",format={pix_fmt}"

    cmd = [
        ffmpeg, "-y",
        "-loop", "1", "-i", image_path,
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl={cl}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "128k",
        "-r", str(fps),
        "-t", str(duration),
        "-shortest",
        output_path,
    ]

    result = subprocess.run(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=_SUBPROCESS_FLAGS,
    )
    if result.returncode != 0:
        logger.error(f"建立圖片影片失敗: {result.stderr.decode(errors='replace')}")
        return False
    return True


def add_intro_outro(video_path, intro_image=None, outro_image=None,
                    intro_duration=3, outro_duration=3, fade_duration=0.5):
    """
    為影片加上片頭/片尾圖片。

    用 concat demuxer（-c copy）合併 intro + 主影片 + outro，
    不重新編碼主影片，速度快。

    Args:
        video_path: 裁剪後的影片路徑
        intro_image/outro_image: 圖片路徑（None 則跳過）
        intro_duration/outro_duration: 圖片持續秒數
        fade_duration: 淡入淡出秒數

    Returns:
        str: 成功回傳輸出路徑，失敗回傳 None
    """
    if not intro_image and not outro_image:
        return video_path

    props = probe_video(video_path)
    if not props:
        logger.error("無法偵測影片屬性，跳過片頭/片尾")
        return None

    temp_dir = tempfile.mkdtemp(prefix=_TEMP_PREFIX)

    try:
        concat_files = []

        # 建立片頭影片
        if intro_image and os.path.isfile(intro_image):
            intro_video = os.path.join(temp_dir, "intro.mp4")
            if create_image_video(intro_image, intro_video, intro_duration,
                                  fade_duration, props):
                concat_files.append(intro_video)
                logger.info(f"片頭影片已建立 ({intro_duration}s)")
            else:
                logger.warning("建立片頭影片失敗，繼續不加片頭")

        # 主影片
        concat_files.append(video_path)

        # 建立片尾影片
        if outro_image and os.path.isfile(outro_image):
            outro_video = os.path.join(temp_dir, "outro.mp4")
            if create_image_video(outro_image, outro_video, outro_duration,
                                  fade_duration, props):
                concat_files.append(outro_video)
                logger.info(f"片尾影片已建立 ({outro_duration}s)")
            else:
                logger.warning("建立片尾影片失敗，繼續不加片尾")

        # 沒有成功建立任何片頭/片尾
        if len(concat_files) == 1:
            return video_path

        # concat demuxer 合併
        list_path = os.path.join(temp_dir, "concat_list.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for fp in concat_files:
                safe = fp.replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{safe}'\n")

        output_path = os.path.join(temp_dir, "output.mp4")
        ffmpeg = get_ffmpeg_path() or "ffmpeg"

        cmd = [
            ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            output_path,
        ]
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=_SUBPROCESS_FLAGS,
        )

        if result.returncode != 0:
            # 嘗試 filter_complex 重新編碼合併
            logger.warning("concat demuxer 失敗，嘗試重新編碼合併...")
            cmd_fb = [ffmpeg, "-y"]
            for fp in concat_files:
                cmd_fb.extend(["-i", fp])
            n = len(concat_files)
            fc = "".join(f"[{i}:v][{i}:a]" for i in range(n))
            fc += f"concat=n={n}:v=1:a=1[outv][outa]"
            cmd_fb.extend([
                "-filter_complex", fc,
                "-map", "[outv]", "-map", "[outa]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-b:a", "128k",
                "-r", str(props["fps"]),
                output_path,
            ])
            result = subprocess.run(
                cmd_fb, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=_SUBPROCESS_FLAGS,
            )
            if result.returncode != 0:
                stderr_text = result.stderr.decode(errors='replace')
                logger.error(f"合併片頭/片尾失敗: {stderr_text}")
                return None

        # 用合併結果取代原始檔案
        shutil.move(output_path, video_path)
        logger.info(f"片頭/片尾已加入: {os.path.basename(video_path)}")
        return video_path

    except Exception as e:
        logger.error(f"加入片頭/片尾時發生錯誤: {e}")
        return None

    finally:
        try:
            shutil.rmtree(temp_dir)
        except OSError:
            pass
