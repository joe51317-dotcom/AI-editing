"""
主應用程式視窗 — 兩欄佈局，整合所有面板，協調事件
"""
import os
import sys
import queue
import logging
import threading
import customtkinter as ctk
from PIL import Image

from gui.theme import COLORS, FONT_FAMILY, FONT_SIZES, PADDING, WINDOW_SIZE, WINDOW_MIN_SIZE
from gui.components.video_panel import VideoPanel
from gui.components.youtube_panel import YouTubePanel
from gui.components.settings_panel import SettingsPanel
from gui.components.progress_panel import ProgressPanel
from gui.components.log_viewer import LogViewer
from gui.settings_store import load_settings, save_settings

logger = logging.getLogger(__name__)


def _get_asset_path(filename):
    """取得 assets 路徑（相容 PyInstaller frozen 環境）"""
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS if hasattr(sys, "_MEIPASS") else os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
        base = os.path.join(base, "..")  # auto-process/
    return os.path.join(base, "assets", filename)


class AutoProcessApp(ctk.CTk):
    """課程影片處理工具 — 主視窗"""

    def __init__(self):
        super().__init__()

        # 視窗設定
        self.title("鼎愛 課程影片處理工具")
        self.geometry(WINDOW_SIZE)
        self.minsize(*WINDOW_MIN_SIZE)
        self.configure(fg_color=COLORS["bg_dark"])

        # 設定深色模式
        ctk.set_appearance_mode("dark")

        # 設定視窗圖示
        self._set_icon()

        # 處理佇列（Worker → GUI）
        self.callback_queue = queue.Queue()
        self.processing = False
        self.current_worker = None

        self._build_ui()
        self._setup_logging()
        self._setup_dnd()
        self._load_settings()
        self._poll_queue()
        self._check_ffmpeg_on_startup()

        # 攔截視窗關閉，儲存設定
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _set_icon(self):
        """設定視窗圖示"""
        try:
            ico_path = _get_asset_path("app.ico")
            if os.path.exists(ico_path):
                self.iconbitmap(ico_path)
        except Exception:
            pass

    def _build_ui(self):
        """建立兩欄佈局"""
        # ── 品牌標題列 ──────────────────────────────────
        title_bar = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_card"],
            corner_radius=0,
            height=52,
        )
        title_bar.pack(fill="x", side="top")
        title_bar.pack_propagate(False)

        # 左側：Logo + 公司名 + 工具名
        brand_frame = ctk.CTkFrame(title_bar, fg_color="transparent")
        brand_frame.pack(side="left", padx=PADDING["section"], pady=8)

        # Logo 圖片
        try:
            logo_path = _get_asset_path("logo.png")
            if os.path.exists(logo_path):
                logo_img = Image.open(logo_path)
                ctk_logo = ctk.CTkImage(logo_img, size=(32, 32))
                ctk.CTkLabel(
                    brand_frame,
                    image=ctk_logo,
                    text="",
                ).pack(side="left", padx=(0, 8))
        except Exception:
            pass

        # 公司名
        ctk.CTkLabel(
            brand_frame,
            text="鼎愛",
            font=(FONT_FAMILY, FONT_SIZES["title"], "bold"),
            text_color=COLORS["accent"],
        ).pack(side="left")

        # 分隔線
        ctk.CTkLabel(
            brand_frame,
            text=" · ",
            font=(FONT_FAMILY, FONT_SIZES["heading"]),
            text_color=COLORS["text_dim"],
        ).pack(side="left")

        # 工具名
        ctk.CTkLabel(
            brand_frame,
            text="課程影片處理工具",
            font=(FONT_FAMILY, FONT_SIZES["heading"]),
            text_color=COLORS["text_secondary"],
        ).pack(side="left")

        # 右側：版本
        try:
            from config import APP_VERSION
            version_text = f"v{APP_VERSION}"
        except ImportError:
            version_text = "v1.0"
        ctk.CTkLabel(
            title_bar,
            text=version_text,
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            text_color=COLORS["text_dim"],
        ).pack(side="right", padx=PADDING["section"])

        # 標題列底部細線
        ctk.CTkFrame(
            self,
            fg_color=COLORS["border"],
            height=1,
            corner_radius=0,
        ).pack(fill="x")

        # ── 主體：兩欄 Grid ──────────────────────────────
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=PADDING["inner"], pady=PADDING["inner"])
        main_frame.grid_columnconfigure(0, weight=1, minsize=220)   # 左欄 ~25%
        main_frame.grid_columnconfigure(1, weight=3, minsize=600)  # 右欄 ~75%
        main_frame.grid_rowconfigure(0, weight=1)

        # ── 左欄：影片選擇 ───────────────────────────────
        left_col = ctk.CTkFrame(main_frame, fg_color="transparent")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        left_col.grid_rowconfigure(0, weight=1)
        left_col.grid_columnconfigure(0, weight=1)

        self.video_panel = VideoPanel(left_col)
        self.video_panel.grid(row=0, column=0, sticky="nsew")

        # ── 右欄：設定 + 狀態 ────────────────────────────
        right_col = ctk.CTkFrame(main_frame, fg_color="transparent")
        right_col.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right_col.grid_columnconfigure(0, weight=1)

        # YouTube 面板
        self.youtube_panel = YouTubePanel(right_col)
        self.youtube_panel.grid(row=0, column=0, sticky="ew", pady=(0, 5))

        # 設定面板
        self.settings_panel = SettingsPanel(right_col)
        self.settings_panel.grid(row=1, column=0, sticky="ew", pady=(0, 5))

        # 操作按鈕列
        btn_frame = ctk.CTkFrame(right_col, fg_color="transparent")
        btn_frame.grid(row=2, column=0, sticky="ew", pady=(0, 5))
        btn_frame.grid_columnconfigure(0, weight=1)

        self.start_btn = ctk.CTkButton(
            btn_frame,
            text="▶  開始處理",
            font=(FONT_FAMILY, FONT_SIZES["heading"], "bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            height=40,
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
            height=40,
            corner_radius=8,
            width=90,
            command=self._stop_processing,
            state="disabled",
        )
        self.stop_btn.pack(side="right")

        # 進度面板
        self.progress_panel = ProgressPanel(right_col)
        self.progress_panel.grid(row=3, column=0, sticky="ew", pady=(0, 5))

        # 日誌面板（預設收合）
        self.log_viewer = LogViewer(right_col)
        self.log_viewer.grid(row=4, column=0, sticky="ew")

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
                self.settings_panel.show_manual_errors(errors)
                for err in errors:
                    logger.error(f"時間格式錯誤: {err}")
                return
            else:
                self.settings_panel.show_manual_errors([])
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
            output_dir=self.youtube_panel.get_output_dir(),
            upload_enabled=self.youtube_panel.is_upload_enabled(),
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

        # 完成通知
        count = len(self.progress_panel.items)
        output_dir = self.youtube_panel.get_output_dir()
        self.progress_panel.show_done(count, output_dir)
        logger.info(f"所有處理完成（{count} 個影片）")

    # ── FFmpeg 自動偵測 ────────────────────────────────

    def _check_ffmpeg_on_startup(self):
        """啟動時背景檢查 FFmpeg，缺少則自動下載"""
        from ffmpeg_manager import check_ffmpeg
        if check_ffmpeg():
            logger.info("FFmpeg 已就緒")
            return

        logger.info("FFmpeg 未偵測到，開始自動下載...")
        self.progress_panel.add_video("__ffmpeg__").set_status("正在下載 FFmpeg...")

        def _download():
            from ffmpeg_manager import download_ffmpeg
            success = download_ffmpeg(
                progress_callback=lambda dl, total: self.callback_queue.put({
                    "type": "progress",
                    "filename": "__ffmpeg__",
                    "value": dl / total if total > 0 else 0,
                    "text": f"下載 FFmpeg... {dl // (1024*1024)}MB / {total // (1024*1024)}MB",
                })
            )
            if success:
                self.callback_queue.put({"type": "done", "filename": "__ffmpeg__"})
                logger.info("FFmpeg 下載完成")
            else:
                self.callback_queue.put({
                    "type": "error", "filename": "__ffmpeg__",
                    "text": "FFmpeg 下載失敗，請手動安裝",
                })

        threading.Thread(target=_download, daemon=True).start()

    # ── 設定持久化 ────────────────────────────────────

    def _load_settings(self):
        """啟動時載入設定"""
        settings = load_settings()
        self.youtube_panel.set_state(settings)
        self.settings_panel.set_state(settings)
        self.video_panel.naming_panel.set_state(settings)

        # 恢復視窗大小位置
        geo = settings.get("window_geometry")
        if geo:
            try:
                self.geometry(geo)
            except Exception:
                pass

    def _save_settings(self):
        """收集各面板狀態並儲存"""
        settings = {}
        settings.update(self.youtube_panel.get_state())
        settings.update(self.settings_panel.get_state())
        settings.update(self.video_panel.naming_panel.get_state())
        settings["window_geometry"] = self.geometry()
        save_settings(settings)

    def _on_close(self):
        """視窗關閉時儲存設定"""
        try:
            self._save_settings()
        except Exception as e:
            logger.warning(f"儲存設定失敗: {e}")
        self.destroy()
