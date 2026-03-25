"""
輸出設定面板 — 儲存位置 + YouTube 上傳（可選）
"""
import os
import threading
import customtkinter as ctk
from tkinter import filedialog

from gui.theme import COLORS, FONT_FAMILY, FONT_SIZES, PADDING, CORNER_RADIUS


class SearchablePlaylistPicker(ctk.CTkFrame):
    """可搜尋、可滾動的播放清單選擇器"""

    def __init__(self, master, on_create_playlist=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        self._all_items = []  # [{'id': str, 'title': str}, ...]
        self._selected_id = None
        self._selected_title = "（不加入播放清單）"
        self._dropdown_visible = False
        self._on_create_playlist = on_create_playlist  # callback(title) → {'id', 'title'} or None

        # --- 顯示按鈕（模擬 ComboBox 外觀） ---
        self.display_btn = ctk.CTkButton(
            self,
            text="（不加入播放清單）",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["bg_hover"],
            border_color=COLORS["border"],
            border_width=1,
            text_color=COLORS["text_primary"],
            height=28,
            corner_radius=4,
            anchor="w",
            command=self._toggle_dropdown,
        )
        self.display_btn.pack(fill="x")

        # --- 下拉面板（預設隱藏） ---
        self.dropdown_frame = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_card"],
            border_color=COLORS["border"],
            border_width=1,
            corner_radius=6,
        )

        # 搜尋欄
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", self._on_search)
        self.search_entry = ctk.CTkEntry(
            self.dropdown_frame,
            textvariable=self.search_var,
            placeholder_text="搜尋播放清單...",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            placeholder_text_color=COLORS["text_dim"],
            height=28,
            corner_radius=4,
        )
        self.search_entry.pack(fill="x", padx=6, pady=(6, 4))

        # 可滾動清單
        self.list_frame = ctk.CTkScrollableFrame(
            self.dropdown_frame,
            fg_color="transparent",
            height=150,
        )
        self.list_frame.pack(fill="x", padx=6, pady=(0, 4))

        # 新增播放清單列
        create_row = ctk.CTkFrame(self.dropdown_frame, fg_color="transparent")
        create_row.pack(fill="x", padx=6, pady=(0, 6))

        self.new_playlist_entry = ctk.CTkEntry(
            create_row,
            placeholder_text="新播放清單名稱...",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            placeholder_text_color=COLORS["text_dim"],
            height=28,
            corner_radius=4,
        )
        self.new_playlist_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.create_btn = ctk.CTkButton(
            create_row,
            text="+ 新增",
            width=60,
            height=28,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            corner_radius=4,
            command=self._create_playlist,
        )
        self.create_btn.pack(side="right")

        # 綁定全域點擊關閉
        self._click_bind_id = None

    def _toggle_dropdown(self):
        if self._dropdown_visible:
            self._hide_dropdown()
        else:
            self._show_dropdown()

    def _show_dropdown(self):
        self._dropdown_visible = True
        self.dropdown_frame.pack(fill="x", pady=(2, 0))
        self.search_var.set("")
        self.search_entry.focus_set()
        self._render_items(self._get_filtered_items())

        # 綁定點擊外部關閉
        root = self.winfo_toplevel()
        self._click_bind_id = root.bind("<Button-1>", self._on_global_click, add="+")

    def _hide_dropdown(self):
        self._dropdown_visible = False
        self.dropdown_frame.pack_forget()

        if self._click_bind_id:
            root = self.winfo_toplevel()
            try:
                root.unbind("<Button-1>", self._click_bind_id)
            except Exception:
                pass
            self._click_bind_id = None

    def _on_global_click(self, event):
        """點擊下拉面板外部時關閉（用座標碰撞檢測，避免 scrollbar 誤判）"""
        try:
            # 取得 dropdown_frame 在螢幕上的邊界框
            x = self.dropdown_frame.winfo_rootx()
            y = self.dropdown_frame.winfo_rooty()
            w = self.dropdown_frame.winfo_width()
            h = self.dropdown_frame.winfo_height()
            # 點擊座標落在 dropdown 內部，不關閉
            if x <= event.x_root <= x + w and y <= event.y_root <= y + h:
                return
            # 也檢查 display_btn（點擊按鈕本身由 _toggle_dropdown 處理）
            bx = self.display_btn.winfo_rootx()
            by = self.display_btn.winfo_rooty()
            bw = self.display_btn.winfo_width()
            bh = self.display_btn.winfo_height()
            if bx <= event.x_root <= bx + bw and by <= event.y_root <= by + bh:
                return
        except Exception:
            pass
        self._hide_dropdown()

    def _on_search(self, *args):
        """搜尋文字變化時過濾清單"""
        self._render_items(self._get_filtered_items())

    def _get_filtered_items(self):
        """根據搜尋文字過濾播放清單"""
        query = self.search_var.get().strip().lower()
        items = [{"id": None, "title": "（不加入播放清單）"}] + self._all_items
        if not query:
            return items
        return [p for p in items if query in p["title"].lower()]

    def _render_items(self, items):
        """渲染過濾後的清單項目"""
        for widget in self.list_frame.winfo_children():
            widget.destroy()

        for item in items:
            is_selected = (item["id"] == self._selected_id and
                           item["title"] == self._selected_title)
            btn = ctk.CTkButton(
                self.list_frame,
                text=item["title"],
                font=(FONT_FAMILY, FONT_SIZES["small"]),
                fg_color=COLORS["accent_dim"] if is_selected else "transparent",
                hover_color=COLORS["bg_hover"],
                text_color=COLORS["text_primary"],
                height=28,
                corner_radius=4,
                anchor="w",
                command=lambda p=item: self._select_item(p),
            )
            btn.pack(fill="x", pady=1)

    def _select_item(self, item):
        """選擇播放清單"""
        self._selected_id = item["id"]
        self._selected_title = item["title"]
        self.display_btn.configure(text=item["title"])
        self._hide_dropdown()

    def _create_playlist(self):
        """建立新播放清單"""
        title = self.new_playlist_entry.get().strip()
        if not title:
            return
        if not self._on_create_playlist:
            return

        self.create_btn.configure(text="建立中...", state="disabled")

        def _do_create():
            result = self._on_create_playlist(title)
            self.after(0, lambda: self._on_playlist_created(result))

        import threading
        threading.Thread(target=_do_create, daemon=True).start()

    def _on_playlist_created(self, result):
        """播放清單建立完成"""
        self.create_btn.configure(text="+ 新增", state="normal")
        if result:
            self._all_items.insert(0, result)
            self._select_item(result)
            self.new_playlist_entry.delete(0, "end")
        else:
            self.new_playlist_entry.configure(border_color=COLORS["error"])
            self.after(2000, lambda: self.new_playlist_entry.configure(border_color=COLORS["border"]))

    # --- Public API ---

    def set_playlists(self, playlists):
        """設定播放清單資料"""
        self._all_items = playlists
        self._selected_id = None
        self._selected_title = "（不加入播放清單）"
        self.display_btn.configure(text="（不加入播放清單）")

    def get_selected_id(self):
        return self._selected_id

    def reset(self):
        self._all_items = []
        self._selected_id = None
        self._selected_title = "（不加入播放清單）"
        self.display_btn.configure(text="（不加入播放清單）")
        self._hide_dropdown()


class YouTubePanel(ctk.CTkFrame):
    """輸出設定面板 — 儲存位置 + YouTube 上傳（可選）"""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=COLORS["bg_card"], corner_radius=CORNER_RADIUS, **kwargs)

        self.youtube_service = None
        self.playlists = []
        self.thumbnail_path = None

        # 標題
        ctk.CTkLabel(
            self,
            text="輸出設定",
            font=(FONT_FAMILY, FONT_SIZES["heading"], "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=PADDING["section"], pady=(PADDING["inner"], 0))

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="x", padx=PADDING["section"], pady=PADDING["inner"])

        # ── 儲存位置 ────────────────────────────────
        row_output = ctk.CTkFrame(content, fg_color="transparent")
        row_output.pack(fill="x", pady=2)

        ctk.CTkLabel(
            row_output,
            text="儲存位置:",
            font=(FONT_FAMILY, FONT_SIZES["body"]),
            text_color=COLORS["text_secondary"],
            width=70,
            anchor="w",
        ).pack(side="left")

        default_dir = os.path.expanduser("~/Desktop")
        self.output_dir_var = ctk.StringVar(value=default_dir)
        self.output_dir_entry = ctk.CTkEntry(
            row_output,
            textvariable=self.output_dir_var,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            height=28,
            corner_radius=4,
            state="readonly",
        )
        self.output_dir_entry.pack(side="left", fill="x", expand=True, padx=(4, 4))

        ctk.CTkButton(
            row_output,
            text="瀏覽",
            width=60,
            height=28,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_hover"],
            hover_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            corner_radius=6,
            command=self._browse_output_dir,
        ).pack(side="right")

        # ── 上傳到 YouTube checkbox ─────────────────
        self.upload_var = ctk.BooleanVar(value=True)
        self.upload_checkbox = ctk.CTkCheckBox(
            content,
            text="上傳到 YouTube",
            variable=self.upload_var,
            font=(FONT_FAMILY, FONT_SIZES["body"]),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_color=COLORS["border"],
            checkmark_color=COLORS["bg_dark"],
            corner_radius=4,
            command=self._toggle_youtube_settings,
        )
        self.upload_checkbox.pack(anchor="w", pady=(6, 4))

        # ── YouTube 設定容器（可折疊）────────────────
        self.yt_settings_frame = ctk.CTkFrame(content, fg_color="transparent")
        self.yt_settings_frame.pack(fill="x")

        # --- Row 1: 帳號 + 登入按鈕 ---
        row1 = ctk.CTkFrame(self.yt_settings_frame, fg_color="transparent")
        row1.pack(fill="x", pady=2)

        ctk.CTkLabel(
            row1,
            text="帳號:",
            font=(FONT_FAMILY, FONT_SIZES["body"]),
            text_color=COLORS["text_secondary"],
            width=70,
            anchor="w",
        ).pack(side="left")

        self.account_label = ctk.CTkLabel(
            row1,
            text="未登入",
            font=(FONT_FAMILY, FONT_SIZES["body"]),
            text_color=COLORS["text_dim"],
            anchor="w",
        )
        self.account_label.pack(side="left", fill="x", expand=True)

        self.login_btn = ctk.CTkButton(
            row1,
            text="登入 YouTube",
            width=110,
            height=28,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            corner_radius=6,
            command=self._login,
        )
        self.login_btn.pack(side="right")

        # --- Row 2: 播放清單（可搜尋） ---
        row2 = ctk.CTkFrame(self.yt_settings_frame, fg_color="transparent")
        row2.pack(fill="x", pady=2)

        ctk.CTkLabel(
            row2,
            text="播放清單:",
            font=(FONT_FAMILY, FONT_SIZES["body"]),
            text_color=COLORS["text_secondary"],
            width=70,
            anchor="w",
        ).pack(side="left", anchor="n", pady=4)

        self.playlist_picker = SearchablePlaylistPicker(
            row2, on_create_playlist=self._create_new_playlist
        )
        self.playlist_picker.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # --- Row 3: 隱私 + 封面圖 ---
        row3 = ctk.CTkFrame(self.yt_settings_frame, fg_color="transparent")
        row3.pack(fill="x", pady=2)

        ctk.CTkLabel(
            row3,
            text="隱私:",
            font=(FONT_FAMILY, FONT_SIZES["body"]),
            text_color=COLORS["text_secondary"],
            width=70,
            anchor="w",
        ).pack(side="left")

        self.privacy_var = ctk.StringVar(value="不公開")
        self.privacy_menu = ctk.CTkComboBox(
            row3,
            variable=self.privacy_var,
            values=["公開", "不公開", "私人"],
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            dropdown_font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            button_color=COLORS["accent_dim"],
            button_hover_color=COLORS["accent"],
            text_color=COLORS["text_primary"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_text_color=COLORS["text_primary"],
            dropdown_hover_color=COLORS["bg_hover"],
            width=120,
            height=28,
            corner_radius=4,
            state="readonly",
        )
        self.privacy_menu.pack(side="left", padx=(4, 16))

        ctk.CTkLabel(
            row3,
            text="封面圖:",
            font=(FONT_FAMILY, FONT_SIZES["body"]),
            text_color=COLORS["text_secondary"],
        ).pack(side="left")

        self.thumb_label = ctk.CTkLabel(
            row3,
            text="未選擇",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_dim"],
            anchor="w",
        )
        self.thumb_label.pack(side="left", padx=(4, 4))

        ctk.CTkButton(
            row3,
            text="選擇圖片",
            width=80,
            height=26,
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            fg_color=COLORS["bg_hover"],
            hover_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            corner_radius=4,
            command=self._browse_thumbnail,
        ).pack(side="left")

        ctk.CTkButton(
            row3,
            text="清除",
            width=50,
            height=26,
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            fg_color="transparent",
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=4,
            command=self._clear_thumbnail,
        ).pack(side="left", padx=(2, 0))

    # ── 儲存位置 ────────────────────────────────────

    def _browse_output_dir(self):
        """選擇輸出資料夾"""
        current = self.output_dir_var.get()
        initial = current if os.path.isdir(current) else os.path.expanduser("~/Desktop")
        path = filedialog.askdirectory(initialdir=initial, title="選擇輸出資料夾")
        if path:
            self.output_dir_entry.configure(state="normal")
            self.output_dir_var.set(path)
            self.output_dir_entry.configure(state="readonly")
            # 驗證可寫入
            if not os.access(path, os.W_OK):
                self.output_dir_entry.configure(border_color=COLORS["error"])
                import logging
                logging.getLogger(__name__).warning(f"輸出目錄無寫入權限: {path}")
            else:
                self.output_dir_entry.configure(border_color=COLORS["border"])

    # ── 新增播放清單 ────────────────────────────────

    def _create_new_playlist(self, title):
        """建立新播放清單（在背景線程中呼叫）"""
        if not self.youtube_service:
            return None
        try:
            from youtube_api import create_playlist
            return create_playlist(self.youtube_service, title)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"建立播放清單失敗: {e}")
            return None

    # ── YouTube 上傳切換 ────────────────────────────

    def _toggle_youtube_settings(self):
        """根據 checkbox 顯示/隱藏 YouTube 設定"""
        if self.upload_var.get():
            self.yt_settings_frame.pack(fill="x")
        else:
            self.yt_settings_frame.pack_forget()

    # ── YouTube 登入 ────────────────────────────────

    def _login(self):
        """啟動 YouTube OAuth2 登入（背景線程）"""
        self.login_btn.configure(text="登入中...", state="disabled")
        thread = threading.Thread(target=self._login_thread, daemon=True)
        thread.start()

    def _login_thread(self):
        """背景執行 OAuth2 登入"""
        try:
            from youtube_api import get_authenticated_service, get_channel_info
            service = get_authenticated_service()
            if service:
                self.youtube_service = service
                info = get_channel_info(service)
                playlists = []
                try:
                    from youtube_api import list_playlists
                    playlists = list_playlists(service)
                except Exception:
                    pass

                self.after(0, lambda: self._on_login_success(info, playlists))
            else:
                self.after(0, self._on_login_failed)
        except Exception as e:
            self.after(0, lambda: self._on_login_failed(str(e)))

    def _on_login_success(self, info, playlists):
        """登入成功，更新 UI"""
        name = info["name"] if info else "已登入"
        self.account_label.configure(
            text=name,
            text_color=COLORS["success"],
        )
        self.login_btn.configure(text="登出", state="normal")
        self.login_btn.configure(command=self._logout)

        # 更新播放清單（使用可搜尋選擇器）
        self.playlists = playlists
        self.playlist_picker.set_playlists(playlists)

    def _on_login_failed(self, error=""):
        """登入失敗"""
        self.account_label.configure(
            text=f"登入失敗 {error}",
            text_color=COLORS["error"],
        )
        self.login_btn.configure(text="重試登入", state="normal")

    def _logout(self):
        """登出並刪除 token，確保下次登入用新帳號"""
        self.youtube_service = None
        self.playlists = []
        self.account_label.configure(text="未登入", text_color=COLORS["text_dim"])
        self.login_btn.configure(text="登入 YouTube", command=self._login)
        self.playlist_picker.reset()

        # 清除 token（keyring + 明文檔），確保下次登入重新授權
        try:
            from youtube_uploader import _delete_token_from_keyring
            _delete_token_from_keyring()
        except Exception:
            pass
        try:
            from config import YOUTUBE_TOKEN_PATH
            if os.path.exists(YOUTUBE_TOKEN_PATH):
                os.remove(YOUTUBE_TOKEN_PATH)
        except Exception:
            pass

    # ── 縮圖 ────────────────────────────────────────

    def _browse_thumbnail(self):
        """選擇縮圖"""
        path = filedialog.askopenfilename(
            filetypes=[("圖片檔案", "*.jpg *.jpeg *.png"), ("所有檔案", "*.*")]
        )
        if path:
            self.thumbnail_path = path
            self.thumb_label.configure(
                text=os.path.basename(path),
                text_color=COLORS["text_primary"],
            )

    def _clear_thumbnail(self):
        """清除縮圖"""
        self.thumbnail_path = None
        self.thumb_label.configure(text="未選擇", text_color=COLORS["text_dim"])

    # ── Public API ──────────────────────────────────

    def get_state(self):
        """取得面板狀態供設定持久化"""
        return {
            "output_dir": self.output_dir_var.get(),
            "upload_enabled": self.upload_var.get(),
            "privacy_status": self.privacy_var.get(),
        }

    def set_state(self, state):
        """從設定恢復面板狀態"""
        if "output_dir" in state:
            d = state["output_dir"]
            if os.path.isdir(d):
                self.output_dir_entry.configure(state="normal")
                self.output_dir_var.set(d)
                self.output_dir_entry.configure(state="readonly")
        if "upload_enabled" in state:
            self.upload_var.set(state["upload_enabled"])
            self._toggle_youtube_settings()
        if "privacy_status" in state:
            self.privacy_var.set(state["privacy_status"])

    def get_output_dir(self):
        """取得輸出資料夾路徑"""
        return self.output_dir_var.get()

    def is_upload_enabled(self):
        """是否啟用 YouTube 上傳"""
        return self.upload_var.get()

    def get_privacy_status(self):
        """取得隱私設定值"""
        mapping = {"公開": "public", "不公開": "unlisted", "私人": "private"}
        return mapping.get(self.privacy_var.get(), "unlisted")

    def get_selected_playlist_id(self):
        """取得選擇的播放清單 ID（None 表示不加入）"""
        return self.playlist_picker.get_selected_id()

    def get_thumbnail_path(self):
        return self.thumbnail_path

    def get_youtube_service(self):
        return self.youtube_service

    def is_logged_in(self):
        return self.youtube_service is not None
