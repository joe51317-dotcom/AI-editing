"""
處理 Worker — 背景線程執行裁剪 + 儲存 + 上傳流程
"""
import os
import re
import shutil
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# 進度區間常數
TRIM_PROGRESS_END = 0.4      # 裁剪佔 0% ~ 40%
UPLOAD_PROGRESS_START = 0.4   # 上傳佔 40% ~ 100%
UPLOAD_PROGRESS_RANGE = 0.6   # 上傳進度區間寬度


class ProcessWorker(threading.Thread):
    """
    背景處理線程：逐一處理影片（裁剪 → 儲存到輸出目錄 → 上傳 → 播放清單 → 縮圖）。
    透過 callback_queue 向 GUI 回報進度。
    """

    def __init__(self, videos, callback_queue, trim_mode="auto",
                 trim_enabled=True,
                 speech_threshold=-20, break_threshold=300,
                 manual_segments=None,
                 output_dir=None, upload_enabled=True,
                 youtube_service=None, privacy_status="unlisted",
                 playlist_id=None, thumbnail_path=None,
                 naming_rule="{filename}"):
        super().__init__(daemon=True)
        self.videos = videos  # [{'path': str, 'title': str}, ...]
        self.queue = callback_queue
        self.trim_mode = trim_mode
        self.trim_enabled = trim_mode != "skip"
        self.manual_segments = manual_segments  # list[list[dict]] or None
        self.speech_threshold = speech_threshold
        self.break_threshold = break_threshold
        self.output_dir = output_dir
        self.upload_enabled = upload_enabled
        self.youtube_service = youtube_service
        self.privacy_status = privacy_status
        self.playlist_id = playlist_id
        self.thumbnail_path = thumbnail_path
        self.naming_rule = naming_rule
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def _stopped(self):
        return self._stop_event.is_set()

    def _send(self, filename, msg_type, **kwargs):
        self.queue.put({"type": msg_type, "filename": filename, **kwargs})

    def _apply_naming_rule(self, title, index, part=1):
        """套用命名規則模板"""
        result = self.naming_rule
        result = result.replace("{filename}", title)
        result = result.replace("{date}", datetime.now().strftime("%Y-%m-%d"))
        result = result.replace("{index}", str(index))
        result = result.replace("{part}", str(part))
        return result

    def _manual_trim(self, video_path, filename):
        """手動裁剪：根據使用者指定的時間片段直接呼叫 render_video"""
        from video_renderer import render_video

        base, ext = os.path.splitext(video_path)
        output_paths = []
        total_parts = len(self.manual_segments)

        self._send(filename, "status", text="手動裁剪中...")
        self._send(filename, "progress", value=0.0)

        for i, segments in enumerate(self.manual_segments):
            if self._stopped():
                return output_paths

            part_num = i + 1
            output_path = f"{base}-{part_num}{ext}"

            self._send(filename, "progress",
                       value=(i / total_parts) * TRIM_PROGRESS_END,
                       text=f"裁剪 Part {part_num}/{total_parts}...")

            def make_cb(part_idx):
                def cb(step, current, total):
                    part_base = (part_idx / total_parts) * TRIM_PROGRESS_END
                    part_range = TRIM_PROGRESS_END / total_parts
                    sub_pct = (current / total * 0.8) if step == "cutting" else 0.9
                    self._send(filename, "progress",
                               value=part_base + sub_pct * part_range)
                return cb

            def make_err_cb(fn):
                def cb(msg):
                    self._send(fn, "status", text=msg)
                    logger.error(msg)
                return cb

            success = render_video(video_path, segments, output_path,
                                   progress_callback=make_cb(i),
                                   error_callback=make_err_cb(filename))

            if success:
                output_paths.append(output_path)
                logger.info(f"  手動裁剪 Part {part_num}: {output_path}")
            else:
                logger.error(f"  手動裁剪 Part {part_num} 失敗")

        if output_paths:
            self._send(filename, "progress", value=TRIM_PROGRESS_END,
                       text=f"手動裁剪完成，{len(output_paths)} 個檔案")
        else:
            self._send(filename, "progress", value=TRIM_PROGRESS_END, text="手動裁剪失敗")

        return output_paths or [video_path]

    def run(self):
        for idx, video in enumerate(self.videos, start=1):
            if self._stopped():
                break

            video_path = video["path"]
            video_title = video["title"]
            filename = os.path.basename(video_path)

            try:
                self._process_one(video_path, video_title, filename, idx)
            except Exception as e:
                logger.error(f"處理 {filename} 時發生錯誤: {e}")
                self._send(filename, "error", text=str(e))

        # 全部完成
        self.queue.put({"type": "all_done"})

    def _process_one(self, video_path, video_title, filename, index):
        """處理單一影片"""
        # === Step 1: 裁剪 ===
        trimmed_files = []

        if self.trim_mode == "manual" and self.manual_segments:
            # --- 手動裁剪模式 ---
            trimmed_files = self._manual_trim(video_path, filename)
        elif self.trim_mode == "auto":
            # --- 自動裁剪模式 ---
            self._send(filename, "status", text="偵測靜音中...")
            self._send(filename, "progress", value=0.0)

            def trim_progress(pct, detail):
                overall = pct * TRIM_PROGRESS_END
                self._send(filename, "progress", value=overall, text=detail)

            from course_trimmer import trim_course_video
            trimmed = trim_course_video(
                video_path,
                speech_threshold_db=self.speech_threshold,
                break_threshold=self.break_threshold,
                progress_callback=trim_progress,
            )

            if trimmed:
                trimmed_files = trimmed
                self._send(filename, "progress", value=TRIM_PROGRESS_END, text=f"裁剪完成，{len(trimmed)} 個檔案")
            else:
                trimmed_files = [video_path]
                self._send(filename, "progress", value=TRIM_PROGRESS_END, text="不需裁剪，使用原始檔")
        else:
            # --- 跳過裁剪 ---
            trimmed_files = [video_path]
            self._send(filename, "progress", value=TRIM_PROGRESS_END, text="跳過裁剪")

        if self._stopped():
            return

        # === Step 2: 複製到輸出目錄 ===
        if self.output_dir:
            self._send(filename, "status", text="儲存到輸出目錄...")
            saved_files = []
            for fp in trimmed_files:
                dest = os.path.join(self.output_dir, os.path.basename(fp))
                # 避免來源與目的相同
                if os.path.abspath(fp) != os.path.abspath(dest):
                    shutil.copy2(fp, dest)
                    logger.info(f"已儲存: {dest}")
                    # 刪除原始位置的暫存裁剪檔（不刪原始影片）
                    if os.path.abspath(fp) != os.path.abspath(video_path):
                        try:
                            os.remove(fp)
                        except OSError:
                            pass
                saved_files.append(dest)
            trimmed_files = saved_files

        if self._stopped():
            return

        # === Step 3: 上傳到 YouTube（可選） ===
        if not self.upload_enabled or not self.youtube_service:
            self._send(filename, "progress", value=1.0)
            self._send(filename, "done")
            if not self.upload_enabled:
                logger.info(f"{filename}: 處理完成（僅本地儲存）")
            else:
                logger.info(f"{filename}: 裁剪完成（未登入 YouTube，跳過上傳）")
            return

        total_files = len(trimmed_files)
        for part_idx, file_path in enumerate(trimmed_files, start=1):
            if self._stopped():
                return

            # 計算標題
            part_title = self._apply_naming_rule(video_title, index, part_idx)
            if total_files > 1:
                # 多段影片加上 -N 後綴
                part_title = f"{part_title}-{part_idx}"

            # 計算上傳進度映射
            upload_base = UPLOAD_PROGRESS_START + (UPLOAD_PROGRESS_RANGE * (part_idx - 1) / total_files)
            upload_range = UPLOAD_PROGRESS_RANGE / total_files

            self._send(filename, "status", text=f"上傳中 ({part_idx}/{total_files})...")

            def upload_progress(pct):
                overall = upload_base + (pct / 100) * upload_range
                self._send(filename, "progress", value=overall)

            from youtube_uploader import upload_video
            video_id = upload_video(
                file_path,
                title=part_title,
                privacy_status=self.privacy_status,
                progress_callback=upload_progress,
            )

            if not video_id:
                self._send(filename, "error", text=f"上傳失敗 (Part {part_idx})")
                return

            logger.info(f"已上傳: {part_title} → https://youtu.be/{video_id}")

            # === Step 4: 設定縮圖 ===
            if self.thumbnail_path:
                try:
                    from youtube_api import set_thumbnail
                    set_thumbnail(self.youtube_service, video_id, self.thumbnail_path)
                except Exception as e:
                    logger.warning(f"設定縮圖失敗: {e}")

            # === Step 5: 加入播放清單 ===
            if self.playlist_id:
                try:
                    from youtube_api import add_to_playlist
                    add_to_playlist(self.youtube_service, video_id, self.playlist_id)
                except Exception as e:
                    logger.warning(f"加入播放清單失敗: {e}")

        self._send(filename, "done")
