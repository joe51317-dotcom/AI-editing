"""
靜音偵測模組 — 使用 FFmpeg silencedetect 濾波器
偵測影片中的靜音區段，分類為開頭空白、結尾空白、中間休息。
"""
import re
import subprocess
import logging

logger = logging.getLogger(__name__)


def get_video_duration(video_path):
    """用 FFprobe 取得影片總長度（秒）"""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFprobe 失敗: {result.stderr}")
    return float(result.stdout.strip())


def detect_silence(video_path, noise_db=-30, min_duration=10):
    """
    用 FFmpeg silencedetect 偵測所有靜音區段。

    Args:
        video_path: 影片路徑
        noise_db: 靜音判定分貝閾值（預設 -30dB）
        min_duration: 最短靜音持續秒數（預設 10 秒）

    Returns:
        list[dict]: 靜音區段清單 [{'start': float, 'end': float, 'duration': float}, ...]
    """
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-vn",
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null",
        "-",
    ]

    logger.info(f"偵測靜音 (noise={noise_db}dB, min_duration={min_duration}s)...")
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    stderr = result.stderr
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


def split_into_parts(video_path, speech_threshold_db=-20, min_duration=10, break_threshold=300):
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
    total_duration = get_video_duration(video_path)
    silence_regions = detect_silence(video_path, noise_db=speech_threshold_db, min_duration=min_duration)

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

    # 將每個 part 包裝成 list[list[dict]] 格式（每個 part 含一個 segment）
    result = [[p] for p in parts]

    total_kept = sum(p["end"] - p["start"] for p in parts)
    total_removed = total_duration - total_kept
    logger.info(f"結果: {len(parts)} 段影片 | 保留 {total_kept:.1f}s / 移除 {total_removed:.1f}s / 原始 {total_duration:.1f}s")
    for i, p in enumerate(parts):
        duration = p["end"] - p["start"]
        logger.info(f"  Part {i+1}: {p['start']:.1f}s ~ {p['end']:.1f}s ({duration:.1f}s)")

    return result
