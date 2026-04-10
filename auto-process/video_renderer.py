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

# ── 停止支援 ─────────────────────────────────────────────
_stop_event = None


def register_stop_event(event):
    """由 ProcessWorker 在開始時呼叫，傳入 threading.Event 以支援中止。"""
    global _stop_event
    _stop_event = event


def _is_stopped():
    return _stop_event is not None and _stop_event.is_set()


def _run_ffmpeg(cmd):
    """subprocess.run 的可中斷版本。

    每 0.5s 檢查一次 stop event，若已設定則 kill FFmpeg 子程序。
    回傳 (returncode, stderr_bytes)。
    停止時回傳 (-1, b'') 讓呼叫端可辨識為中止。
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=_SUBPROCESS_FLAGS,
    )
    while True:
        try:
            _, stderr = proc.communicate(timeout=0.5)
            return proc.returncode, stderr
        except subprocess.TimeoutExpired:
            if _is_stopped():
                proc.kill()
                proc.wait()
                logger.info("FFmpeg 已停止（使用者中止）")
                return -1, b""


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


def _render_segment_smart(source_video, start, end, output_path, temp_dir, video_props):
    """
    Smart segment extraction：只在頭部做邊界重新編碼，其餘 stream copy。

    H.264 stream copy 切割非 keyframe 位置時，B-frame 參考幀缺失導致頭部卡頓。
    解法：只 re-encode 從 start 到下一個 keyframe 的「頭部」片段，
    其餘維持 stream copy（快速）。尾部不 re-encode 避免重複幀問題。

    - 若 start 已對齊 keyframe：直接 stream copy 整段（最快）
    - 若 start 不在 keyframe：re-encode head + stream copy rest → TS concat

    Returns:
        True: 成功
        False: 失敗
        None: 使用者中止
    """
    ffmpeg = get_ffmpeg_path() or "ffmpeg"
    fps = str(video_props["fps"])
    pix_fmt = video_props.get("pix_fmt", "yuv420p")
    timescale = video_props.get("timescale")
    duration = end - start

    kf_start = _find_keyframe_after(source_video, start)

    # ── Case 1：start 已對齊 keyframe → 直接 stream copy ──────
    if kf_start <= start + 0.001:
        logger.info(f"  Start 已對齊 keyframe ({kf_start:.3f}s)，直接 stream copy")
        cmd = [
            ffmpeg, "-y",
            "-ss", str(start),
            "-t", str(duration),
            "-i", source_video,
            "-c", "copy",
            # 注意：不加 -avoid_negative_ts，從 keyframe seek 無負 DTS 問題，
            # 加了反而會偏移 PTS（start_time 從 0 變成 0.066s）
            output_path,
        ]
        rc, stderr = _run_ffmpeg(cmd)
        if rc == -1:
            return None
        if rc != 0:
            logger.error(f"Stream copy 失敗: {stderr.decode(errors='replace')[:200]}")
            return False
        return True

    # ── Case 2：start 不在 keyframe → head re-encode + rest stream copy ──
    # 短片段（整段在一個 GOP 內）→ 整段 re-encode（output seeking 精確）
    if kf_start >= end - 0.001:
        logger.info(f"  短片段（< 1 GOP），整段重新編碼: {start:.3f}s → {end:.3f}s")
        cmd = [
            ffmpeg, "-y",
            "-i", source_video,
            "-ss", str(start), "-to", str(end),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", pix_fmt,
            "-bf", "0",  # 禁用 B-frames：避免負 DTS pre-roll 造成 start_time 偏移
            "-r", fps,
            "-c:a", "aac", "-b:a", "192k",
        ]
        if timescale:
            cmd += ["-video_track_timescale", str(timescale)]
        cmd.append(output_path)
        rc, stderr = _run_ffmpeg(cmd)
        if rc == -1:
            return None
        if rc != 0:
            logger.error(f"短片段 re-encode 失敗: {stderr.decode(errors='replace')[:200]}")
            return False
        return True

    sub_temp = tempfile.mkdtemp(dir=temp_dir, prefix="smart_")

    # ── Head：output seeking re-encode（start → kf_start），精確到幀 ──
    head_path = os.path.join(sub_temp, "head.mp4")
    head_cmd = [
        ffmpeg, "-y",
        "-i", source_video,
        "-ss", str(start), "-to", str(kf_start),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", pix_fmt,
        "-bf", "0",  # 禁用 B-frames：避免負 DTS pre-roll 造成 start_time 偏移
        "-r", fps,
        "-c:a", "aac", "-b:a", "192k",
    ]
    if timescale:
        head_cmd += ["-video_track_timescale", str(timescale)]
    head_cmd.append(head_path)

    rc, stderr = _run_ffmpeg(head_cmd)
    if rc == -1:
        return None
    if rc != 0:
        logger.error(f"Head re-encode 失敗: {stderr.decode(errors='replace')[:200]}")
        return False
    logger.info(f"  Head re-encode: {start:.3f}s → {kf_start:.3f}s")

    # ── Rest：input seeking stream copy（kf_start → end），快速 ──
    rest_path = os.path.join(sub_temp, "rest.mp4")
    rest_duration = end - kf_start
    rest_cmd = [
        ffmpeg, "-y",
        "-ss", str(kf_start),
        "-t", str(rest_duration),
        "-i", source_video,
        "-c", "copy",
        # 不加 -avoid_negative_ts：從 keyframe seek，無負 DTS 問題
        rest_path,
    ]
    rc, stderr = _run_ffmpeg(rest_cmd)
    if rc == -1:
        return None
    if rc != 0:
        logger.error(f"Rest stream copy 失敗: {stderr.decode(errors='replace')[:200]}")
        return False
    logger.info(f"  Rest stream copy: {kf_start:.3f}s → {end:.3f}s ({rest_duration:.1f}s)")

    # ── Concat head + rest via TS（統一 timebase）──────────────
    return _concat_via_ts(ffmpeg, [head_path, rest_path], output_path, sub_temp)


def render_video(source_video, kept_segments, output_path,
                 progress_callback=None, error_callback=None):
    """
    裁剪並合併影片片段（邊界重新編碼確保無卡頓，中間 stream copy 保持速度）。

    Args:
        source_video: 來源影片路徑
        kept_segments: 要保留的片段清單 [{'start': 0.0, 'end': 10.0}, ...]
        output_path: 輸出影片路徑
        progress_callback: 可選回呼 callback(step, current, total) 用於 GUI 進度
        error_callback: 可選回呼 callback(error_msg) 將 FFmpeg 錯誤回傳給 GUI

    Returns:
        bool: 成功與否
    """
    logger.info("開始裁剪（邊界重新編碼 + 中間 stream copy）...")

    temp_dir = tempfile.mkdtemp(prefix=_TEMP_PREFIX)
    segment_files = []

    try:
        # 取得影片屬性（供邊界重新編碼使用）
        video_props = probe_video(source_video)
        if not video_props:
            logger.error("無法取得影片屬性")
            if error_callback:
                error_callback("無法偵測影片屬性")
            return False

        # 1. 逐 segment 切割（smart 邊界重新編碼）
        for i, segment in enumerate(kept_segments):
            if _is_stopped():
                return False

            start = segment["start"]
            end = segment["end"]
            duration = end - start

            if duration <= 0:
                continue

            seg_filename = os.path.join(temp_dir, f"seg_{i:04d}.mp4")

            result = _render_segment_smart(
                source_video, start, end, seg_filename, temp_dir, video_props
            )

            if result is None:
                return False  # 使用者中止
            if not result:
                logger.error(f"切割片段 {i} 失敗")
                if error_callback:
                    error_callback(f"片段 {i+1} 切割失敗")
                continue

            segment_files.append(seg_filename)
            logger.info(f"  切割片段 {i + 1}/{len(kept_segments)} ({duration:.1f}s)")
            if progress_callback:
                progress_callback("cutting", i + 1, len(kept_segments))

        if not segment_files:
            logger.warning("沒有保留的片段，跳過裁剪。")
            return False

        # 2. 合併所有片段（TS 中間格式避免 PTS 不連續）
        logger.info(f"合併 {len(segment_files)} 個片段...")
        if progress_callback:
            progress_callback("merging", 0, 1)

        ffmpeg = get_ffmpeg_path() or "ffmpeg"

        if len(segment_files) == 1:
            shutil.move(segment_files[0], output_path)
            logger.info(f"裁剪完成（單片段）: {output_path}")
            return True

        success = _concat_via_ts(ffmpeg, segment_files, output_path, temp_dir)
        if not success:
            if _is_stopped():
                return False
            logger.warning("TS concat 失敗，嘗試 concat demuxer")
            success = _concat_copy(ffmpeg, segment_files, output_path, temp_dir)

        if not success:
            logger.error("合併失敗")
            if error_callback:
                error_callback("合併失敗")
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
        rc, stderr_bytes = _run_ffmpeg(cmd)
        if rc == -1:
            return False  # 使用者中止
        if rc != 0:
            logger.error(f"音頻淡入淡出失敗: {stderr_bytes.decode(errors='replace')}")
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


def _find_keyframe_after(video_path, target_time, search_window=10):
    """找到 target_time 之後最近的 keyframe 時間。

    用於 stream copy 分割時確保 rest 從 keyframe 開始，
    避免解碼器因缺少參考幀而定格。

    Args:
        video_path: 影片路徑
        target_time: 目標時間（秒）
        search_window: 從 target_time 往後搜尋的秒數

    Returns:
        float: 找到的 keyframe 時間（≥ target_time），找不到則回傳 target_time
    """
    ffprobe = get_ffprobe_path() or "ffprobe"
    start = max(0, target_time - 0.1)
    cmd = [
        ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,flags",
        "-of", "csv=p=0",
        "-read_intervals", f"{start}%+{search_window}",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            creationflags=_SUBPROCESS_FLAGS)
    if result.returncode != 0:
        logger.warning(f"keyframe 偵測失敗，使用原始分割點 {target_time:.2f}s")
        return target_time

    for line in result.stdout.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split(',')
        if len(parts) < 2:
            continue
        try:
            pts = float(parts[0])
        except ValueError:
            continue
        if 'K' in parts[1] and pts >= target_time - 0.01:
            logger.info(f"Keyframe 分割: {target_time:.2f}s → {pts:.3f}s")
            return pts

    logger.warning(f"在 {search_window}s 內找不到 keyframe，使用原始分割點 {target_time:.2f}s")
    return target_time


def _find_keyframe_before(video_path, target_time, search_window=10):
    """找到 target_time 之前最近的 keyframe 時間。

    用於 stream copy 分割時確保 body 在 keyframe 結束，
    避免接合處的時間戳不連續。

    Args:
        video_path: 影片路徑
        target_time: 目標時間（秒）
        search_window: 從 target_time 往前搜尋的秒數

    Returns:
        float: 找到的 keyframe 時間（≤ target_time），找不到則回傳 target_time
    """
    ffprobe = get_ffprobe_path() or "ffprobe"
    start = max(0, target_time - search_window)
    cmd = [
        ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,flags",
        "-of", "csv=p=0",
        "-read_intervals", f"{start}%+{search_window + 1}",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            creationflags=_SUBPROCESS_FLAGS)
    if result.returncode != 0:
        logger.warning(f"keyframe 偵測失敗，使用原始分割點 {target_time:.2f}s")
        return target_time

    best = None
    for line in result.stdout.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split(',')
        if len(parts) < 2:
            continue
        try:
            pts = float(parts[0])
        except ValueError:
            continue
        if 'K' in parts[1] and pts <= target_time + 0.01:
            best = pts

    if best is not None:
        logger.info(f"Keyframe 分割 (before): {target_time:.2f}s → {best:.3f}s")
        return best

    logger.warning(f"在 {search_window}s 內找不到 keyframe，使用原始分割點 {target_time:.2f}s")
    return target_time


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
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-bf", "0",   # 禁用 B-frames：避免負 DTS pre-roll，確保 start_time=0
        "-c:a", "aac", "-b:a", "128k",
        "-r", str(fps),
        "-t", str(duration),
        "-shortest",
        output_path,
    ]

    rc, stderr_bytes = _run_ffmpeg(cmd)
    if rc == -1:
        return False  # 使用者中止
    if rc != 0:
        logger.error(f"建立圖片影片失敗: {stderr_bytes.decode(errors='replace')}")
        return False
    return True


def _get_raw_first_keyframe_pts(video_path):
    """取得影片第一個 keyframe 的原始 PTS（不受 MP4 edit list 影響）。

    用於 _concat_via_ts 計算正確的 ts_offset：
    stream-copied 片段的 raw PTS 不一定從 0 開始，
    必須用 target_start - raw_first_keyframe_pts 補償偏移。

    Returns:
        float: 第一個 keyframe 的 pts_time，失敗時回傳 0.0
    """
    ffprobe = get_ffprobe_path() or "ffprobe"
    cmd = [
        ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-show_packets",
        "-show_entries", "packet=pts_time,flags",
        "-read_intervals", "%+#30",  # 讀前 30 個封包，足以找到第一個 keyframe
        "-of", "csv=p=0",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            creationflags=_SUBPROCESS_FLAGS)
    if result.returncode != 0:
        logger.warning(f"無法探測 raw keyframe PTS: {video_path}，使用 0.0")
        return 0.0

    for line in result.stdout.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split(',')
        if len(parts) < 2:
            continue
        pts_str = parts[0]
        flags = parts[1] if len(parts) > 1 else ""
        if 'K' not in flags:
            continue
        if pts_str in ('N/A', 'n/a', ''):
            continue
        try:
            return float(pts_str)
        except ValueError:
            continue

    logger.warning(f"找不到 keyframe PTS: {video_path}，使用 0.0")
    return 0.0


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
    rc, _ = _run_ffmpeg(cmd)
    return rc == 0


def _concat_via_ts(ffmpeg, file_list, output_path, temp_dir):
    """透過 MPEG-TS 中間格式串接，解決不同 timebase 的 MP4 concat PTS 錯誤。

    MP4 concat demuxer 在混合 stream-copied 和 re-encoded 段落時，
    timebase 映射可能破壞 B-frame 的 PTS 順序。TS 使用固定 90kHz 時鐘，
    能統一所有段落的時間基準，避免 PTS 錯誤。

    ts_offset 補償說明：
    - target_start：此 segment 在輸出中的目標起始時間（edit-list 層面）
    - raw_first_kf_pts：此 segment raw 第一個 keyframe 的 PTS（不受 edit list 影響）
    - ts_offset = target_start - raw_first_kf_pts
    - 效果：將 raw PTS 空間「平移」，使第一個 keyframe 正好落在 target_start
    - 對 re-encoded 檔案（raw PTS ≈ 0）：ts_offset ≈ target_start（與原本相同）
    - 對 stream-copied 檔案（raw PTS ≈ split_point）：正確補償偏移
    """
    ts_files = []
    target_start = 0.0  # 此 segment 在輸出中的目標起始時間
    for i, fp in enumerate(file_list):
        ts_path = os.path.join(temp_dir, f"seg_{i}.ts")

        if i == 0:
            # 第一個 segment：固定從 0 開始，不做 raw PTS 補償
            # （補償會讓 B-frame pre-roll DTS 更負，造成 MPEG-TS 問題）
            ts_offset = 0.0
            raw_first_kf_pts = 0.0
        else:
            # 後續 segment：探測 raw 第一個 keyframe PTS（繞過 MP4 edit list）
            # 補償偏移，確保第一幀落在 target_start（前一 segment 的播放結束點）
            raw_first_kf_pts = _get_raw_first_keyframe_pts(fp)
            ts_offset = target_start - raw_first_kf_pts
        logger.debug(f"  TS seg {i}: target={target_start:.3f}s, "
                     f"raw_kf_pts={raw_first_kf_pts:.3f}s, ts_offset={ts_offset:.3f}s")

        cmd = [
            ffmpeg, "-y",
            "-i", fp,
            "-output_ts_offset", str(ts_offset),   # 強制此 segment 從 target_start 開始
            "-c:v", "copy",
            "-bsf:v", "h264_mp4toannexb",
            "-c:a", "aac", "-b:a", "192k",
            "-f", "mpegts",
            ts_path,
        ]
        rc, stderr_bytes = _run_ffmpeg(cmd)
        if rc == -1:
            return False  # 使用者中止
        if rc != 0:
            logger.error(f"轉換 TS 失敗: {stderr_bytes.decode(errors='replace')}")
            return False
        ts_files.append(ts_path)
        # 累加此 segment 的「播放時長」（edit-list 層面），作為下個 segment 的目標起始時間
        try:
            target_start += _get_duration(fp)
        except Exception:
            pass

    # 用 concat protocol 串接時間戳已對齊的 TS byte stream
    concat_input = "concat:" + "|".join(
        tp.replace("\\", "/") for tp in ts_files
    )
    cmd = [
        ffmpeg, "-y",
        "-i", concat_input,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        # make_zero：把 B-frame pre-roll 的微小負 DTS 歸零，確保 start_time=0.000
        # 此處作用於整個 TS timeline，不影響各 segment 間的相對位置（安全）
        "-avoid_negative_ts", "make_zero",
        output_path,
    ]
    rc, stderr_bytes = _run_ffmpeg(cmd)
    if rc != 0 and rc != -1:
        logger.error(f"TS concat 失敗: {stderr_bytes.decode(errors='replace')}")
    return rc == 0


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
                    # 找到 head_seconds 之後最近的 keyframe，確保 rest
                    # 從 keyframe 開始（stream copy 必須從 keyframe 才能正確解碼）
                    head_seconds = min(fade_duration + 2.0, main_dur)
                    split_at = _find_keyframe_after(current_video, head_seconds)
                    head_path = os.path.join(temp_dir, "head.mp4")
                    rest_path = os.path.join(temp_dir, "rest.mp4")

                    # head: 重新編碼到 split_at（keyframe 邊界）
                    head_cmd = [
                        ffmpeg, "-y",
                        "-i", current_video,
                        "-t", str(split_at),
                        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-bf", "0",  # 禁用 B-frames，確保 start_time=0
                        "-c:a", "aac", "-b:a", "192k",
                        "-r", str(props["fps"]),
                        head_path,
                    ]
                    # rest: input seeking 到 keyframe 精確位置（-c copy 從 keyframe 開始）
                    # 不加 -avoid_negative_ts：從 keyframe seek 無負 DTS，
                    # 加了反而把 PTS 偏移 ~66ms，TS concat 時產生空隙卡頓
                    rest_cmd = [
                        ffmpeg, "-y",
                        "-ss", str(split_at),
                        "-i", current_video,
                        "-c", "copy",
                        rest_path,
                    ]
                    rc_head, _ = _run_ffmpeg(head_cmd)
                    if rc_head == -1:
                        return None  # 使用者中止
                    rc_rest, _ = _run_ffmpeg(rest_cmd)
                    if rc_rest == -1:
                        return None  # 使用者中止

                    if rc_head != 0 or rc_rest != 0:
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
                            "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                            "-bf", "0",  # 禁用 B-frames，確保 start_time=0
                            "-c:a", "aac", "-b:a", "192k",
                            "-r", str(props["fps"]),
                            intro_transition,
                        ]
                        rc_xfade, _ = _run_ffmpeg(xfade_cmd)
                        if rc_xfade == -1:
                            return None  # 使用者中止

                        if rc_xfade != 0:
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

            # B1: 分割 body + tail（keyframe 對齊，確保 body 乾淨結尾）
            tail_seconds = min(fade_duration + 2.0, main_dur)
            body_end = main_dur - tail_seconds
            split_at = _find_keyframe_before(current_video, body_end)

            body_path = os.path.join(temp_dir, "body.mp4")
            tail_path = os.path.join(temp_dir, "tail.mp4")

            # body: -c copy 到 keyframe（快速，乾淨結尾）
            # 不加 -avoid_negative_ts：current_video 從 PTS=0 開始，無需偏移調整
            body_cmd = [
                ffmpeg, "-y",
                "-i", current_video,
                "-t", str(split_at),
                "-c", "copy",
                body_path,
            ]
            # tail: 從同一 keyframe 重新編碼（只有幾秒，極快）
            tail_cmd = [
                ffmpeg, "-y",
                "-ss", str(split_at),
                "-i", current_video,
                "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-bf", "0",  # 禁用 B-frames，確保 start_time=0
                "-c:a", "aac", "-b:a", "128k",
                "-r", str(props["fps"]),
                tail_path,
            ]

            rc_body, _ = _run_ffmpeg(body_cmd)
            if rc_body == -1:
                return None  # 使用者中止
            rc_tail, _ = _run_ffmpeg(tail_cmd)
            if rc_tail == -1:
                return None  # 使用者中止

            if rc_body != 0 or rc_tail != 0:
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
                "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-bf", "0",  # 禁用 B-frames，確保 start_time=0
                "-c:a", "aac", "-b:a", "128k",
                "-r", str(props["fps"]),
                transition_path,
            ]
            rc_xfade, stderr_bytes = _run_ffmpeg(xfade_cmd)
            if rc_xfade == -1:
                return None  # 使用者中止
            if rc_xfade != 0:
                stderr_text = stderr_bytes.decode(errors='replace')
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
