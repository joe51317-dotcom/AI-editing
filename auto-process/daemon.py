"""
資料夾監聽 Daemon — 偵測 inbox 中的新影片，自動裁剪 + 上傳 YouTube
啟動方式: python daemon.py
"""
import sys
import io
import os
import time
import shutil
import queue
import logging
import threading
from datetime import datetime

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

if sys.stdout is not None and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from config import (
    INBOX_DIR, PROCESSING_DIR, DONE_DIR, FAILED_DIR, LOG_DIR, VIDEO_EXTENSIONS
)
from course_trimmer import trim_course_video
from youtube_uploader import upload_video

# 設定日誌：同時輸出到 console 和檔案
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, f"daemon_{datetime.now():%Y-%m-%d}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# 處理佇列
processing_queue = queue.Queue()


def is_video_file(path):
    """檢查檔案是否為支援的影片格式"""
    ext = os.path.splitext(path)[1].lower()
    return ext in VIDEO_EXTENSIONS


def wait_for_file_ready(path, check_interval=3, stable_count=3):
    """
    等待檔案複製完成（file size 穩定檢查）。

    Args:
        path: 檔案路徑
        check_interval: 每次檢查間隔秒數
        stable_count: 需要連續穩定的次數

    Returns:
        bool: 檔案是否就緒
    """
    logger.info(f"等待檔案複製完成: {os.path.basename(path)}")
    last_size = -1
    stable = 0

    while stable < stable_count:
        if not os.path.exists(path):
            logger.warning(f"檔案消失了: {path}")
            return False

        try:
            current_size = os.path.getsize(path)
        except OSError:
            time.sleep(check_interval)
            continue

        if current_size == last_size and current_size > 0:
            stable += 1
        else:
            stable = 0

        last_size = current_size
        time.sleep(check_interval)

    logger.info(f"檔案就緒 ({last_size / (1024*1024):.1f} MB)")
    return True


class VideoDropHandler(FileSystemEventHandler):
    """監聽 inbox 資料夾中的新影片檔案"""

    def on_created(self, event):
        if event.is_directory:
            return
        if is_video_file(event.src_path):
            logger.info(f"偵測到新影片: {os.path.basename(event.src_path)}")
            processing_queue.put(event.src_path)


class ProcessingWorker(threading.Thread):
    """背景 worker，從佇列取出影片進行處理"""

    def __init__(self):
        super().__init__(daemon=True)

    def run(self):
        while True:
            video_path = processing_queue.get()
            try:
                self._process(video_path)
            except Exception as e:
                logger.error(f"處理失敗 ({os.path.basename(video_path)}): {e}", exc_info=True)
                self._move_to(video_path, FAILED_DIR)
            finally:
                processing_queue.task_done()

    def _process(self, video_path):
        filename = os.path.basename(video_path)

        # 等待檔案複製完成
        if not wait_for_file_ready(video_path):
            return

        # 移到 processing/
        processing_path = self._move_to(video_path, PROCESSING_DIR)
        if not processing_path:
            return

        logger.info(f"=== 開始處理: {filename} ===")

        # Step 1: 裁剪（回傳多個檔案路徑）
        trimmed_paths = trim_course_video(processing_path)

        if not trimmed_paths:
            # 沒有裁剪（無靜音），直接上傳原始檔
            trimmed_paths = [processing_path]

        # Step 2: 逐一上傳到 YouTube
        all_success = True
        video_ids = []

        for path in trimmed_paths:
            video_id = upload_video(path)
            if video_id:
                video_ids.append(video_id)
                logger.info(f"  上傳成功: {os.path.basename(path)} → https://youtu.be/{video_id}")
            else:
                all_success = False
                logger.error(f"  上傳失敗: {os.path.basename(path)}")

        # Step 3: 移動檔案
        dest_dir = DONE_DIR if all_success else FAILED_DIR

        if all_success:
            logger.info(f"=== 處理完成: {filename} → {len(video_ids)} 支影片已上傳 ===")
        else:
            logger.error(f"=== 部分上傳失敗: {filename} ({len(video_ids)}/{len(trimmed_paths)} 成功) ===")

        self._move_to(processing_path, dest_dir)
        for path in trimmed_paths:
            if path != processing_path and os.path.exists(path):
                self._move_to(path, dest_dir)

    def _move_to(self, src, dest_dir):
        """移動檔案到指定目錄，回傳新路徑"""
        os.makedirs(dest_dir, exist_ok=True)
        filename = os.path.basename(src)
        dest = os.path.join(dest_dir, filename)

        # 避免同名衝突
        if os.path.exists(dest):
            base, ext = os.path.splitext(filename)
            dest = os.path.join(dest_dir, f"{base}_{int(time.time())}{ext}")

        try:
            shutil.move(src, dest)
            logger.info(f"  移動: {filename} → {os.path.basename(dest_dir)}/")
            return dest
        except Exception as e:
            logger.error(f"  移動失敗: {e}")
            return None


def main():
    # 確保所有工作目錄存在
    for d in [INBOX_DIR, PROCESSING_DIR, DONE_DIR, FAILED_DIR, LOG_DIR]:
        os.makedirs(d, exist_ok=True)

    logger.info("=" * 50)
    logger.info("Auto-Process Daemon 啟動")
    logger.info(f"  監聽資料夾: {INBOX_DIR}")
    logger.info(f"  日誌檔案: {log_file}")
    logger.info("=" * 50)

    # 啟動處理 worker
    worker = ProcessingWorker()
    worker.start()

    # 檢查 inbox 中是否有未處理的影片（daemon 重啟時）
    for filename in os.listdir(INBOX_DIR):
        filepath = os.path.join(INBOX_DIR, filename)
        if os.path.isfile(filepath) and is_video_file(filepath):
            logger.info(f"發現未處理影片: {filename}")
            processing_queue.put(filepath)

    # 啟動資料夾監聽
    event_handler = VideoDropHandler()
    observer = Observer()
    observer.schedule(event_handler, INBOX_DIR, recursive=False)
    observer.start()

    logger.info("等待新影片...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到停止信號，正在關閉...")
        observer.stop()

    observer.join()
    logger.info("Daemon 已停止。")


if __name__ == "__main__":
    main()
