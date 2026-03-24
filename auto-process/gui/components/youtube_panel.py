"""
YouTube 設定面板 — 登入、播放清單、隱私、縮圖
"""
import os
import threading
import customtkinter as ctk
from tkinter import filedialog

from gui.theme import COLORS, FONT_FAMILY, FONT_SIZES, PADDING, CORNER_RADIUS


class YouTubePanel(ctk.CTkFrame):
    """YouTube 上傳設定面板"""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=COLORS["bg_card"], corner_radius=CORNER_RADIUS, **kwargs)

        self.youtube_service = None
        self.playlists = []
        self.thumbnail_path = None

        # 標題
        ctk.CTkLabel(
            self,
            text="YouTube",
            font=(FONT_FAMILY, FONT_SIZES["heading"], "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=PADDING["section"], pady=(PADDING["inner"], 0))

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="x", padx=PADDING["section"], pady=PADDING["inner"])

        # --- Row 1: 帳號 + 登入按鈕 ---
        row1 = ctk.CTkFrame(content, fg_color="transparent")
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

        # --- Row 2: 播放清單 ---
        row2 = ctk.CTkFrame(content, fg_color="transparent")
        row2.pack(fill="x", pady=2)

        ctk.CTkLabel(
            row2,
            text="播放清單:",
            font=(FONT_FAMILY, FONT_SIZES["body"]),
            text_color=COLORS["text_secondary"],
            width=70,
            anchor="w",
        ).pack(side="left")

        self.playlist_var = ctk.StringVar(value="（不加入播放清單）")
        self.playlist_menu = ctk.CTkComboBox(
            row2,
            variable=self.playlist_var,
            values=["（不加入播放清單）"],
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
            height=28,
            corner_radius=4,
            state="readonly",
        )
        self.playlist_menu.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # --- Row 3: 隱私 + 封面圖 ---
        row3 = ctk.CTkFrame(content, fg_color="transparent")
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

                # 回到主線程更新 UI
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

        # 更新播放清單
        self.playlists = playlists
        playlist_names = ["（不加入播放清單）"] + [p["title"] for p in playlists]
        self.playlist_menu.configure(values=playlist_names)
        self.playlist_var.set("（不加入播放清單）")

    def _on_login_failed(self, error=""):
        """登入失敗"""
        self.account_label.configure(
            text=f"登入失敗 {error}",
            text_color=COLORS["error"],
        )
        self.login_btn.configure(text="重試登入", state="normal")

    def _logout(self):
        """登出"""
        self.youtube_service = None
        self.playlists = []
        self.account_label.configure(text="未登入", text_color=COLORS["text_dim"])
        self.login_btn.configure(text="登入 YouTube", command=self._login)
        self.playlist_menu.configure(values=["（不加入播放清單）"])
        self.playlist_var.set("（不加入播放清單）")

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

    def get_privacy_status(self):
        """取得隱私設定值"""
        mapping = {"公開": "public", "不公開": "unlisted", "私人": "private"}
        return mapping.get(self.privacy_var.get(), "unlisted")

    def get_selected_playlist_id(self):
        """取得選擇的播放清單 ID（None 表示不加入）"""
        selected = self.playlist_var.get()
        for p in self.playlists:
            if p["title"] == selected:
                return p["id"]
        return None

    def get_thumbnail_path(self):
        return self.thumbnail_path

    def get_youtube_service(self):
        return self.youtube_service

    def is_logged_in(self):
        return self.youtube_service is not None
