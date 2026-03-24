"""
處理設定面板 — 裁剪模式選擇（自動/手動/跳過）+ 參數
"""
import customtkinter as ctk

from gui.theme import COLORS, FONT_FAMILY, FONT_SIZES, PADDING, CORNER_RADIUS
from gui.utils import parse_time_segments


class SettingsPanel(ctk.CTkFrame):
    """裁剪處理參數設定"""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=COLORS["bg_card"], corner_radius=CORNER_RADIUS, **kwargs)

        # 標題
        ctk.CTkLabel(
            self,
            text="處理設定",
            font=(FONT_FAMILY, FONT_SIZES["heading"], "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=PADDING["section"], pady=(PADDING["inner"], 0))

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="x", padx=PADDING["section"], pady=PADDING["inner"])

        # --- 模式切換 ---
        self.trim_mode = ctk.StringVar(value="auto")
        self.mode_btn = ctk.CTkSegmentedButton(
            content,
            values=["auto", "manual", "skip"],
            variable=self.trim_mode,
            command=self._on_mode_change,
            font=(FONT_FAMILY, FONT_SIZES["body"]),
            fg_color=COLORS["bg_input"],
            selected_color=COLORS["accent"],
            selected_hover_color=COLORS["accent_hover"],
            unselected_color=COLORS["bg_hover"],
            unselected_hover_color=COLORS["border"],
            text_color=COLORS["bg_dark"],
            text_color_disabled=COLORS["text_dim"],
            corner_radius=6,
        )
        self.mode_btn.pack(fill="x", pady=(0, 8))

        # 自訂按鈕文字（CTkSegmentedButton 不直接支援 label mapping，但 values 就是顯示文字）
        # 所以改用中文 values
        self.mode_btn.configure(values=["自動裁剪", "手動裁剪", "跳過裁剪"])
        self.trim_mode.set("自動裁剪")

        # === 自動模式子面板 ===
        self.auto_frame = ctk.CTkFrame(content, fg_color="transparent")
        self._build_auto_panel(self.auto_frame)
        self.auto_frame.pack(fill="x")

        # === 手動模式子面板 ===
        self.manual_frame = ctk.CTkFrame(content, fg_color="transparent")
        self._build_manual_panel(self.manual_frame)
        # 預設隱藏

        # 模式值映射（中文 → 內部值）
        self._mode_map = {"自動裁剪": "auto", "手動裁剪": "manual", "跳過裁剪": "skip"}

    def _build_auto_panel(self, parent):
        """建立自動裁剪參數面板"""
        # Row 1: 語音門檻
        row1 = ctk.CTkFrame(parent, fg_color="transparent")
        row1.pack(fill="x", pady=2)

        ctk.CTkLabel(
            row1,
            text="語音門檻:",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
        ).pack(side="left")

        self.threshold_var = ctk.StringVar(value="-20")
        ctk.CTkEntry(
            row1,
            textvariable=self.threshold_var,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            width=50,
            height=26,
            corner_radius=4,
        ).pack(side="left", padx=(4, 2))

        ctk.CTkLabel(
            row1,
            text="dB",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
        ).pack(side="left")

        # Row 2: 休息切割門檻
        row2 = ctk.CTkFrame(parent, fg_color="transparent")
        row2.pack(fill="x", pady=2)

        ctk.CTkLabel(
            row2,
            text="休息切割門檻:",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
        ).pack(side="left")

        self.break_var = ctk.StringVar(value="300")
        ctk.CTkEntry(
            row2,
            textvariable=self.break_var,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            width=60,
            height=26,
            corner_radius=4,
        ).pack(side="left", padx=(4, 2))

        ctk.CTkLabel(
            row2,
            text="秒（超過此時間的靜音會切割成獨立影片）",
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            text_color=COLORS["text_dim"],
        ).pack(side="left", padx=(4, 0))

    def _build_manual_panel(self, parent):
        """建立手動裁剪輸入面板"""
        # 說明文字
        ctk.CTkLabel(
            parent,
            text="每行輸入一個片段（開始時間 - 結束時間），空白行分隔不同 Part：",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        # 時間輸入框
        self.manual_textbox = ctk.CTkTextbox(
            parent,
            font=("Consolas", FONT_SIZES["small"]),
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            border_width=1,
            corner_radius=6,
            height=100,
            wrap="none",
        )
        self.manual_textbox.pack(fill="x", pady=(0, 4))

        # 格式提示
        ctk.CTkLabel(
            parent,
            text="格式：HH:MM:SS、MM:SS、或秒數。例如 00:00 - 45:30",
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            text_color=COLORS["text_dim"],
            anchor="w",
        ).pack(fill="x")

    def _on_mode_change(self, value):
        """模式切換時顯示/隱藏對應面板"""
        mode = self._mode_map.get(value, "auto")

        if mode == "auto":
            self.manual_frame.pack_forget()
            self.auto_frame.pack(fill="x")
        elif mode == "manual":
            self.auto_frame.pack_forget()
            self.manual_frame.pack(fill="x")
        else:  # skip
            self.auto_frame.pack_forget()
            self.manual_frame.pack_forget()

    # --- Public API ---

    def get_trim_mode(self):
        """回傳目前模式: 'auto', 'manual', 'skip'"""
        return self._mode_map.get(self.trim_mode.get(), "auto")

    def is_trim_enabled(self):
        """向後相容：非 skip 模式都算啟用"""
        return self.get_trim_mode() != "skip"

    def get_speech_threshold(self):
        try:
            return int(self.threshold_var.get())
        except ValueError:
            return -20

    def get_break_threshold(self):
        try:
            return float(self.break_var.get())
        except ValueError:
            return 300.0

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
