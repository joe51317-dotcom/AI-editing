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

from gui.theme import COLORS, FONT_FAMILY, FONT_FAMILY_DISPLAY, FONT_SIZES, PADDING, WINDOW_SIZE, WINDOW_MIN_SIZE
from gui.components.video_panel import VideoPanel
from gui.components.youtube_panel import YouTubePanel
from gui.components.settings_panel import SettingsPanel
from gui.components.progress_panel import ProgressPanel
from gui.components.log_viewer import LogViewer
from gui.components.tab_bar import TabBar
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
        self._video_map = {}  # basename → {path, title, index}

        # Review 模式狀態
        self._review_pending = []      # list of video dicts 待偵測
        self._reviewed = {}            # abs_path → list[list[dict]]
        self._review_gen = 0           # generation counter，用於丟棄過期訊息
        self._review_stop = None       # threading.Event，控制偵測線程
        self._review_dialog = None     # 目前開啟的 SegmentReviewDialog
        self._worker_params = {}       # snapshot of worker params at begin time

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
            font=(FONT_FAMILY_DISPLAY, FONT_SIZES["display"], "bold"),
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

        # ── 右欄：分頁設定 + sticky 動作/進度 + 日誌 ──
        right_col = ctk.CTkFrame(main_frame, fg_color="transparent")
        right_col.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right_col.grid_columnconfigure(0, weight=1)
        right_col.grid_rowconfigure(1, weight=1, minsize=380)  # tab content 吃掉剩餘空間

        # ── Tab Bar ──
        self.tab_bar = TabBar(
            right_col,
            tabs=["輸出設定", "處理設定"],
            on_change=self._on_tab_change,
        )
        self.tab_bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        # ── 可捲動 Tab 內容區 ──
        self.tab_content = ctk.CTkScrollableFrame(
            right_col,
            fg_color="transparent",
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["accent_dim"],
        )
        self.tab_content.grid(row=1, column=0, sticky="nsew")
        self.tab_content.grid_columnconfigure(0, weight=1)

        # YouTube 面板（輸出設定）
        self.youtube_panel = YouTubePanel(self.tab_content)
        self.youtube_panel.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        # 設定面板（處理設定）— 預設隱藏
        self.settings_panel = SettingsPanel(self.tab_content)
        self.settings_panel.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.settings_panel.grid_remove()

        # ── Sticky 操作按鈕列 ──
        btn_frame = ctk.CTkFrame(right_col, fg_color="transparent")
        btn_frame.grid(row=2, column=0, sticky="ew", pady=(8, 6))
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

        # ── Sticky 進度面板 ──
        self.progress_panel = ProgressPanel(right_col, on_retry=self._retry_video)
        self.progress_panel.grid(row=3, column=0, sticky="ew", pady=(0, 4))

        # ── 日誌面板（貼底，預設收合）──
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

        # ── 沒有 filename 的特殊訊息，在 item 查找前處理 ──
        if msg_type == "all_done":
            if self.processing:
                self._on_all_done()
            return

        # ── Review 階段訊息（有 filename 但 progress panel 不一定有 item）──
        if msg_type == "detect_progress":
            gen = msg.get("gen", -1)
            if gen != self._review_gen:
                return
            filename = msg.get("filename", "")
            item = self.progress_panel.get_item(filename)
            if item:
                item.set_status(msg.get("text", "偵測中..."))
                value = msg.get("value")
                if value is not None:
                    item.set_progress(value * 0.4)  # 偵測佔前 40%
            return

        if msg_type == "detect_done":
            gen = msg.get("gen", -1)
            if gen != self._review_gen:
                return  # 過期訊息，丟棄
            self._on_detect_done(msg)
            return

        # ── 一般 Worker 訊息 ──
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
            try:
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
                    self.callback_queue.put({
                        "type": "error", "filename": "__ffmpeg__",
                        "text": "FFmpeg 下載失敗，請手動安裝",
                    })
            except Exception as e:
                logger.error(f"FFmpeg 下載異常: {e}")
                self.callback_queue.put({
                    "type": "error", "filename": "__ffmpeg__",
                    "text": f"FFmpeg 下載異常: {e}",
                })

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
        self.progress_panel.start_timer()

        # 為每個影片建立進度項目
        self._video_map.clear()
        for idx, v in enumerate(videos, start=1):
            filename = os.path.basename(v["path"])
            self.progress_panel.add_video(filename)
            self._video_map[filename] = {"path": v["path"], "title": v["title"], "index": idx}

        # ── Review 模式：偵測 → 逐一確認 → 渲染 ──
        if trim_mode == "review":
            self._start_review_phase(videos)
            return

        # ── 一般模式：直接啟動 Worker ──
        intro_outro = self._collect_intro_outro()
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
            description_template=self.youtube_panel.get_description(),
            naming_rule=self.video_panel.get_naming_rule(),
            intro_outro=intro_outro,
        )
        self.current_worker.start()

    # ── Review 階段 ───────────────────────────────────────────

    def _start_review_phase(self, videos):
        """初始化 review 階段狀態並開始逐一偵測"""
        # Snapshot 所有 Worker 參數（review 期間禁止 UI 改動影響輸出）
        self._worker_params = {
            "speech_threshold": self.settings_panel.get_speech_threshold(),
            "break_threshold": self.settings_panel.get_break_threshold(),
            "output_dir": self.youtube_panel.get_output_dir(),
            "upload_enabled": self.youtube_panel.is_upload_enabled(),
            "youtube_service": self.youtube_panel.get_youtube_service(),
            "privacy_status": self.youtube_panel.get_privacy_status(),
            "playlist_id": self.youtube_panel.get_selected_playlist_id(),
            "thumbnail_path": self.youtube_panel.get_thumbnail_path(),
            "description_template": self.youtube_panel.get_description(),
            "naming_rule": self.video_panel.get_naming_rule(),
            "intro_outro": self._collect_intro_outro(),
        }

        self._review_pending = list(videos)
        self._reviewed = {}
        self._review_gen += 1

        # 禁用影響輸出的 UI（video_panel / youtube_panel / naming）
        self._lock_ui_for_review(True)

        # 開始偵測第一部
        self._review_next()

    def _review_next(self):
        """取下一部影片開始背景偵測；清單空則啟動 ProcessWorker"""
        if not self._review_pending:
            self._start_worker_with_reviewed()
            return

        video = self._review_pending.pop(0)
        video_path = video["path"]
        filename = os.path.basename(video_path)
        gen = self._review_gen

        item = self.progress_panel.get_item(filename)
        if item:
            item.set_status("偵測靜音中...")

        # 建立偵測線程專屬的 stop event
        self._review_stop = threading.Event()

        def _detect():
            try:
                from silence_detector import register_stop_event, split_into_parts
                register_stop_event(self._review_stop)

                parts = None
                status = "error"

                try:
                    def progress_cb(pct, text):
                        if self._review_stop.is_set():
                            return
                        self.callback_queue.put({
                            "type": "detect_progress",
                            "filename": filename,
                            "value": pct,
                            "text": text,
                            "gen": gen,
                        })

                    raw = split_into_parts(
                        video_path,
                        speech_threshold_db=self._worker_params["speech_threshold"],
                        break_threshold=self._worker_params["break_threshold"],
                        progress_callback=progress_cb,
                    )
                except Exception as e:
                    logger.error(f"偵測失敗 {filename}: {e}")
                    raw = None

                if self._review_stop.is_set():
                    status = "stopped"
                elif raw is None:
                    status = "error"
                elif len(raw) == 0:
                    # split_into_parts 回傳 [] 代表整支保留（無長休息）或失敗
                    # 後者已在 except 分支處理，此處視為 no_trim
                    status = "no_trim"
                else:
                    status = "parts"
                    parts = raw

                register_stop_event(None)

                self.callback_queue.put({
                    "type": "detect_done",
                    "filename": filename,
                    "path": video_path,
                    "video": video,
                    "parts": parts,
                    "status": status,
                    "gen": gen,
                })
            except Exception as e:
                logger.error(f"偵測線程錯誤: {e}")
                self.callback_queue.put({
                    "type": "detect_done",
                    "filename": filename,
                    "path": video_path,
                    "video": video,
                    "parts": None,
                    "status": "error",
                    "gen": gen,
                })

        threading.Thread(target=_detect, daemon=True).start()

    def _on_detect_done(self, msg):
        """處理偵測完成訊息，決定是否開段落確認視窗"""
        filename = msg["filename"]
        video_path = msg["path"]
        video = msg["video"]
        status = msg["status"]
        parts = msg.get("parts")

        item = self.progress_panel.get_item(filename)

        if status == "stopped":
            logger.info(f"{filename}: 偵測已停止")
            if item:
                item.set_status("已停止")
            return

        if status == "error":
            logger.error(f"{filename}: 偵測失敗")
            if item:
                item.set_error("偵測失敗")
            # 繼續下一部（不中斷整批）
            self._review_next()
            return

        if status == "no_trim":
            # 整支保留，視為單一 part，無需彈窗直接加入
            logger.info(f"{filename}: 不需裁剪，以整支影片為單一 Part")
            if item:
                item.set_status("不需裁剪（已確認）")
            from silence_detector import get_video_duration
            try:
                dur = get_video_duration(video_path)
                auto_parts = [[{"start": 0.0, "end": dur}]]
            except Exception:
                auto_parts = [[{"start": 0.0, "end": 0.0}]]
            self._reviewed[os.path.abspath(video_path)] = auto_parts
            self._review_next()
            return

        # status == "parts" → 開確認視窗
        if item:
            item.set_status(f"等待段落確認（{len(parts)} 段）...")
        if item:
            item.set_progress(0.4)

        total_videos = len(self._reviewed) + len(self._review_pending) + 1
        reviewed_count = len(self._reviewed) + 1

        from gui.components.segment_review_dialog import SegmentReviewDialog

        def on_confirm(segments):
            self._review_dialog = None
            abs_path = os.path.abspath(video_path)
            self._reviewed[abs_path] = segments
            fn_item = self.progress_panel.get_item(filename)
            if fn_item:
                fn_item.set_status(f"已確認 {len(segments)} 段")
            self._review_next()

        def on_cancel():
            self._review_dialog = None
            logger.info("使用者取消段落確認，中止整批")
            self._abort_review()

        self._review_dialog = SegmentReviewDialog(
            master=self,
            video_path=video_path,
            parts=parts,
            video_index=reviewed_count,
            total_videos=total_videos,
            on_confirm=on_confirm,
            on_cancel=on_cancel,
        )

    def _start_worker_with_reviewed(self):
        """所有影片確認完畢，組裝 videos 清單並啟動 ProcessWorker"""
        self._lock_ui_for_review(False)

        # 組裝帶 segments 的 video 清單
        reviewed_videos = []
        for filename, info in self._video_map.items():
            abs_path = os.path.abspath(info["path"])
            segments = self._reviewed.get(abs_path)
            if not segments:
                logger.warning(f"{filename}: 沒有找到 reviewed segments，略過")
                continue
            reviewed_videos.append({
                "path": info["path"],
                "title": info["title"],
                "segments": segments,
            })

        if not reviewed_videos:
            logger.error("沒有可處理的影片（全部略過或偵測失敗）")
            self._on_all_done()
            return

        params = self._worker_params
        from gui.workers.process_worker import ProcessWorker
        self.current_worker = ProcessWorker(
            videos=reviewed_videos,
            callback_queue=self.callback_queue,
            trim_mode="review",
            output_dir=params["output_dir"],
            upload_enabled=params["upload_enabled"],
            youtube_service=params["youtube_service"],
            privacy_status=params["privacy_status"],
            playlist_id=params["playlist_id"],
            thumbnail_path=params["thumbnail_path"],
            description_template=params["description_template"],
            naming_rule=params["naming_rule"],
            intro_outro=params["intro_outro"],
        )
        self.current_worker.start()

    def _abort_review(self):
        """取消整批 review，重置所有狀態"""
        self._review_gen += 1  # 讓在途 detect_done 失效
        if self._review_stop:
            self._review_stop.set()
        self._review_pending.clear()
        self._reviewed.clear()
        self._review_dialog = None
        self._lock_ui_for_review(False)
        self.processing = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.progress_panel.stop_timer()
        logger.info("段落確認已取消")

    def _lock_ui_for_review(self, lock: bool):
        """review 期間鎖定影響輸出的 UI 元件"""
        state = "disabled" if lock else "normal"
        try:
            self.video_panel.configure(state=state)
        except Exception:
            pass
        try:
            self.youtube_panel.configure(state=state)
        except Exception:
            pass

    # ── 一般流程 ──────────────────────────────────────────────

    def _collect_intro_outro(self):
        """收集片頭/片尾設定"""
        if self.settings_panel.is_intro_outro_enabled():
            io = self.settings_panel.get_intro_outro_settings()
            if not io.get("intro_path") and not io.get("outro_path"):
                logger.warning("⚠ 片頭/片尾已啟用但未選擇圖片，將不會加入片頭/片尾")
            return io
        return None

    def _on_tab_change(self, idx: int):
        """切換分頁：互斥顯示 YouTube / Settings 面板"""
        if idx == 0:
            self.settings_panel.grid_remove()
            self.youtube_panel.grid()
        else:
            self.youtube_panel.grid_remove()
            self.settings_panel.grid()

    def _stop_processing(self):
        """停止處理（含 review 階段偵測線程）"""
        if self._review_stop:
            self._review_stop.set()
        self._review_gen += 1  # 讓在途 detect_done 失效
        if self._review_dialog:
            try:
                self._review_dialog.destroy()
            except Exception:
                pass
            self._review_dialog = None
        if self.current_worker:
            self.current_worker.stop()
            logger.info("正在停止...")
        if self._review_pending or self._reviewed:
            self._abort_review()

    def _on_all_done(self):
        """所有處理完成"""
        self.processing = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.current_worker = None
        self._lock_ui_for_review(False)  # 確保 UI 解鎖
        self.progress_panel.stop_timer()

        # 完成通知
        count = len(self.progress_panel.items)
        output_dir = self.youtube_panel.get_output_dir()
        self.progress_panel.show_done(count, output_dir)
        logger.info(f"所有處理完成（{count} 個影片）")

    def _retry_video(self, filename):
        """重試單一失敗影片"""
        info = self._video_map.get(filename)
        if not info:
            logger.warning(f"找不到影片資訊: {filename}")
            return

        trim_mode = self.settings_panel.get_trim_mode()
        abs_path = os.path.abspath(info["path"])

        intro_outro = self._collect_intro_outro()

        if trim_mode == "review":
            # review 模式：使用已存的 segments 直接重跑，不重新偵測
            segments = self._reviewed.get(abs_path)
            if not segments:
                logger.warning(f"找不到 {filename} 的已審核 segments，無法重試")
                return
            from gui.workers.process_worker import ProcessWorker
            worker = ProcessWorker(
                videos=[{"path": info["path"], "title": info["title"],
                         "segments": segments}],
                callback_queue=self.callback_queue,
                trim_mode="review",
                output_dir=self._worker_params.get("output_dir",
                    self.youtube_panel.get_output_dir()),
                upload_enabled=self._worker_params.get("upload_enabled",
                    self.youtube_panel.is_upload_enabled()),
                youtube_service=self._worker_params.get("youtube_service",
                    self.youtube_panel.get_youtube_service()),
                privacy_status=self._worker_params.get("privacy_status",
                    self.youtube_panel.get_privacy_status()),
                playlist_id=self._worker_params.get("playlist_id",
                    self.youtube_panel.get_selected_playlist_id()),
                thumbnail_path=self._worker_params.get("thumbnail_path",
                    self.youtube_panel.get_thumbnail_path()),
                description_template=self._worker_params.get("description_template",
                    self.youtube_panel.get_description()),
                naming_rule=self._worker_params.get("naming_rule",
                    self.video_panel.get_naming_rule()),
                intro_outro=intro_outro,
            )
            worker.start()
            logger.info(f"重試（review）: {filename}")
            return

        # 非 review 模式
        manual_segments = None
        if trim_mode == "manual":
            segments, errors = self.settings_panel.get_manual_segments()
            if not errors and segments:
                manual_segments = segments

        from gui.workers.process_worker import ProcessWorker
        worker = ProcessWorker(
            videos=[{"path": info["path"], "title": info["title"]}],
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
            description_template=self.youtube_panel.get_description(),
            naming_rule=self.video_panel.get_naming_rule(),
            intro_outro=intro_outro,
        )
        worker.start()
        logger.info(f"重試處理: {filename}")

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

        # 恢復 LosslessCut 路徑（若之前設定過）
        lc_path = settings.get("losslesscut_path", "")
        if lc_path:
            try:
                import config
                config.LOSSLESSCUT_PATH = lc_path
            except ImportError:
                pass

        # 恢復視窗大小位置
        geo = settings.get("window_geometry")
        if geo:
            try:
                self.geometry(geo)
            except Exception:
                pass

        # 恢復上次使用的分頁
        active_tab = settings.get("active_tab", 0)
        if active_tab != 0:
            self.tab_bar.set_active(active_tab)
            self._on_tab_change(active_tab)

    def _save_settings(self):
        """收集各面板狀態並儲存"""
        settings = {}
        settings.update(self.youtube_panel.get_state())
        settings.update(self.settings_panel.get_state())
        settings.update(self.video_panel.naming_panel.get_state())
        settings["window_geometry"] = self.geometry()
        settings["active_tab"] = self.tab_bar.active
        save_settings(settings)

    def _on_close(self):
        """視窗關閉時儲存設定"""
        try:
            self._save_settings()
        except Exception as e:
            logger.warning(f"儲存設定失敗: {e}")
        self.destroy()
