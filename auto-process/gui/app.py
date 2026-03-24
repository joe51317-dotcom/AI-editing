"""
主應用程式視窗 — 整合所有面板，協調事件
"""
import os
import queue
import logging
import threading
import customtkinter as ctk

from gui.theme import COLORS, FONT_FAMILY, FONT_SIZES, PADDING, WINDOW_SIZE, WINDOW_MIN_SIZE
from gui.components.video_panel import VideoPanel
from gui.components.youtube_panel import YouTubePanel
from gui.components.settings_panel import SettingsPanel
from gui.components.progress_panel import ProgressPanel
from gui.components.log_viewer import LogViewer

logger = logging.getLogger(__name__)


class AutoProcessApp(ctk.CTk):
    """課程影片處理工具 — 主視窗"""

    def __init__(self):
        super().__init__()

        # 視窗設定
        self.title("課程影片處理工具")
        self.geometry(WINDOW_SIZE)
        self.minsize(*WINDOW_MIN_SIZE)
        self.configure(fg_color=COLORS["bg_dark"])

        # 設定深色模式
        ctk.set_appearance_mode("dark")

        # 處理佇列（Worker → GUI）
        self.callback_queue = queue.Queue()
        self.processing = False
        self.current_worker = None

        self._build_ui()
        self._setup_logging()
        self._setup_dnd()
        self._poll_queue()

    def _build_ui(self):
        """建立 UI 佈局"""
        # 標題列
        title_bar = ctk.CTkFrame(self, fg_color="transparent", height=50)
        title_bar.pack(fill="x", padx=PADDING["section"], pady=(PADDING["inner"], 0))
        title_bar.pack_propagate(False)

        ctk.CTkLabel(
            title_bar,
            text="課程影片處理工具",
            font=(FONT_FAMILY, FONT_SIZES["title"], "bold"),
            text_color=COLORS["accent"],
        ).pack(side="left", pady=8)

        # 可捲動主區域
        self.scroll_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
        )
        self.scroll_frame.pack(fill="both", expand=True, padx=PADDING["inner"], pady=PADDING["inner"])

        # 影片面板
        self.video_panel = VideoPanel(self.scroll_frame)
        self.video_panel.pack(fill="x", pady=(0, PADDING["inner"]))

        # YouTube 面板
        self.youtube_panel = YouTubePanel(self.scroll_frame)
        self.youtube_panel.pack(fill="x", pady=(0, PADDING["inner"]))

        # 設定面板
        self.settings_panel = SettingsPanel(self.scroll_frame)
        self.settings_panel.pack(fill="x", pady=(0, PADDING["inner"]))

        # 操作按鈕
        btn_frame = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(0, PADDING["inner"]))

        self.start_btn = ctk.CTkButton(
            btn_frame,
            text="▶  開始處理",
            font=(FONT_FAMILY, FONT_SIZES["heading"], "bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            height=42,
            corner_radius=8,
            command=self._start_processing,
        )
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        self.stop_btn = ctk.CTkButton(
            btn_frame,
            text="⏹  停止",
            font=(FONT_FAMILY, FONT_SIZES["body"]),
            fg_color=COLORS["bg_hover"],
            hover_color=COLORS["error"],
            text_color=COLORS["text_primary"],
            height=42,
            corner_radius=8,
            width=100,
            command=self._stop_processing,
            state="disabled",
        )
        self.stop_btn.pack(side="right")

        # 進度面板
        self.progress_panel = ProgressPanel(self.scroll_frame)
        self.progress_panel.pack(fill="x", pady=(0, PADDING["inner"]))

        # 日誌面板
        self.log_viewer = LogViewer(self.scroll_frame)
        self.log_viewer.pack(fill="x", pady=(0, PADDING["inner"]))

    def _setup_logging(self):
        """設定 logging 導向 GUI"""
        root_logger = logging.getLogger()
        root_logger.addHandler(self.log_viewer.get_handler())

    def _setup_dnd(self):
        """設定拖放功能"""
        self.video_panel.setup_dnd(self)

    def _poll_queue(self):
        """每 100ms 輪詢背景線程的訊息"""
        while not self.callback_queue.empty():
            try:
                msg = self.callback_queue.get_nowait()
                self._handle_message(msg)
            except queue.Empty:
                break
        self.after(100, self._poll_queue)

    def _handle_message(self, msg):
        """處理來自 Worker 的訊息"""
        msg_type = msg.get("type")
        filename = msg.get("filename", "")

        item = self.progress_panel.get_item(filename)
        if not item:
            return

        if msg_type == "status":
            item.set_status(msg.get("text", ""))

        elif msg_type == "progress":
            item.set_progress(msg.get("value", 0))
            status_text = msg.get("text", "")
            if status_text:
                item.set_status(status_text)

        elif msg_type == "done":
            item.set_done()

        elif msg_type == "error":
            item.set_error(msg.get("text", "失敗"))

        elif msg_type == "all_done":
            self._on_all_done()

    def _start_processing(self):
        """開始處理所有影片"""
        videos = self.video_panel.get_videos()
        if not videos:
            logger.warning("請先加入影片")
            return

        # 檢查 FFmpeg
        from ffmpeg_manager import check_ffmpeg
        if not check_ffmpeg():
            logger.error("找不到 FFmpeg！正在嘗試下載...")
            self._download_ffmpeg_then_start(videos)
            return

        self._begin_processing(videos)

    def _download_ffmpeg_then_start(self, videos):
        """下載 FFmpeg 後開始處理"""
        def _download():
            from ffmpeg_manager import download_ffmpeg
            success = download_ffmpeg(
                progress_callback=lambda dl, total: self.callback_queue.put({
                    "type": "status",
                    "filename": "__ffmpeg__",
                    "text": f"下載 FFmpeg... {dl // (1024*1024)}MB / {total // (1024*1024)}MB",
                })
            )
            if success:
                self.after(0, lambda: self._begin_processing(videos))
            else:
                self.after(0, lambda: logger.error("FFmpeg 下載失敗，請手動安裝"))
                self.after(0, self._on_all_done)

        self.progress_panel.clear()
        self.progress_panel.add_video("__ffmpeg__").set_status("下載 FFmpeg...")
        threading.Thread(target=_download, daemon=True).start()

    def _begin_processing(self, videos):
        """實際開始處理"""
        trim_mode = self.settings_panel.get_trim_mode()

        # 手動模式：驗證時間片段
        manual_segments = None
        if trim_mode == "manual":
            segments, errors = self.settings_panel.get_manual_segments()
            if errors:
                for err in errors:
                    logger.error(f"時間格式錯誤: {err}")
                return
            if not segments:
                logger.warning("請輸入至少一個時間片段")
                return
            manual_segments = segments

        self.processing = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress_panel.clear()

        # 為每個影片建立進度項目
        for v in videos:
            filename = os.path.basename(v["path"])
            self.progress_panel.add_video(filename)

        # 啟動 Worker
        from gui.workers.process_worker import ProcessWorker
        self.current_worker = ProcessWorker(
            videos=videos,
            callback_queue=self.callback_queue,
            trim_mode=trim_mode,
            speech_threshold=self.settings_panel.get_speech_threshold(),
            break_threshold=self.settings_panel.get_break_threshold(),
            manual_segments=manual_segments,
            youtube_service=self.youtube_panel.get_youtube_service(),
            privacy_status=self.youtube_panel.get_privacy_status(),
            playlist_id=self.youtube_panel.get_selected_playlist_id(),
            thumbnail_path=self.youtube_panel.get_thumbnail_path(),
            naming_rule=self.video_panel.get_naming_rule(),
        )
        self.current_worker.start()

    def _stop_processing(self):
        """停止處理"""
        if self.current_worker:
            self.current_worker.stop()
            logger.info("正在停止...")

    def _on_all_done(self):
        """所有處理完成"""
        self.processing = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.current_worker = None
        logger.info("所有處理完成")
