"""
進度面板 — 橫向緊湊設計：總進度 header + 可折疊的逐影片詳情
"""
import os
import time
import customtkinter as ctk

from gui.theme import COLORS, FONT_FAMILY, FONT_FAMILY_MONO, FONT_SIZES, PADDING, CORNER_RADIUS


class ProgressItem(ctk.CTkFrame):
    """單一影片進度列"""

    def __init__(self, master, filename, on_retry=None, on_update=None, **kwargs):
        super().__init__(master, fg_color=COLORS["bg_hover"], corner_radius=6, **kwargs)
        self.filename = filename
        self._on_retry = on_retry
        self._on_update = on_update  # 通知 parent 重算 overall
        self._progress_value = 0.0
        self._done = False

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="x", padx=PADDING["small"], pady=3)

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
            fg_color=COLORS["progress_track"],
            progress_color=COLORS["accent"],
            height=5,
            corner_radius=3,
        )
        self.progress_bar.pack(fill="x", pady=(3, 0))
        self.progress_bar.set(0)

    def set_status(self, status, color=None):
        color = color or COLORS["text_secondary"]
        self.status_label.configure(text=status, text_color=color)

    def set_progress(self, value):
        self._progress_value = max(0.0, min(1.0, value))
        self.progress_bar.set(self._progress_value)
        if self._on_update:
            self._on_update()

    def set_done(self):
        self._done = True
        self._progress_value = 1.0
        self.set_status("完成", COLORS["success"])
        self.set_progress(1.0)
        self.progress_bar.configure(progress_color=COLORS["success"])

    def _retry(self):
        if self._on_retry:
            self.retry_btn.pack_forget()
            self.set_status("等待重試...", COLORS["text_dim"])
            self.progress_bar.configure(progress_color=COLORS["accent"])
            self._progress_value = 0.0
            self.progress_bar.set(0)
            self._done = False
            self._on_retry(self.filename)

    def set_error(self, msg="失敗"):
        self.set_status(msg, COLORS["error"])
        self.progress_bar.configure(progress_color=COLORS["error"])
        if self._on_retry:
            self.retry_btn.pack(side="right", padx=(0, 4))


class ProgressPanel(ctk.CTkFrame):
    """處理進度面板 — 橫向 header + 可折疊詳情列表"""

    def __init__(self, master, on_retry=None, **kwargs):
        super().__init__(master, fg_color=COLORS["bg_card"], corner_radius=CORNER_RADIUS, **kwargs)
        self.items = {}
        self._on_retry = on_retry
        self._timer_start = None
        self._timer_after_id = None
        self._list_visible = True
        self._output_dir = None

        # ── Header 列（永遠可見）─────────────────────
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=PADDING["section"], pady=(PADDING["inner"], 0))

        # 計時器
        self.timer_label = ctk.CTkLabel(
            header,
            text="",
            font=(FONT_FAMILY_MONO, FONT_SIZES["small"]),
            text_color=COLORS["text_dim"],
            width=45,
            anchor="w",
        )
        self.timer_label.pack(side="left")

        # 計數
        self.count_label = ctk.CTkLabel(
            header,
            text="尚未開始",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
        )
        self.count_label.pack(side="left", padx=(8, 0))

        # 展開/收合按鈕
        self.toggle_btn = ctk.CTkButton(
            header,
            text="詳情 ▾",
            width=64,
            height=22,
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            fg_color="transparent",
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_dim"],
            corner_radius=4,
            command=self._toggle_list,
        )
        self.toggle_btn.pack(side="right")

        # 總進度條（科技藍，居中自動拉伸）
        self.overall_bar = ctk.CTkProgressBar(
            header,
            fg_color=COLORS["progress_track"],
            progress_color=COLORS["progress"],
            height=6,
            corner_radius=3,
        )
        self.overall_bar.pack(side="right", fill="x", expand=True, padx=(10, 8))
        self.overall_bar.set(0)

        # ── 詳情清單（可折疊）────────────────────────
        self.list_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            height=100,
        )
        self.list_frame.pack(fill="x", padx=PADDING["section"], pady=PADDING["tiny"])

        self.empty_label = ctk.CTkLabel(
            self.list_frame,
            text="尚未開始處理",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_dim"],
        )
        self.empty_label.pack(pady=6)

        # ── 完成通知列（預設隱藏）───────────────────
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

    # ── 清單操作 ─────────────────────────────────────

    def add_video(self, filename):
        """加入一個影片到進度清單"""
        if self.empty_label.winfo_ismapped():
            self.empty_label.pack_forget()

        item = ProgressItem(
            self.list_frame,
            filename,
            on_retry=self._on_retry,
            on_update=self._recompute_overall,
        )
        item.pack(fill="x", pady=2)
        self.items[filename] = item
        self._recompute_overall()
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
        if self._output_dir and os.path.isdir(self._output_dir):
            os.startfile(self._output_dir)

    # ── 計時器 ───────────────────────────────────────

    def start_timer(self):
        self._timer_start = time.time()
        self.timer_label.configure(text="00:00")
        self._tick_timer()

    def stop_timer(self):
        if self._timer_after_id:
            self.after_cancel(self._timer_after_id)
            self._timer_after_id = None

    def _tick_timer(self):
        if self._timer_start is None:
            return
        elapsed = int(time.time() - self._timer_start)
        mins, secs = divmod(elapsed, 60)
        self.timer_label.configure(text=f"{mins:02d}:{secs:02d}")
        self._timer_after_id = self.after(1000, self._tick_timer)

    # ── Overall 進度計算 ──────────────────────────────

    def _recompute_overall(self):
        """聚合所有影片進度 → 更新 overall bar + count label"""
        total = len(self.items)
        if total == 0:
            self.overall_bar.set(0)
            self.count_label.configure(text="尚未開始")
            return

        done = sum(1 for it in self.items.values() if it._done)
        avg = sum(it._progress_value for it in self.items.values()) / total

        self.overall_bar.set(avg)
        self.count_label.configure(text=f"{done} / {total} 個影片")

    # ── 清單展開/收合 ─────────────────────────────────

    def _toggle_list(self):
        self._list_visible = not self._list_visible
        if self._list_visible:
            self.list_frame.pack(
                fill="x", padx=PADDING["section"], pady=PADDING["tiny"],
            )
            self.toggle_btn.configure(text="詳情 ▾")
        else:
            self.list_frame.pack_forget()
            self.toggle_btn.configure(text="詳情 ▸")

    # ── 重置 ─────────────────────────────────────────

    def clear(self):
        """清除所有進度"""
        self.stop_timer()
        self._timer_start = None
        self.timer_label.configure(text="")
        for item in self.items.values():
            item.destroy()
        self.items.clear()
        self.done_frame.pack_forget()
        self.empty_label.pack(pady=6)
        self.overall_bar.set(0)
        self.count_label.configure(text="尚未開始")
        if not self._list_visible:
            self._toggle_list()
