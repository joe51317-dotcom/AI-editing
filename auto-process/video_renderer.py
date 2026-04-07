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


def _get_duration(video_path):
    """用 FFprobe 取得影片時長（秒）"""
    ffprobe = get_ffprobe_path() or "ffprobe"
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            creationflags=_SUBPROCESS_FLAGS)
    if result.returncode != 0:
        raise RuntimeError(f"FFprobe 取得時長失敗: {result.stderr}")
    return float(result.stdout.strip())


def apply_audio_fade(video_path, fade_duration=1.0):
    """
    為影片頭尾加上 Constant Power 音頻淡入淡出（只重新編碼音訊）。

    Args:
        video_path: 影片路徑（直接覆蓋原檔）
        fade_duration: 淡入/淡出秒數（預設 1 秒）

    Returns:
        bool: 成功與否
    """
    try:
        dur = _get_duration(video_path)
    except RuntimeError as e:
        logger.error(f"apply_audio_fade: {e}")
        return False

    fade_out_start = max(0.0, dur - fade_duration)
    af = (
        f"afade=t=in:d={fade_duration}:curve=qsin,"
        f"afade=t=out:st={fade_out_start}:d={fade_duration}:curve=qsin"
    )

    ffmpeg = get_ffmpeg_path() or "ffmpeg"
    temp_dir = tempfile.mkdtemp(prefix=_TEMP_PREFIX)
    try:
        out = os.path.join(temp_dir, "audio_fade.mp4")
        cmd = [
            ffmpeg, "-y",
            "-i", video_path,
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-af", af,
            out,
        ]
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=_SUBPROCESS_FLAGS,
        )
        if result.returncode != 0:
            logger.error(f"音頻淡入淡出失敗: {result.stderr.decode(errors='replace')}")
            return False
        shutil.move(out, video_path)
        logger.info(f"已套用音頻淡入淡出 ({fade_duration}s)")
        return True
    except Exception as e:
        logger.error(f"apply_audio_fade 發生錯誤: {e}")
        return False
    finally:
        try:
            shutil.rmtree(temp_dir)
        except OSError:
            pass


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
        "-show_entries", "stream=width,height,r_frame_rate,pix_fmt,time_base",
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
        # time_base 格式如 "1/90000"，取分母作為 container timescale
        timescale = None
        tb = stream.get("time_base", "")
        if "/" in tb:
            try:
                timescale = int(tb.split("/")[1])
            except (ValueError, IndexError):
                pass
        props = {
            "width": stream["width"],
            "height": stream["height"],
            "fps": fps,
            "pix_fmt": stream.get("pix_fmt", "yuv420p"),
            "timescale": timescale,
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


def create_image_video(image_path, output_path, duration, fade_duration, video_props,
                       skip_fade_in=False, skip_fade_out=False):
    """
    將靜態圖片轉換為影片（含靜音音軌）。
    輸出的 codec/解析度/fps 會匹配來源影片，以便後續用 concat demuxer 合併。

    Args:
        skip_fade_in: 為 True 時跳過 fade-in（片尾用，xfade 會處理進場過渡）
        skip_fade_out: 為 True 時跳過 fade-out（片尾用，圖片維持到結束）
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
        if not skip_fade_in:
            vf += f",fade=t=in:d={fade_duration}"
        if not skip_fade_out:
            vf += f",fade=t=out:st={fade_out_start}:d={fade_duration}"
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


def _concat_copy(ffmpeg, file_list, output_path, temp_dir):
    """用 concat demuxer (-c copy) 快速串接多個影片檔案。"""
    list_path = os.path.join(temp_dir, f"concat_{os.path.basename(output_path)}.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for fp in file_list:
            safe = fp.replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{safe}'\n")
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
    return result.returncode == 0


def _concat_via_ts(ffmpeg, file_list, output_path, temp_dir):
    """透過 MPEG-TS 中間格式串接，解決不同 timebase 的 MP4 concat PTS 錯誤。

    MP4 concat demuxer 在混合 stream-copied 和 re-encoded 段落時，
    timebase 映射可能破壞 B-frame 的 PTS 順序。TS 使用固定 90kHz 時鐘，
    能統一所有段落的時間基準，避免 PTS 錯誤。
    """
    ts_files = []
    for i, fp in enumerate(file_list):
        ts_path = os.path.join(temp_dir, f"seg_{i}.ts")
        cmd = [
            ffmpeg, "-y",
            "-i", fp,
            "-c:v", "copy",
            "-bsf:v", "h264_mp4toannexb",
            "-c:a", "aac", "-b:a", "192k",   # 統一音頻為 AAC（TS 不支援 pcm_f32le 等格式）
            "-f", "mpegts",
            ts_path,
        ]
        r = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=_SUBPROCESS_FLAGS,
        )
        if r.returncode != 0:
            logger.error(f"轉換 TS 失敗: {r.stderr.decode(errors='replace')}")
            return False
        ts_files.append(ts_path)

    list_path = os.path.join(temp_dir, f"concat_ts_{os.path.basename(output_path)}.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for tp in ts_files:
            safe = tp.replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        output_path,
    ]
    result = subprocess.run(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=_SUBPROCESS_FLAGS,
    )
    if result.returncode != 0:
        logger.error(f"TS concat 失敗: {result.stderr.decode(errors='replace')}")
    return result.returncode == 0


def add_intro_outro(video_path, intro_image=None, outro_image=None,
                    intro_duration=3, outro_duration=3, fade_duration=0.5):
    """
    為影片加上片頭/片尾圖片。

    - Intro：concat demuxer（-c copy）串接，快速
    - Outro：Split Tail — 只重新編碼尾巴幾秒做 xfade cross dissolve，
      其餘維持 -c copy，三小時影片仍可在分鐘級完成
    - 不處理音頻淡入淡出（由呼叫端另行呼叫 apply_audio_fade）

    Args:
        video_path: 裁剪後的影片路徑（成功時會被覆蓋）
        intro_image/outro_image: 圖片路徑（None 則跳過）
        intro_duration/outro_duration: 圖片持續秒數
        fade_duration: 視訊淡入淡出秒數（xfade duration）

    Returns:
        str: 成功回傳輸出路徑，失敗回傳 None
    """
    if not intro_image and not outro_image:
        return video_path

    props = probe_video(video_path)
    if not props:
        logger.error("無法偵測影片屬性，跳過片頭/片尾")
        return None

    ffmpeg = get_ffmpeg_path() or "ffmpeg"
    temp_dir = tempfile.mkdtemp(prefix=_TEMP_PREFIX)

    try:
        current_video = video_path

        # ── Phase A：Intro cross dissolve（Split Head）─────────────
        if intro_image and os.path.isfile(intro_image):
            # A0: 建立 intro 影片（無 fade，xfade 處理過渡，第一幀即為圖片）
            intro_video = os.path.join(temp_dir, "intro.mp4")
            if not create_image_video(intro_image, intro_video, intro_duration,
                                      fade_duration, props,
                                      skip_fade_in=True, skip_fade_out=True):
                logger.warning("建立片頭影片失敗，繼續不加片頭")
            else:
                try:
                    main_dur = _get_duration(current_video)
                except RuntimeError as e:
                    logger.error(f"無法取得影片時長: {e}")
                    main_dur = 0

                if main_dur > 0:
                    # A1: 分割 head + rest
                    head_seconds = min(fade_duration + 2.0, main_dur)
                    head_path = os.path.join(temp_dir, "head.mp4")
                    rest_path = os.path.join(temp_dir, "rest.mp4")

                    head_cmd = [
                        ffmpeg, "-y",
                        "-i", current_video,
                        "-t", str(head_seconds),
                        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                        "-c:a", "aac", "-b:a", "192k",
                        "-r", str(props["fps"]),
                        head_path,
                    ]
                    rest_cmd = [
                        ffmpeg, "-y",
                        "-ss", str(head_seconds),
                        "-i", current_video,
                        "-c", "copy",
                        "-avoid_negative_ts", "1",
                        rest_path,
                    ]
                    r_head = subprocess.run(
                        head_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        creationflags=_SUBPROCESS_FLAGS,
                    )
                    r_rest = subprocess.run(
                        rest_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        creationflags=_SUBPROCESS_FLAGS,
                    )

                    if r_head.returncode != 0 or r_rest.returncode != 0:
                        logger.warning("分割 head/rest 失敗，跳過片頭")
                    else:
                        # A2: xfade intro + head
                        xfade_offset = max(0.0, intro_duration - fade_duration)
                        fc = (
                            f"[0:v][1:v]xfade=transition=fade:duration={fade_duration}"
                            f":offset={xfade_offset}[outv];"
                            f"[0:a][1:a]acrossfade=d={fade_duration}"
                            f":c1=qsin:c2=qsin[outa]"
                        )
                        intro_transition = os.path.join(temp_dir, "intro_transition.mp4")
                        xfade_cmd = [
                            ffmpeg, "-y",
                            "-i", intro_video,
                            "-i", head_path,
                            "-filter_complex", fc,
                            "-map", "[outv]", "-map", "[outa]",
                            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                            "-c:a", "aac", "-b:a", "192k",
                            "-r", str(props["fps"]),
                            intro_transition,
                        ]
                        r_xfade = subprocess.run(
                            xfade_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=_SUBPROCESS_FLAGS,
                        )

                        if r_xfade.returncode != 0:
                            logger.warning("片頭 xfade 失敗，跳過片頭")
                        else:
                            # A3: concat intro_transition + rest（透過 TS）
                            intro_merged = os.path.join(temp_dir, "intro_merged.mp4")
                            if _concat_via_ts(ffmpeg, [intro_transition, rest_path],
                                              intro_merged, temp_dir):
                                current_video = intro_merged
                                logger.info(f"片頭 cross dissolve 已套用 ({intro_duration}s)")
                            else:
                                logger.warning("片頭 concat 失敗，跳過片頭")

        # ── Phase B：Outro cross dissolve（Split Tail）────────────
        if outro_image and os.path.isfile(outro_image):
            # B0: 建立 outro 影片（無 fade-in/fade-out，xfade 處理進場，圖片維持到結束）
            outro_video = os.path.join(temp_dir, "outro.mp4")
            if not create_image_video(outro_image, outro_video, outro_duration,
                                      fade_duration, props,
                                      skip_fade_in=True, skip_fade_out=True):
                logger.warning("建立片尾影片失敗，降級為 concat 串接")
                outro_fb = os.path.join(temp_dir, "outro_fb.mp4")
                if create_image_video(outro_image, outro_fb, outro_duration,
                                      fade_duration, props):
                    output_path = os.path.join(temp_dir, "output.mp4")
                    _concat_copy(ffmpeg, [current_video, outro_fb],
                                 output_path, temp_dir)
                    shutil.move(output_path, video_path)
                return video_path

            try:
                main_dur = _get_duration(current_video)
            except RuntimeError as e:
                logger.error(f"無法取得影片時長: {e}")
                return None

            # B1: 分割 body + tail
            tail_seconds = min(fade_duration + 2.0, main_dur)
            body_end = main_dur - tail_seconds

            body_path = os.path.join(temp_dir, "body.mp4")
            tail_path = os.path.join(temp_dir, "tail.mp4")

            # body: -c copy（快速）
            body_cmd = [
                ffmpeg, "-y",
                "-i", current_video,
                "-t", str(body_end),
                "-c", "copy",
                "-avoid_negative_ts", "1",
                body_path,
            ]
            # tail: 重新編碼（只有幾秒，極快）
            tail_cmd = [
                ffmpeg, "-y",
                "-ss", str(body_end),
                "-i", current_video,
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-b:a", "128k",
                "-r", str(props["fps"]),
                tail_path,
            ]

            r_body = subprocess.run(
                body_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=_SUBPROCESS_FLAGS,
            )
            r_tail = subprocess.run(
                tail_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=_SUBPROCESS_FLAGS,
            )

            if r_body.returncode != 0 or r_tail.returncode != 0:
                logger.error("分割 body/tail 失敗，降級為 concat 串接")
                output_path = os.path.join(temp_dir, "output.mp4")
                _concat_copy(ffmpeg, [current_video, outro_video],
                             output_path, temp_dir)
                shutil.move(output_path, video_path)
                return video_path

            # B2: xfade tail + outro（只處理幾秒，極快）
            try:
                tail_dur = _get_duration(tail_path)
            except RuntimeError:
                tail_dur = tail_seconds

            xfade_offset = max(0.0, tail_dur - fade_duration)
            fc = (
                f"[0:v][1:v]xfade=transition=fade:duration={fade_duration}"
                f":offset={xfade_offset}[outv];"
                f"[0:a][1:a]acrossfade=d={fade_duration}"
                f":c1=qsin:c2=qsin[outa]"
            )

            transition_path = os.path.join(temp_dir, "transition.mp4")
            xfade_cmd = [
                ffmpeg, "-y",
                "-i", tail_path,
                "-i", outro_video,
                "-filter_complex", fc,
                "-map", "[outv]", "-map", "[outa]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-b:a", "128k",
                "-r", str(props["fps"]),
                transition_path,
            ]
            r_xfade = subprocess.run(
                xfade_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=_SUBPROCESS_FLAGS,
            )
            if r_xfade.returncode != 0:
                stderr_text = r_xfade.stderr.decode(errors='replace')
                logger.error(f"xfade 失敗: {stderr_text}，降級為 concat 串接")
                output_path = os.path.join(temp_dir, "output.mp4")
                _concat_copy(ffmpeg, [current_video, outro_video],
                             output_path, temp_dir)
                shutil.move(output_path, video_path)
                return video_path

            # B3: concat body + transition（透過 TS 中間格式避免 PTS 錯誤）
            output_path = os.path.join(temp_dir, "output.mp4")
            if not _concat_via_ts(ffmpeg, [body_path, transition_path],
                                  output_path, temp_dir):
                logger.warning("TS concat 失敗，降級為直接 concat")
                if not _concat_copy(ffmpeg, [body_path, transition_path],
                                    output_path, temp_dir):
                    logger.error("concat body+transition 失敗")
                    return None

            shutil.move(output_path, video_path)
            logger.info(f"片尾 cross dissolve 已套用 ({outro_duration}s, xfade={fade_duration}s)")

        else:
            # 只有 intro，current_video 已是 concat 結果
            if current_video != video_path:
                shutil.move(current_video, video_path)

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
