"""
進度面板 — 顯示每個影片的處理狀態與進度條
"""
import os
import sys
import time
import customtkinter as ctk

from gui.theme import COLORS, FONT_FAMILY, FONT_SIZES, PADDING, CORNER_RADIUS


class ProgressItem(ctk.CTkFrame):
    """單一影片進度列"""

    def __init__(self, master, filename, on_retry=None, **kwargs):
        super().__init__(master, fg_color=COLORS["bg_hover"], corner_radius=6, **kwargs)
        self.filename = filename
        self._on_retry = on_retry

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

        self.retry_btn = ctk.CTkButton(
            top,
            text="重試",
            width=50,
            height=20,
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            corner_radius=4,
            command=self._retry,
        )

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

    def _retry(self):
        """重試此影片"""
        if self._on_retry:
            self.retry_btn.pack_forget()
            self.set_status("等待重試...", COLORS["text_dim"])
            self.progress_bar.configure(progress_color=COLORS["accent"])
            self.progress_bar.set(0)
            self._on_retry(self.filename)

    def set_error(self, msg="失敗"):
        self.set_status(msg, COLORS["error"])
        self.progress_bar.configure(progress_color=COLORS["error"])
        if self._on_retry:
            self.retry_btn.pack(side="right", padx=(0, 4))


class ProgressPanel(ctk.CTkFrame):
    """處理進度面板"""

    def __init__(self, master, on_retry=None, **kwargs):
        super().__init__(master, fg_color=COLORS["bg_card"], corner_radius=CORNER_RADIUS, **kwargs)
        self.items = {}
        self._on_retry = on_retry
        self._timer_start = None
        self._timer_after_id = None

        # 標題列：進度 + 計時器
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=PADDING["section"], pady=(PADDING["inner"], 0))

        ctk.CTkLabel(
            header,
            text="進度",
            font=(FONT_FAMILY, FONT_SIZES["heading"], "bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left")

        self.timer_label = ctk.CTkLabel(
            header,
            text="",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_dim"],
        )
        self.timer_label.pack(side="right")

        # 進度清單（限制最大高度，避免擠壓日誌面板）
        self.list_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            height=60,
        )
        self.list_frame.pack(fill="x", padx=PADDING["section"], pady=PADDING["inner"])
        self.list_frame.configure(height=60)  # 最大高度約 1-2 個影片

        # 空白提示
        self.empty_label = ctk.CTkLabel(
            self.list_frame,
            text="尚未開始處理",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_dim"],
        )
        self.empty_label.pack(pady=8)

        # 完成通知列（預設隱藏）
        self.done_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.done_label = ctk.CTkLabel(
            self.done_frame,
            text="",
            font=(FONT_FAMILY, FONT_SIZES["body"], "bold"),
            text_color=COLORS["success"],
            anchor="w",
        )
        self.done_label.pack(side="left")

        self.open_folder_btn = ctk.CTkButton(
            self.done_frame,
            text="開啟資料夾",
            width=100,
            height=26,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            corner_radius=6,
            command=self._open_output_folder,
        )
        self.open_folder_btn.pack(side="right")
        self._output_dir = None

    def add_video(self, filename):
        """加入一個影片到進度清單"""
        if self.empty_label.winfo_ismapped():
            self.empty_label.pack_forget()

        item = ProgressItem(self.list_frame, filename, on_retry=self._on_retry)
        item.pack(fill="x", pady=2)
        self.items[filename] = item
        return item

    def get_item(self, filename):
        return self.items.get(filename)

    def show_done(self, count, output_dir=None):
        """顯示完成通知"""
        self._output_dir = output_dir
        text = f"  {count} 個影片處理完成"
        if output_dir:
            text += f"，儲存在 {output_dir}"
        self.done_label.configure(text=text)
        self.done_frame.pack(fill="x", padx=PADDING["section"], pady=(0, PADDING["inner"]))
        if output_dir:
            self.open_folder_btn.pack(side="right")
        else:
            self.open_folder_btn.pack_forget()

    def _open_output_folder(self):
        """開啟輸出資料夾"""
        if self._output_dir and os.path.isdir(self._output_dir):
            os.startfile(self._output_dir)

    def start_timer(self):
        """開始計時"""
        self._timer_start = time.time()
        self.timer_label.configure(text="00:00")
        self._tick_timer()

    def stop_timer(self):
        """停止計時（保留最終時間顯示）"""
        if self._timer_after_id:
            self.after_cancel(self._timer_after_id)
            self._timer_after_id = None

    def _tick_timer(self):
        """每秒更新計時器"""
        if self._timer_start is None:
            return
        elapsed = int(time.time() - self._timer_start)
        mins, secs = divmod(elapsed, 60)
        self.timer_label.configure(text=f"{mins:02d}:{secs:02d}")
        self._timer_after_id = self.after(1000, self._tick_timer)

    def clear(self):
        """清除所有進度"""
        self.stop_timer()
        self._timer_start = None
        self.timer_label.configure(text="")
        for item in self.items.values():
            item.destroy()
        self.items.clear()
        self.done_frame.pack_forget()
        self.empty_label.pack(pady=8)
