"""
日誌檢視器 — 可收合的即時日誌面板
"""
import logging
import customtkinter as ctk

from gui.theme import COLORS, FONT_FAMILY, FONT_SIZES, PADDING, CORNER_RADIUS


class TextboxLogHandler(logging.Handler):
    """將 logging 輸出導向 CTkTextbox 的 Handler"""

    def __init__(self, textbox, log_viewer=None, max_lines=500):
        super().__init__()
        self.textbox = textbox
        self.log_viewer = log_viewer
        self.max_lines = max_lines

    def emit(self, record):
        msg = self.format(record) + "\n"
        try:
            self.textbox.after(0, self._append, msg)
            # ERROR 級別自動展開日誌面板
            if record.levelno >= logging.ERROR and self.log_viewer:
                self.textbox.after(0, self._auto_expand)
        except Exception:
            pass

    def _auto_expand(self):
        if self.log_viewer and self.log_viewer.collapsed:
            self.log_viewer.toggle()

    def _append(self, msg):
        self.textbox.configure(state="normal")
        self.textbox.insert("end", msg)
        # 限制行數
        line_count = int(self.textbox.index("end-1c").split(".")[0])
        if line_count > self.max_lines:
            self.textbox.delete("1.0", f"{line_count - self.max_lines}.0")
        self.textbox.see("end")
        self.textbox.configure(state="disabled")


class LogViewer(ctk.CTkFrame):
    """可收合的日誌檢視面板"""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=COLORS["bg_card"], corner_radius=CORNER_RADIUS, **kwargs)
        self.collapsed = False

        # 標題列（可點擊收合）
        header = ctk.CTkFrame(self, fg_color="transparent", cursor="hand2")
        header.pack(fill="x", padx=PADDING["section"], pady=(PADDING["inner"], 0))
        header.bind("<Button-1>", lambda e: self.toggle())

        self.toggle_label = ctk.CTkLabel(
            header,
            text="▼ 日誌",
            font=(FONT_FAMILY, FONT_SIZES["heading"], "bold"),
            text_color=COLORS["text_primary"],
            cursor="hand2",
        )
        self.toggle_label.pack(side="left")
        self.toggle_label.bind("<Button-1>", lambda e: self.toggle())

        self.clear_btn = ctk.CTkButton(
            header,
            text="清除",
            width=50,
            height=22,
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            fg_color="transparent",
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=4,
            command=self.clear,
        )
        self.clear_btn.pack(side="right")

        # 日誌文字區域
        self.textbox = ctk.CTkTextbox(
            self,
            font=("Consolas", FONT_SIZES["tiny"]),
            fg_color=COLORS["bg_input"],
            text_color=COLORS["text_secondary"],
            border_color=COLORS["border"],
            border_width=1,
            corner_radius=4,
            height=100,
            state="disabled",
            wrap="word",
        )
        self.textbox.pack(fill="both", expand=True, padx=PADDING["section"], pady=PADDING["inner"])

        # 設定 logging handler
        self.log_handler = TextboxLogHandler(self.textbox, log_viewer=self)
        self.log_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
        )

        # 預設展開，讓使用者能看到處理進度
        # （日誌面板位於最底部，會填滿剩餘空間）

    def get_handler(self):
        """取得 logging handler，供外部註冊"""
        return self.log_handler

    def toggle(self):
        """收合/展開"""
        self.collapsed = not self.collapsed
        if self.collapsed:
            self.textbox.pack_forget()
            self.toggle_label.configure(text="▶ 日誌")
        else:
            self.textbox.pack(fill="both", expand=True, padx=PADDING["section"], pady=PADDING["inner"])
            self.toggle_label.configure(text="▼ 日誌")

    def clear(self):
        """清除日誌"""
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.configure(state="disabled")
