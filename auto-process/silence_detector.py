"""
靜音偵測模組 — 使用 FFmpeg silencedetect 濾波器
偵測影片中的靜音區段，分類為開頭空白、結尾空白、中間休息。
"""
import re
import sys
import subprocess
import logging

from ffmpeg_manager import get_ffmpeg_path, get_ffprobe_path

logger = logging.getLogger(__name__)

# Windows 下隱藏 FFmpeg console 視窗
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# ── 停止支援 ─────────────────────────────────────────────
_stop_event = None


def register_stop_event(event):
    """由 ProcessWorker 在開始時呼叫，傳入 threading.Event 以支援中止。"""
    global _stop_event
    _stop_event = event


def _is_stopped():
    return _stop_event is not None and _stop_event.is_set()


def get_video_duration(video_path):
    """用 FFprobe 取得影片總長度（秒）"""
    cmd = [
        get_ffprobe_path() or "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True,
                             creationflags=_SUBPROCESS_FLAGS)
    if result.returncode != 0:
        raise RuntimeError(f"FFprobe 失敗: {result.stderr}")
    return float(result.stdout.strip())


def _parse_ffmpeg_time(line):
    """從 FFmpeg stderr 行解析 time= 的秒數"""
    m = re.search(r"time=(\d+):(\d+):(\d+\.?\d*)", line)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return None


def detect_silence(video_path, noise_db=-30, min_duration=10,
                   total_duration=None, progress_callback=None):
    """
    用 FFmpeg silencedetect 偵測所有靜音區段。

    Args:
        video_path: 影片路徑
        noise_db: 靜音判定分貝閾值（預設 -30dB）
        min_duration: 最短靜音持續秒數（預設 10 秒）
        total_duration: 影片總長度（秒），用於計算進度百分比
        progress_callback: 可選回呼 callback(pct, text)

    Returns:
        list[dict]: 靜音區段清單 [{'start': float, 'end': float, 'duration': float}, ...]
    """
    cmd = [
        get_ffmpeg_path() or "ffmpeg",
        "-i", video_path,
        "-vn",
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null",
        "-",
    ]

    logger.info(f"偵測靜音 (noise={noise_db}dB, min_duration={min_duration}s)...")

    # 用 Popen 即時讀取 stderr 以回報進度
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=_SUBPROCESS_FLAGS,
    )

    stderr_lines = []
    last_report = 0
    try:
        for raw_line in proc.stderr:
            if _is_stopped():
                proc.kill()
                proc.wait()
                logger.info("靜音偵測已停止（使用者中止）")
                return []

            line = raw_line.decode("utf-8", errors="replace")
            stderr_lines.append(line)

            if progress_callback and total_duration and total_duration > 0:
                t = _parse_ffmpeg_time(line)
                if t is not None and t - last_report >= 2:  # 每 2 秒更新一次
                    last_report = t
                    pct = min(t / total_duration, 0.99)
                    mins = int(t) // 60
                    secs = int(t) % 60
                    progress_callback(pct, f"偵測靜音中... {mins}:{secs:02d} / {int(total_duration)//60}:{int(total_duration)%60:02d}")

        proc.wait()
    except Exception:
        proc.kill()
        proc.wait()
        raise
    stderr = "".join(stderr_lines)
    silence_regions = []

    # 解析 silence_start
    starts = re.findall(r"silence_start:\s*([\d.]+)", stderr)
    # 解析 silence_end（含 duration）
    ends = re.findall(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)", stderr)

    for i, start_str in enumerate(starts):
        start = float(start_str)
        if i < len(ends):
            end = float(ends[i][0])
            duration = float(ends[i][1])
        else:
            # 最後一段靜音可能沒有 end（延伸到檔案結尾）
            end = None
            duration = None

        silence_regions.append({
            "start": start,
            "end": end,
            "duration": duration,
        })

    logger.info(f"偵測到 {len(silence_regions)} 段靜音")
    return silence_regions


def _extract_rms_windows(video_path, start_time, duration=30,
                         sample_rate=8000, window_ms=200):
    """
    提取音訊並計算每個時間視窗的 RMS 音量。

    Args:
        video_path: 影片路徑
        start_time: 提取起點（秒）
        duration: 提取長度（秒）
        sample_rate: 取樣率（預設 8kHz，足夠分析音量）
        window_ms: 視窗大小（毫秒）

    Returns:
        list[float]: 每個視窗的 RMS 值
    """
    import array

    cmd = [
        get_ffmpeg_path() or "ffmpeg",
        "-ss", str(start_time),
        "-t", str(duration),
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", "1",
        "-f", "s16le",
        "pipe:1",
    ]
    if _is_stopped():
        return []

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             creationflags=_SUBPROCESS_FLAGS)

    if not result.stdout:
        return []

    samples = array.array("h")
    samples.frombytes(result.stdout)

    window_size = sample_rate * window_ms // 1000
    rms_values = []

    for i in range(0, len(samples) - window_size + 1, window_size):
        window = samples[i:i + window_size]
        rms = (sum(s * s for s in window) / len(window)) ** 0.5
        rms_values.append(rms)

    return rms_values


def find_speech_boundary(video_path, time_point, direction="forward",
                         search_duration=60, max_trim=20):
    """
    用持續性驗證的音量跳變偵測語音邊界。

    基線取自「休息區」（boundary 的另一側）。
    偵測到音量超過門檻後，驗證是否為持續語音：
    檢查後續 10 秒是否有 ≥3s 回到「休息區音量」的沉默。
    有 → 短暫噪音（學生、麥克風），跳過。無 → 真正語音。

    驗證用 break_rms（而非 threshold）判定沉默，避免把語音中
    自然的音節間隔誤判為「回到沉默」。

    Args:
        video_path: 影片路徑
        time_point: boundary 時間點（秒）
        direction: "forward" 找語音開始，"backward" 找語音結束
        search_duration: 搜尋範圍（秒，預設 60）
        max_trim: 最大修剪秒數（超過則不修剪，預設 20）

    Returns:
        float: 修正後的時間點（秒）
    """
    window_ms = 200
    window_sec = window_ms / 1000  # 0.2s
    baseline_duration = 3  # 從休息區取 3 秒作為基線

    if direction == "forward":
        # 從 boundary 前 3 秒開始提取（前 3 秒在休息區 = 安靜基線）
        ss = max(0, time_point - baseline_duration)
        actual_lead = time_point - ss
        total_duration = actual_lead + search_duration

        if _is_stopped():
            return time_point
        rms_values = _extract_rms_windows(video_path, ss, total_duration,
                                          window_ms=window_ms)
        if not rms_values:
            return time_point

        # 休息區基線（用 median 排除 lead 期間的噪音汙染）
        n_lead = max(1, int(actual_lead / window_sec))
        n_lead = min(n_lead, len(rms_values))
        sorted_lead = sorted(rms_values[:n_lead])
        break_rms = sorted_lead[len(sorted_lead) // 2]

        # 語音門檻：休息環境 × 3（≈+10dB）
        threshold = max(break_rms * 3, 100)
        # 沉默判定：回到休息區附近音量（用於驗證）
        silence_level = max(break_rms * 1.5, 50)

        logger.info(f"    邊界偵測(forward): break_rms={break_rms:.0f}, threshold={threshold:.0f}, silence_level={silence_level:.0f}")

        # 從 boundary 位置開始搜尋語音
        start_idx = n_lead
        consecutive = 0
        for i in range(start_idx, len(rms_values)):
            if rms_values[i] > threshold:
                consecutive += 1
                if consecutive >= 3:
                    # 持續性驗證：後續 20 秒內是否回到休息區音量 ≥8s？
                    # 8 秒閾值：上課中停頓通常 < 8s，真正休息後沉默 > 8s
                    verify_end = min(i + 1 + 100, len(rms_values))  # 20s
                    silence_streak = 0
                    is_transient = False
                    for j in range(i + 1, verify_end):
                        if rms_values[j] < silence_level:
                            silence_streak += 1
                            if silence_streak >= 40:  # 8 秒連續沉默
                                is_transient = True
                                break
                        else:
                            silence_streak = 0

                    if is_transient:
                        # 短暫噪音爆發 — 跳過，繼續找
                        consecutive = 0
                        continue

                    # 通過驗證 — 這是真正的語音開始
                    onset_idx = i - 2
                    onset_time = (onset_idx - start_idx) * window_sec
                    if onset_time <= 0.3:
                        return time_point
                    if onset_time > max_trim:
                        return time_point
                    return time_point + max(0, onset_time - 0.3)
            else:
                consecutive = 0

        return time_point

    else:  # backward
        # 提取範圍：boundary 前 search_duration + 後延伸檢查 + 深處基線
        forward_check = 20   # 延伸檢查：boundary 後 20 秒（講師/主持人可能還在講）
        baseline_skip = 15   # 跳過 15 秒避免語音殘餘汙染基線

        ss = max(0, time_point - search_duration)
        total_duration = search_duration + forward_check + baseline_skip + baseline_duration

        if _is_stopped():
            return time_point
        rms_values = _extract_rms_windows(video_path, ss, total_duration,
                                          window_ms=window_ms)
        if not rms_values:
            return time_point

        # 基線：從休息區深處取（跳過 boundary 後 25 秒）
        baseline_start_idx = int((search_duration + forward_check + baseline_skip) / window_sec)
        if baseline_start_idx < len(rms_values):
            baseline_windows = rms_values[baseline_start_idx:]
        else:
            # 影片尾端不夠長，退回使用最後 3 秒
            n_tail = max(1, int(baseline_duration / window_sec))
            baseline_windows = rms_values[-n_tail:]

        if not baseline_windows:
            return time_point

        sorted_baseline = sorted(baseline_windows)
        break_rms = sorted_baseline[len(sorted_baseline) // 2]
        threshold = max(break_rms * 3, 100)
        silence_level = max(break_rms * 1.5, 50)
        logger.info(f"    邊界偵測(backward): break_rms={break_rms:.0f}, threshold={threshold:.0f}, silence_level={silence_level:.0f}")

        boundary_idx = int(search_duration / window_sec)

        # Phase 1: 延伸檢查 — 語音是否延續到 boundary 之後？
        # 用較高門檻區分「講師語音」和「環境噪音突起」
        forward_end_idx = min(int((search_duration + forward_check) / window_sec),
                              len(rms_values))
        ext_threshold = max(threshold, 200)  # 延伸檢查門檻至少 200
        silence_end_thresh = 10  # 10 windows = 2 秒連續沉默 → 語音結束

        speech_end_idx = boundary_idx
        consecutive_silence = 0
        for i in range(boundary_idx, forward_end_idx):
            if rms_values[i] > ext_threshold:
                speech_end_idx = i
                consecutive_silence = 0
            else:
                consecutive_silence += 1
                if consecutive_silence >= silence_end_thresh:
                    break  # 語音已結束

        if speech_end_idx > boundary_idx + 2:  # 語音延伸 > 0.6s
            speech_count = sum(1 for i in range(boundary_idx, speech_end_idx + 1)
                               if rms_values[i] > ext_threshold)
            if speech_count >= 3:  # 至少 0.6s 語音
                extension = (speech_end_idx - boundary_idx) * window_sec
                if extension <= max_trim:
                    logger.info(f"    語音延伸 {extension:.1f}s 超過 boundary (ext_threshold={ext_threshold:.0f})")
                    return time_point + extension + 0.3

        # Phase 2: 標準反向修剪搜尋
        boundary_idx = min(boundary_idx, len(rms_values) - 1)

        consecutive = 0
        for i in range(boundary_idx - 1, -1, -1):
            if rms_values[i] > threshold:
                consecutive += 1
                if consecutive >= 3:
                    # 持續性驗證（反向）：前方 20 秒內是否回到沉默 ≥8s？
                    verify_start = max(0, i - 100)
                    silence_streak = 0
                    is_transient = False
                    for j in range(i - 1, verify_start - 1, -1):
                        if rms_values[j] < silence_level:
                            silence_streak += 1
                            if silence_streak >= 40:
                                is_transient = True
                                break
                        else:
                            silence_streak = 0

                    if is_transient:
                        consecutive = 0
                        continue

                    end_idx = i + 3
                    end_time = end_idx * window_sec
                    trim_amount = search_duration - end_time
                    if trim_amount <= 0.3:
                        return time_point
                    if trim_amount > max_trim:
                        return time_point
                    return ss + end_time + 0.3
            else:
                consecutive = 0

        return time_point


def split_into_parts(video_path, speech_threshold_db=-20, min_duration=10, break_threshold=300, progress_callback=None):
    """
    偵測靜音與環境音，按長休息切割成多段影片。

    使用 speech_threshold_db（預設 -20dB）偵測「非講課」段落，
    包含完全靜音和環境音（如麥克風沒關的休息時間）。

    - 長休息（>break_threshold）作為切割點，產生多個 part
    - 每個 part 的開頭/結尾靜音會被移除
    - 1 段休息 → 2 個 part，2 段休息 → 3 個 part

    Args:
        video_path: 影片路徑
        speech_threshold_db: 講課語音門檻（低於此 dB 視為非講課，預設 -20）
        min_duration: 最短靜音秒數
        break_threshold: 中間休息判定秒數（預設 300 = 5分鐘）

    Returns:
        list[list[dict]]: 每個 part 是一組 [{'start': float, 'end': float}, ...]
    """
    if progress_callback:
        progress_callback(0.0, "取得影片長度...")

    total_duration = get_video_duration(video_path)

    if progress_callback:
        progress_callback(0.02, "偵測靜音中...")

    def silence_progress(pct, text):
        # 靜音偵測佔 0.02 ~ 0.30 的進度
        if progress_callback:
            progress_callback(0.02 + pct * 0.28, text)

    silence_regions = detect_silence(
        video_path, noise_db=speech_threshold_db, min_duration=min_duration,
        total_duration=total_duration, progress_callback=silence_progress,
    )

    if progress_callback:
        progress_callback(0.3, f"偵測到 {len(silence_regions)} 段靜音，分析中...")

    # 補全最後一段靜音的 end（如果缺失）
    for region in silence_regions:
        if region["end"] is None:
            region["end"] = total_duration
            region["duration"] = total_duration - region["start"]

    # 分類靜音區段
    leading_trailing = []  # 開頭/結尾空白（只移除，不切割）
    long_breaks = []       # 長休息（切割點）

    for region in silence_regions:
        is_leading = region["start"] < 1.0
        is_trailing = abs(region["end"] - total_duration) < 1.0
        is_long_break = region["duration"] >= break_threshold

        if is_leading:
            logger.info(f"  開頭空白: {region['start']:.1f}s ~ {region['end']:.1f}s ({region['duration']:.1f}s)")
            leading_trailing.append(region)
        elif is_trailing:
            logger.info(f"  結尾空白: {region['start']:.1f}s ~ {region['end']:.1f}s ({region['duration']:.1f}s)")
            leading_trailing.append(region)
        elif is_long_break:
            logger.info(f"  中間休息（切割點）: {region['start']:.1f}s ~ {region['end']:.1f}s ({region['duration']:.1f}s)")
            long_breaks.append(region)

    # 建立影片的有效範圍（移除開頭/結尾空白後）
    content_start = 0.0
    content_end = total_duration

    for region in leading_trailing:
        if region["start"] < 1.0:  # 開頭空白
            content_start = max(content_start, region["end"])
        if abs(region["end"] - total_duration) < 1.0:  # 結尾空白
            content_end = min(content_end, region["start"])

    if content_start >= content_end:
        logger.warning("移除開頭/結尾空白後沒有內容！")
        return []

    # 按長休息切割成 parts
    long_breaks.sort(key=lambda r: r["start"])

    parts = []
    cursor = content_start

    for brk in long_breaks:
        # 這段休息在有效範圍外就跳過
        if brk["end"] <= content_start or brk["start"] >= content_end:
            continue

        part_end = brk["start"]
        if part_end > cursor:
            parts.append({"start": cursor, "end": part_end})
        cursor = brk["end"]

    # 最後一段
    if cursor < content_end:
        parts.append({"start": cursor, "end": content_end})

    # 過濾太短的 part（< 1 秒）
    parts = [p for p in parts if (p["end"] - p["start"]) >= 1.0]

    if not parts:
        logger.warning("沒有保留的片段！")
        return []

    # 精確修剪每個 part 邊界（移除開頭/結尾短暫環境音）
    logger.info("精確修剪 part 邊界...")
    total_boundary_steps = len(parts) * 2  # 每個 part: forward + backward
    boundary_step = 0

    for i, p in enumerate(parts):
        if progress_callback:
            pct = 0.3 + (boundary_step / total_boundary_steps) * 0.7
            progress_callback(pct, f"精確修剪 Part {i+1}/{len(parts)} 開頭邊界...")

        # 修剪開頭
        refined_start = find_speech_boundary(
            video_path, p["start"], direction="forward",
        )
        if refined_start > p["start"]:
            trimmed = refined_start - p["start"]
            logger.info(f"  Part {i+1}: 修剪開頭 {trimmed:.1f}s 靜音")
            p["start"] = refined_start
        boundary_step += 1

        if progress_callback:
            pct = 0.3 + (boundary_step / total_boundary_steps) * 0.7
            progress_callback(pct, f"精確修剪 Part {i+1}/{len(parts)} 結尾邊界...")

        # 修剪或延伸結尾
        refined_end = find_speech_boundary(
            video_path, p["end"], direction="backward",
        )
        if refined_end != p["end"]:
            diff = refined_end - p["end"]
            if diff < 0:
                logger.info(f"  Part {i+1}: 修剪結尾 {-diff:.1f}s 環境音")
            else:
                logger.info(f"  Part {i+1}: 延伸結尾 {diff:.1f}s（語音延伸至休息區）")
            p["end"] = refined_end
        boundary_step += 1

    # 再次過濾
    parts = [p for p in parts if (p["end"] - p["start"]) >= 1.0]

    if not parts:
        logger.warning("精確修剪後沒有保留的片段！")
        return []

    # 結尾緩衝：每個 part 結尾多保留 1 秒（自然收尾，避免片尾 cross dissolve 覆蓋語音）
    _END_PADDING = 1.0
    for i, p in enumerate(parts):
        max_end = parts[i + 1]["start"] if i < len(parts) - 1 else total_duration
        new_end = min(p["end"] + _END_PADDING, max_end)
        if new_end > p["end"]:
            logger.info(f"  Part {i+1}: 結尾延伸 {new_end - p['end']:.1f}s（收尾緩衝）")
            p["end"] = new_end

    # 將每個 part 包裝成 list[list[dict]] 格式（每個 part 含一個 segment）
    result = [[p] for p in parts]

    total_kept = sum(p["end"] - p["start"] for p in parts)
    total_removed = total_duration - total_kept
    logger.info(f"結果: {len(parts)} 段影片 | 保留 {total_kept:.1f}s / 移除 {total_removed:.1f}s / 原始 {total_duration:.1f}s")

    if progress_callback:
        progress_callback(1.0, f"分析完成: {len(parts)} 段影片")
    for i, p in enumerate(parts):
        duration = p["end"] - p["start"]
        logger.info(f"  Part {i+1}: {p['start']:.1f}s ~ {p['end']:.1f}s ({duration:.1f}s)")

    return result
