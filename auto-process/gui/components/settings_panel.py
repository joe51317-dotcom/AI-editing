"""
處理設定面板 — 裁剪模式選擇（自動/手動/跳過）+ 片頭片尾設定
自動模式以推薦卡片呈現，手動/跳過為次要選項。
"""
import os
import customtkinter as ctk
from tkinter import filedialog

from gui.theme import COLORS, FONT_FAMILY, FONT_FAMILY_MONO, FONT_SIZES, PADDING, CORNER_RADIUS
from gui.utils import parse_time_segments


class SettingsPanel(ctk.CTkFrame):
    """裁剪處理參數設定"""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=COLORS["bg_card"], corner_radius=CORNER_RADIUS, **kwargs)

        self._current_mode = "auto"  # auto / manual / skip

        # 標題
        ctk.CTkLabel(
            self,
            text="處理設定",
            font=(FONT_FAMILY, FONT_SIZES["heading"], "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=PADDING["section"], pady=(PADDING["inner"], 0))

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="x", padx=PADDING["section"], pady=PADDING["inner"])

        # ── 自動裁剪推薦卡片 ──────────────────────────
        self.auto_card = ctk.CTkFrame(
            content,
            fg_color=COLORS["bg_input"],
            border_color=COLORS["accent"],
            border_width=2,
            corner_radius=8,
        )
        self.auto_card.pack(fill="x", pady=(0, 6))

        # 卡片內容 padding
        card_inner = ctk.CTkFrame(self.auto_card, fg_color="transparent")
        card_inner.pack(fill="x", padx=10, pady=8)

        # 第一行：標題 + 推薦 badge
        header_row = ctk.CTkFrame(card_inner, fg_color="transparent")
        header_row.pack(fill="x")

        self.auto_title = ctk.CTkLabel(
            header_row,
            text="智慧自動裁剪",
            font=(FONT_FAMILY, FONT_SIZES["body"], "bold"),
            text_color=COLORS["accent"],
            anchor="w",
        )
        self.auto_title.pack(side="left")

        # 推薦 badge
        self.badge = ctk.CTkLabel(
            header_row,
            text=" 推薦 ",
            font=(FONT_FAMILY, FONT_SIZES["tiny"], "bold"),
            fg_color=COLORS["accent"],
            text_color=COLORS["bg_dark"],
            corner_radius=4,
            height=20,
        )
        self.badge.pack(side="left", padx=(8, 0))

        # 描述
        self.auto_desc = ctk.CTkLabel(
            card_inner,
            text="AI 演算法自動偵測語音邊界，精準移除靜音段落",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
            anchor="w",
        )
        self.auto_desc.pack(fill="x", pady=(3, 4))

        # 功能亮點
        features_row = ctk.CTkFrame(card_inner, fg_color="transparent")
        features_row.pack(fill="x")

        for feat in ["開頭/結尾靜音", "中間休息段", "免設定"]:
            feat_label = ctk.CTkLabel(
                features_row,
                text=f"  {feat}",
                font=(FONT_FAMILY, FONT_SIZES["tiny"]),
                text_color=COLORS["success"],
                anchor="w",
            )
            feat_label.pack(side="left", padx=(0, 10))

        # 讓整個卡片可點擊
        self._bind_click_recursive(self.auto_card, lambda e: self._select_mode("auto"))

        # ── 次要選項：手動 / 跳過 ─────────────────────
        alt_row = ctk.CTkFrame(content, fg_color="transparent")
        alt_row.pack(fill="x", pady=(0, 2))

        self.manual_btn = ctk.CTkButton(
            alt_row,
            text="手動裁剪",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_hover"],
            hover_color=COLORS["border"],
            text_color=COLORS["text_secondary"],
            border_color=COLORS["border_subtle"],
            border_width=1,
            height=30,
            corner_radius=6,
            command=lambda: self._select_mode("manual"),
        )
        self.manual_btn.pack(side="left", padx=(0, 6))

        self.skip_btn = ctk.CTkButton(
            alt_row,
            text="跳過裁剪",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_hover"],
            hover_color=COLORS["border"],
            text_color=COLORS["text_secondary"],
            border_color=COLORS["border_subtle"],
            border_width=1,
            height=30,
            corner_radius=6,
            command=lambda: self._select_mode("skip"),
        )
        self.skip_btn.pack(side="left")

        # ── 手動模式子面板（預設隱藏）────────────────
        self.manual_frame = ctk.CTkFrame(content, fg_color="transparent")
        self._build_manual_panel(self.manual_frame)

        # ── 片頭/片尾設定 ──────────────────────────
        self._build_intro_outro_section(content)

        # 初始視覺狀態
        self._update_visual()

    def _build_intro_outro_section(self, parent):
        """建立片頭/片尾設定區塊"""
        self._intro_path = None
        self._outro_path = None

        # 分隔線
        ctk.CTkFrame(
            parent, fg_color=COLORS["border_subtle"], height=1,
        ).pack(fill="x", pady=(8, 6))

        # checkbox
        self._intro_outro_var = ctk.BooleanVar(value=False)
        self._io_checkbox = ctk.CTkCheckBox(
            parent,
            text="片頭 / 片尾",
            variable=self._intro_outro_var,
            font=(FONT_FAMILY, FONT_SIZES["body"], "bold"),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_color=COLORS["border"],
            checkmark_color=COLORS["bg_dark"],
            corner_radius=4,
            command=self._toggle_intro_outro,
        )
        self._io_checkbox.pack(anchor="w", pady=(0, 4))

        # 詳細設定（預設隱藏）
        self._io_detail = ctk.CTkFrame(parent, fg_color="transparent")

        # --- 片頭列 ---
        intro_row = ctk.CTkFrame(self._io_detail, fg_color="transparent")
        intro_row.pack(fill="x", pady=2)

        ctk.CTkLabel(
            intro_row, text="片頭圖片:",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"], width=72, anchor="w",
        ).pack(side="left")

        ctk.CTkButton(
            intro_row, text="選擇", width=50, height=26,
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            text_color=COLORS["text_secondary"], corner_radius=4,
            command=self._browse_intro,
        ).pack(side="left", padx=(0, 4))

        self._intro_label = ctk.CTkLabel(
            intro_row, text="未選擇",
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            text_color=COLORS["text_dim"], anchor="w",
        )
        self._intro_label.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            intro_row, text="✕", width=24, height=24,
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            fg_color="transparent", hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_dim"], corner_radius=4,
            command=self._clear_intro,
        ).pack(side="right")

        # --- 片尾列 ---
        outro_row = ctk.CTkFrame(self._io_detail, fg_color="transparent")
        outro_row.pack(fill="x", pady=2)

        ctk.CTkLabel(
            outro_row, text="片尾圖片:",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"], width=72, anchor="w",
        ).pack(side="left")

        ctk.CTkButton(
            outro_row, text="選擇", width=50, height=26,
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            text_color=COLORS["text_secondary"], corner_radius=4,
            command=self._browse_outro,
        ).pack(side="left", padx=(0, 4))

        self._outro_label = ctk.CTkLabel(
            outro_row, text="未選擇",
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            text_color=COLORS["text_dim"], anchor="w",
        )
        self._outro_label.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            outro_row, text="✕", width=24, height=24,
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            fg_color="transparent", hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_dim"], corner_radius=4,
            command=self._clear_outro,
        ).pack(side="right")

        # --- 秒數設定列 ---
        dur_row = ctk.CTkFrame(self._io_detail, fg_color="transparent")
        dur_row.pack(fill="x", pady=(4, 2))

        ctk.CTkLabel(
            dur_row, text="片頭秒數:",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
        ).pack(side="left")

        self._intro_dur_var = ctk.StringVar(value="3")
        ctk.CTkEntry(
            dur_row, textvariable=self._intro_dur_var, width=45, height=26,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_input"], border_color=COLORS["border"],
            text_color=COLORS["text_primary"], corner_radius=4,
        ).pack(side="left", padx=(4, 12))

        ctk.CTkLabel(
            dur_row, text="片尾秒數:",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
        ).pack(side="left")

        self._outro_dur_var = ctk.StringVar(value="3")
        ctk.CTkEntry(
            dur_row, textvariable=self._outro_dur_var, width=45, height=26,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_input"], border_color=COLORS["border"],
            text_color=COLORS["text_primary"], corner_radius=4,
        ).pack(side="left", padx=(4, 0))

        # --- 淡入淡出列 ---
        fade_row = ctk.CTkFrame(self._io_detail, fg_color="transparent")
        fade_row.pack(fill="x", pady=2)

        ctk.CTkLabel(
            fade_row, text="淡入淡出:",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
        ).pack(side="left")

        self._fade_dur_var = ctk.StringVar(value="0.5")
        ctk.CTkEntry(
            fade_row, textvariable=self._fade_dur_var, width=45, height=26,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_input"], border_color=COLORS["border"],
            text_color=COLORS["text_primary"], corner_radius=4,
        ).pack(side="left", padx=(4, 4))

        ctk.CTkLabel(
            fade_row, text="秒",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_dim"],
        ).pack(side="left")

    def _toggle_intro_outro(self):
        """切換片頭/片尾設定面板顯示"""
        if self._intro_outro_var.get():
            self._io_detail.pack(fill="x", padx=(20, 0))
        else:
            self._io_detail.pack_forget()

    def _browse_intro(self):
        path = filedialog.askopenfilename(
            filetypes=[("圖片檔案", "*.jpg *.jpeg *.png *.bmp"), ("所有檔案", "*.*")]
        )
        if path:
            self._intro_path = path
            self._intro_label.configure(
                text=os.path.basename(path), text_color=COLORS["text_primary"])

    def _browse_outro(self):
        path = filedialog.askopenfilename(
            filetypes=[("圖片檔案", "*.jpg *.jpeg *.png *.bmp"), ("所有檔案", "*.*")]
        )
        if path:
            self._outro_path = path
            self._outro_label.configure(
                text=os.path.basename(path), text_color=COLORS["text_primary"])

    def _clear_intro(self):
        self._intro_path = None
        self._intro_label.configure(text="未選擇", text_color=COLORS["text_dim"])

    def _clear_outro(self):
        self._outro_path = None
        self._outro_label.configure(text="未選擇", text_color=COLORS["text_dim"])

    def _bind_click_recursive(self, widget, callback):
        """遞迴綁定點擊事件到 widget 及所有子元件"""
        widget.bind("<Button-1>", callback)
        for child in widget.winfo_children():
            self._bind_click_recursive(child, callback)

    def _build_manual_panel(self, parent):
        """建立手動裁剪輸入面板"""
        ctk.CTkLabel(
            parent,
            text="每行輸入一個片段（開始時間 - 結束時間），空白行分隔不同 Part：",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
            anchor="w",
        ).pack(fill="x", pady=(6, 4))

        self.manual_textbox = ctk.CTkTextbox(
            parent,
            font=(FONT_FAMILY_MONO, FONT_SIZES["small"]),
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            border_width=1,
            corner_radius=6,
            height=100,
            wrap="none",
        )
        self.manual_textbox.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            parent,
            text="格式：HH:MM:SS、MM:SS、或秒數。例如 00:00 - 45:30",
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            text_color=COLORS["text_dim"],
            anchor="w",
        ).pack(fill="x")

        # 錯誤訊息（預設隱藏）
        self.manual_error_label = ctk.CTkLabel(
            parent,
            text="",
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            text_color=COLORS["error"],
            anchor="w",
        )

    def _select_mode(self, mode):
        """切換裁剪模式"""
        if mode == self._current_mode:
            return
        self._current_mode = mode
        self._update_visual()

    def _update_visual(self):
        """根據目前模式更新所有元件的視覺狀態"""
        mode = self._current_mode

        # 自動卡片狀態
        if mode == "auto":
            self.auto_card.configure(
                border_color=COLORS["accent"],
                fg_color=COLORS["bg_input"],
            )
            self.auto_title.configure(text_color=COLORS["accent"])
            self.auto_desc.configure(text_color=COLORS["text_secondary"])
            self.badge.configure(fg_color=COLORS["accent"], text_color=COLORS["bg_dark"])
        else:
            self.auto_card.configure(
                border_color=COLORS["border_subtle"],
                fg_color=COLORS["bg_dark"],
            )
            self.auto_title.configure(text_color=COLORS["text_dim"])
            self.auto_desc.configure(text_color=COLORS["text_dim"])
            self.badge.configure(fg_color=COLORS["border"], text_color=COLORS["text_dim"])

        # 次要按鈕狀態
        if mode == "manual":
            self.manual_btn.configure(
                fg_color=COLORS["accent_dim"],
                text_color=COLORS["text_primary"],
                border_color=COLORS["accent"],
            )
            self.skip_btn.configure(
                fg_color=COLORS["bg_hover"],
                text_color=COLORS["text_secondary"],
                border_color=COLORS["border_subtle"],
            )
        elif mode == "skip":
            self.skip_btn.configure(
                fg_color=COLORS["accent_dim"],
                text_color=COLORS["text_primary"],
                border_color=COLORS["accent"],
            )
            self.manual_btn.configure(
                fg_color=COLORS["bg_hover"],
                text_color=COLORS["text_secondary"],
                border_color=COLORS["border_subtle"],
            )
        else:
            self.manual_btn.configure(
                fg_color=COLORS["bg_hover"],
                text_color=COLORS["text_secondary"],
                border_color=COLORS["border_subtle"],
            )
            self.skip_btn.configure(
                fg_color=COLORS["bg_hover"],
                text_color=COLORS["text_secondary"],
                border_color=COLORS["border_subtle"],
            )

        # 手動面板顯示/隱藏
        if mode == "manual":
            self.manual_frame.pack(fill="x")
        else:
            self.manual_frame.pack_forget()

    # --- Public API ---

    def get_trim_mode(self):
        """回傳目前模式: 'auto', 'manual', 'skip'"""
        return self._current_mode

    def is_trim_enabled(self):
        """向後相容：非 skip 模式都算啟用"""
        return self._current_mode != "skip"

    def get_speech_threshold(self):
        """使用 config.py 的優化預設值"""
        from config import SPEECH_THRESHOLD_DB
        return SPEECH_THRESHOLD_DB

    def get_break_threshold(self):
        """使用 config.py 的優化預設值"""
        from config import BREAK_THRESHOLD_SECONDS
        return BREAK_THRESHOLD_SECONDS

    def get_manual_segments(self):
        """
        解析手動輸入的時間片段。

        Returns:
            tuple[list[list[dict]], list[str]]: (parts, errors)
        """
        text = self.manual_textbox.get("1.0", "end")
        return parse_time_segments(text)

    def set_manual_segments_text(self, text):
        """預填手動時間文字（供自動偵測結果回填）"""
        self.manual_textbox.delete("1.0", "end")
        self.manual_textbox.insert("1.0", text)

    def show_manual_errors(self, errors):
        """顯示手動模式解析錯誤"""
        if errors:
            self.manual_error_label.configure(text="\n".join(errors))
            self.manual_error_label.pack(fill="x", pady=(2, 0))
        else:
            self.manual_error_label.pack_forget()
            self.manual_error_label.configure(text="")

    def is_intro_outro_enabled(self):
        """是否啟用片頭/片尾"""
        return self._intro_outro_var.get()

    def get_intro_outro_settings(self):
        """取得片頭/片尾設定"""
        try:
            intro_dur = float(self._intro_dur_var.get())
        except ValueError:
            intro_dur = 3.0
        try:
            outro_dur = float(self._outro_dur_var.get())
        except ValueError:
            outro_dur = 3.0
        try:
            fade_dur = float(self._fade_dur_var.get())
        except ValueError:
            fade_dur = 0.5

        return {
            "enabled": self._intro_outro_var.get(),
            "intro_path": self._intro_path,
            "outro_path": self._outro_path,
            "intro_duration": max(0.5, intro_dur),
            "outro_duration": max(0.5, outro_dur),
            "fade_duration": max(0, fade_dur),
        }

    def get_state(self):
        """取得面板狀態供設定持久化"""
        return {
            "trim_mode": self._current_mode,
            "intro_outro_enabled": self._intro_outro_var.get(),
            "intro_path": self._intro_path,
            "outro_path": self._outro_path,
            "intro_duration": self._intro_dur_var.get(),
            "outro_duration": self._outro_dur_var.get(),
            "fade_duration": self._fade_dur_var.get(),
        }

    def set_state(self, state):
        """從設定恢復面板狀態"""
        mode = state.get("trim_mode", "auto")
        if mode in ("auto", "manual", "skip"):
            self._current_mode = mode
            self._update_visual()

        # 片頭/片尾
        if state.get("intro_outro_enabled"):
            self._intro_outro_var.set(True)
            self._toggle_intro_outro()
        if state.get("intro_path") and os.path.isfile(state["intro_path"]):
            self._intro_path = state["intro_path"]
            self._intro_label.configure(
                text=os.path.basename(state["intro_path"]),
                text_color=COLORS["text_primary"])
        if state.get("outro_path") and os.path.isfile(state["outro_path"]):
            self._outro_path = state["outro_path"]
            self._outro_label.configure(
                text=os.path.basename(state["outro_path"]),
                text_color=COLORS["text_primary"])
        if "intro_duration" in state:
            self._intro_dur_var.set(state["intro_duration"])
        if "outro_duration" in state:
            self._outro_dur_var.set(state["outro_duration"])
        if "fade_duration" in state:
            self._fade_dur_var.set(state["fade_duration"])
