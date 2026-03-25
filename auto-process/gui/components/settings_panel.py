"""
處理設定面板 — 裁剪模式選擇（自動/手動/跳過）
自動模式以推薦卡片呈現，手動/跳過為次要選項。
"""
import customtkinter as ctk

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

        # 初始視覺狀態
        self._update_visual()

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

    def get_state(self):
        """取得面板狀態供設定持久化"""
        return {"trim_mode": self._current_mode}

    def set_state(self, state):
        """從設定恢復面板狀態"""
        mode = state.get("trim_mode", "auto")
        if mode in ("auto", "manual", "skip"):
            self._current_mode = mode
            self._update_visual()
