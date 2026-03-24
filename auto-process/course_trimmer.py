"""
課程影片裁剪器 — 移除開頭/結尾空白和中間長休息
可獨立執行：python course_trimmer.py "影片.mp4"
"""
import sys
import io
import os
import argparse
import logging

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from silence_detector import split_into_parts
from video_renderer import render_video

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def trim_course_video(video_path, speech_threshold_db=None, min_duration=None,
                      break_threshold=None, progress_callback=None):
    """
    裁剪課程影片：移除開頭/結尾空白，遇到長休息則切成多段影片。

    輸出命名：原檔名-1.mp4, 原檔名-2.mp4, ...

    Args:
        video_path: 來源影片路徑
        speech_threshold_db: 講課語音門檻 dB（None 則用 .env 設定）
        min_duration: 最短靜音秒數（None 則用 .env 設定）
        break_threshold: 休息判定秒數（None 則用 .env 設定）
        progress_callback: 可選回呼 callback(stage, detail) 用於 GUI 進度

    Returns:
        list[str]: 輸出檔案路徑清單，失敗則空清單
    """
    from config import SPEECH_THRESHOLD_DB, SILENCE_MIN_DURATION, BREAK_THRESHOLD_SECONDS

    speech_threshold_db = speech_threshold_db if speech_threshold_db is not None else SPEECH_THRESHOLD_DB
    min_duration = min_duration if min_duration is not None else SILENCE_MIN_DURATION
    break_threshold = break_threshold if break_threshold is not None else BREAK_THRESHOLD_SECONDS

    if not os.path.exists(video_path):
        logger.error(f"檔案不存在: {video_path}")
        return []

    logger.info(f"開始處理: {os.path.basename(video_path)}")
    logger.info(f"參數: speech_threshold={speech_threshold_db}dB, min_silence={min_duration}s, break={break_threshold}s")

    # Step 1: 偵測靜音並切割成 parts（佔總進度 0.0 ~ 0.5）
    def detection_progress(pct, detail):
        if progress_callback:
            progress_callback(pct * 0.5, detail)

    parts = split_into_parts(
        video_path,
        speech_threshold_db=speech_threshold_db,
        min_duration=min_duration,
        break_threshold=break_threshold,
        progress_callback=detection_progress,
    )

    if not parts:
        logger.warning("沒有保留的片段！")
        return []

    # 檢查是否整段保留（只有 1 個 part 且等於完整影片）
    if len(parts) == 1 and len(parts[0]) == 1:
        from silence_detector import get_video_duration
        total = get_video_duration(video_path)
        seg = parts[0][0]
        if abs(seg["start"]) < 0.5 and abs(seg["end"] - total) < 0.5:
            logger.info("影片不需要裁剪，沒有偵測到需移除的靜音。")
            return []

    # Step 2: 對每個 part 分別裁剪
    base, ext = os.path.splitext(video_path)
    output_paths = []
    input_size = os.path.getsize(video_path) / (1024 * 1024)

    total_parts = len(parts)
    for i, segments in enumerate(parts):
        part_num = i + 1
        output_path = f"{base}-{part_num}{ext}"

        logger.info(f"--- Part {part_num}/{total_parts} ---")

        # 裁剪階段佔總進度 0.5 ~ 1.0，每個 part 均分
        def make_render_cb(part_idx):
            def render_progress(step, current, total):
                if progress_callback:
                    part_base = 0.5 + (part_idx / total_parts) * 0.5
                    part_range = 0.5 / total_parts
                    if step == "cutting":
                        sub_pct = current / total * 0.8
                    else:  # merging
                        sub_pct = 0.9
                    progress_callback(
                        part_base + sub_pct * part_range,
                        f"裁剪 Part {part_idx + 1}/{total_parts}",
                    )
            return render_progress

        if progress_callback:
            progress_callback(0.5 + (i / total_parts) * 0.5, f"裁剪 Part {part_num}/{total_parts}...")

        success = render_video(video_path, segments, output_path,
                               progress_callback=make_render_cb(i))

        if success:
            output_size = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(f"  輸出: {os.path.basename(output_path)} ({output_size:.1f}MB)")
            output_paths.append(output_path)
        else:
            logger.error(f"  Part {part_num} 裁剪失敗")

    if output_paths:
        logger.info(f"完成! {input_size:.1f}MB → {len(output_paths)} 個檔案")
        if progress_callback:
            progress_callback(1.0, f"裁剪完成: {len(output_paths)} 個檔案")

    return output_paths


def main():
    parser = argparse.ArgumentParser(description="課程影片自動裁剪 — 移除靜音與休息時間")
    parser.add_argument("video_path", help="影片檔案路徑")
    parser.add_argument("--speech-threshold-db", type=int, default=None, help="講課語音門檻 dB（預設: -20）")
    parser.add_argument("--min-duration", type=float, default=None, help="最短靜音秒數（預設: 10）")
    parser.add_argument("--break-threshold", type=float, default=None, help="休息判定秒數（預設: 300）")
    args = parser.parse_args()

    results = trim_course_video(
        args.video_path,
        speech_threshold_db=args.speech_threshold_db,
        min_duration=args.min_duration,
        break_threshold=args.break_threshold,
    )

    if results:
        print(f"\n輸出 {len(results)} 個檔案:")
        for path in results:
            print(f"  {path}")
    else:
        print("\n未產生輸出檔案。")
        sys.exit(1)


if __name__ == "__main__":
    main()
