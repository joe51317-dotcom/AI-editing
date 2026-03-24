"""
進度面板 — 顯示每個影片的處理狀態與進度條
"""
import customtkinter as ctk

from gui.theme import COLORS, FONT_FAMILY, FONT_SIZES, PADDING, CORNER_RADIUS


class ProgressItem(ctk.CTkFrame):
    """單一影片進度列"""

    def __init__(self, master, filename, **kwargs):
        super().__init__(master, fg_color=COLORS["bg_hover"], corner_radius=6, **kwargs)
        self.filename = filename

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="x", padx=PADDING["small"], pady=4)

        # 檔名 + 狀態
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")

        self.name_label = ctk.CTkLabel(
            top,
            text=filename,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        self.name_label.pack(side="left")

        self.status_label = ctk.CTkLabel(
            top,
            text="等待中",
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            text_color=COLORS["text_dim"],
            anchor="e",
        )
        self.status_label.pack(side="right")

        # 進度條
        self.progress_bar = ctk.CTkProgressBar(
            inner,
            fg_color=COLORS["bg_input"],
            progress_color=COLORS["accent"],
            height=6,
            corner_radius=3,
        )
        self.progress_bar.pack(fill="x", pady=(4, 0))
        self.progress_bar.set(0)

    def set_status(self, status, color=None):
        """更新狀態文字"""
        color = color or COLORS["text_secondary"]
        self.status_label.configure(text=status, text_color=color)

    def set_progress(self, value):
        """更新進度（0.0 ~ 1.0）"""
        self.progress_bar.set(max(0, min(1, value)))

    def set_done(self):
        self.set_status("完成", COLORS["success"])
        self.set_progress(1.0)
        self.progress_bar.configure(progress_color=COLORS["success"])

    def set_error(self, msg="失敗"):
        self.set_status(msg, COLORS["error"])
        self.progress_bar.configure(progress_color=COLORS["error"])


class ProgressPanel(ctk.CTkFrame):
    """處理進度面板"""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=COLORS["bg_card"], corner_radius=CORNER_RADIUS, **kwargs)
        self.items = {}

        # 標題
        ctk.CTkLabel(
            self,
            text="進度",
            font=(FONT_FAMILY, FONT_SIZES["heading"], "bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor="w", padx=PADDING["section"], pady=(PADDING["inner"], 0))

        # 進度清單
        self.list_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            height=80,
        )
        self.list_frame.pack(fill="x", padx=PADDING["section"], pady=PADDING["inner"])

        # 空白提示
        self.empty_label = ctk.CTkLabel(
            self.list_frame,
            text="尚未開始處理",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_dim"],
        )
        self.empty_label.pack(pady=8)

    def add_video(self, filename):
        """加入一個影片到進度清單"""
        if self.empty_label.winfo_ismapped():
            self.empty_label.pack_forget()

        item = ProgressItem(self.list_frame, filename)
        item.pack(fill="x", pady=2)
        self.items[filename] = item
        return item

    def get_item(self, filename):
        return self.items.get(filename)

    def clear(self):
        """清除所有進度"""
        for item in self.items.values():
            item.destroy()
        self.items.clear()
        self.empty_label.pack(pady=8)
