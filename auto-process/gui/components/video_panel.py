"""
影片選擇面板 — 拖放區域 + 影片清單 + 友善命名選擇
"""
import os
import threading
import customtkinter as ctk
from tkinter import filedialog, messagebox

from gui.theme import COLORS, FONT_FAMILY, FONT_SIZES, PADDING, CORNER_RADIUS

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".mts", ".m4v"}


class VideoItem(ctk.CTkFrame):
    """單一影片列表項目"""

    def __init__(self, master, video_path, on_remove, **kwargs):
        super().__init__(master, fg_color=COLORS["bg_hover"], corner_radius=6, **kwargs)
        self.video_path = video_path
        self.on_remove = on_remove

        filename = os.path.basename(video_path)
        size_mb = os.path.getsize(video_path) / (1024 * 1024)
        name_no_ext = os.path.splitext(filename)[0]

        # 檔案資訊
        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.pack(side="left", fill="x", expand=True, padx=PADDING["small"], pady=4)

        self.info_label = ctk.CTkLabel(
            info_frame,
            text=f"{filename}  ({size_mb:.0f} MB)",
            font=(FONT_FAMILY, FONT_SIZES["body"]),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        self.info_label.pack(side="top", anchor="w")

        # 背景取得影片時長
        self._size_mb = size_mb
        self._filename = filename
        threading.Thread(target=self._probe_duration, daemon=True).start()

        # 標題編輯
        title_frame = ctk.CTkFrame(info_frame, fg_color="transparent")
        title_frame.pack(side="top", anchor="w", fill="x", pady=(2, 0))

        ctk.CTkLabel(
            title_frame,
            text="YT 標題:",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
        ).pack(side="left")

        self.title_var = ctk.StringVar(value=name_no_ext)
        self.title_entry = ctk.CTkEntry(
            title_frame,
            textvariable=self.title_var,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            height=24,
            corner_radius=4,
        )
        self.title_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # 移除按鈕
        ctk.CTkButton(
            self,
            text="✕",
            width=28,
            height=28,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color="transparent",
            hover_color=COLORS["error"],
            text_color=COLORS["text_dim"],
            command=self._remove,
        ).pack(side="right", padx=PADDING["small"])

    def _probe_duration(self):
        """背景用 ffprobe 取得影片時長"""
        try:
            from silence_detector import get_video_duration
            dur = get_video_duration(self.video_path)
            mins, secs = divmod(int(dur), 60)
            hours, mins = divmod(mins, 60)
            if hours > 0:
                dur_text = f"{hours}:{mins:02d}:{secs:02d}"
            else:
                dur_text = f"{mins}:{secs:02d}"
            self.after(0, lambda: self.info_label.configure(
                text=f"{self._filename}  ({self._size_mb:.0f} MB · {dur_text})"
            ))
        except Exception:
            pass

    def _remove(self):
        self.on_remove(self)

    def get_title(self):
        return self.title_var.get().strip()


class NamingRulePanel(ctk.CTkFrame):
    """使用者友善的輸出命名選擇"""

    MODES = {
        "original": ("{filename}", "原始檔名"),
        "date":     ("{date}_{filename}", "日期 + 檔名"),
        "custom":   (None, "自訂格式"),
    }

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.naming_mode = ctk.StringVar(value="original")

        # 標題
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", pady=(4, 2))
        ctk.CTkLabel(
            header,
            text="輸出命名",
            font=(FONT_FAMILY, FONT_SIZES["small"], "bold"),
            text_color=COLORS["text_secondary"],
            anchor="w",
        ).pack(side="left")

        # 三個 RadioButton 選項
        options_frame = ctk.CTkFrame(self, fg_color="transparent")
        options_frame.pack(fill="x")

        for key, (_, label) in self.MODES.items():
            ctk.CTkRadioButton(
                options_frame,
                text=label,
                variable=self.naming_mode,
                value=key,
                font=(FONT_FAMILY, FONT_SIZES["small"]),
                text_color=COLORS["text_primary"],
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
                border_color=COLORS["border"],
                command=self._on_mode_change,
            ).pack(anchor="w", pady=1)

        # 自訂格式輸入框（預設隱藏）
        self.custom_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.custom_var = ctk.StringVar(value="{filename}")
        self.custom_entry = ctk.CTkEntry(
            self.custom_frame,
            textvariable=self.custom_var,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            placeholder_text="{filename}_{date}_{index}",
            height=26,
            corner_radius=4,
        )
        self.custom_entry.pack(fill="x")

        # 可點擊的變數標籤列
        vars_row = ctk.CTkFrame(self.custom_frame, fg_color="transparent")
        vars_row.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(
            vars_row,
            text="插入：",
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            text_color=COLORS["text_dim"],
        ).pack(side="left")

        for label, token in [("檔名", "{filename}"), ("日期", "{date}"), ("編號", "{index}")]:
            ctk.CTkButton(
                vars_row,
                text=label,
                font=(FONT_FAMILY, FONT_SIZES["tiny"]),
                fg_color="transparent",
                hover_color=COLORS["bg_hover"],
                text_color=COLORS["accent"],
                width=0,
                height=20,
                corner_radius=4,
                command=lambda v=token: self._insert_var(v),
            ).pack(side="left", padx=1)

        # 預覽
        self.preview_label = ctk.CTkLabel(
            self,
            text="範例：課程影片-1.mp4",
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            text_color=COLORS["text_dim"],
            anchor="w",
        )
        self.preview_label.pack(fill="x", pady=(3, 0))

        # 段落提示
        ctk.CTkLabel(
            self,
            text="多段影片自動加上 -1、-2 後綴",
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            text_color=COLORS["text_dim"],
            anchor="w",
        ).pack(fill="x", pady=(1, 0))

        self._on_mode_change()

    def _insert_var(self, var_text):
        """在游標位置插入變數"""
        try:
            pos = self.custom_entry.index("insert")
            current = self.custom_var.get()
            new_val = current[:pos] + var_text + current[pos:]
            self.custom_var.set(new_val)
            self.custom_entry.icursor(pos + len(var_text))
            self.custom_entry.focus_set()
        except Exception:
            # fallback: 直接附加到尾端
            self.custom_var.set(self.custom_var.get() + var_text)
        self._update_preview()

    def _on_mode_change(self):
        mode = self.naming_mode.get()
        if mode == "custom":
            self.custom_frame.pack(fill="x", pady=(4, 0))
        else:
            self.custom_frame.pack_forget()
        self._update_preview()

    def _update_preview(self):
        mode = self.naming_mode.get()
        if mode == "original":
            base = "課程影片"
        elif mode == "date":
            base = "20260325_課程影片"
        else:
            val = self.custom_var.get() or "{filename}"
            base = val.replace("{filename}", "課程影片").replace("{date}", "20260325").replace("{index}", "1")
        example = f"{base}-1.mp4"
        self.preview_label.configure(text=f"範例：{example}")

    def get_naming_rule(self):
        mode = self.naming_mode.get()
        if mode == "custom":
            return self.custom_var.get() or "{filename}"
        return self.MODES[mode][0]

    def get_state(self):
        """取得命名規則狀態"""
        return {
            "naming_mode": self.naming_mode.get(),
            "custom_naming": self.custom_var.get(),
        }

    def set_state(self, state):
        """恢復命名規則狀態"""
        if "naming_mode" in state:
            mode = state["naming_mode"]
            if mode in self.MODES:
                self.naming_mode.set(mode)
        if "custom_naming" in state:
            self.custom_var.set(state["custom_naming"])
        self._on_mode_change()


class VideoPanel(ctk.CTkFrame):
    """影片選擇與管理面板（填滿左欄高度）"""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=COLORS["bg_card"], corner_radius=CORNER_RADIUS, **kwargs)
        self.video_items = []

        # 標題列
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=PADDING["section"], pady=(PADDING["inner"], 4))

        ctk.CTkLabel(
            header,
            text="影片",
            font=(FONT_FAMILY, FONT_SIZES["heading"], "bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left")

        ctk.CTkButton(
            header,
            text="+ 瀏覽檔案",
            width=100,
            height=26,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            corner_radius=6,
            command=self._browse_files,
        ).pack(side="right")

        self.clear_all_btn = ctk.CTkButton(
            header,
            text="清除全部",
            width=80,
            height=26,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color="transparent",
            hover_color=COLORS["error"],
            text_color=COLORS["text_secondary"],
            corner_radius=6,
            command=self.clear_all,
        )
        self.clear_all_btn.pack(side="right", padx=(0, 6))

        # 拖放提示（無邊框，融入背景）
        self.drop_hint = ctk.CTkLabel(
            self,
            text="拖放影片到這裡",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_dim"],
        )
        self.drop_hint.pack(pady=(0, 4))

        # 影片清單（填滿剩餘空間）
        self.list_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
        )
        self.list_frame.pack(fill="both", expand=True, padx=PADDING["section"])

        # 分隔線
        ctk.CTkFrame(self, fg_color=COLORS["border_subtle"], height=1, corner_radius=0).pack(
            fill="x", padx=PADDING["section"], pady=(4, 0)
        )

        # 命名規則（底部固定）
        self.naming_panel = NamingRulePanel(self)
        self.naming_panel.pack(fill="x", padx=PADDING["section"], pady=(6, PADDING["inner"]))

    def setup_dnd(self, root):
        """設定拖放功能（整個左欄都可拖放）"""
        targets = [self, self.list_frame]
        # CTkScrollableFrame 內部有 canvas，也一起綁定
        try:
            targets.append(self.list_frame._parent_canvas)
        except AttributeError:
            pass
        for widget in targets:
            try:
                widget.drop_target_register("DND_Files")
                widget.dnd_bind("<<Drop>>", self._on_drop)
                widget.dnd_bind("<<DragEnter>>", self._on_drag_enter)
                widget.dnd_bind("<<DragLeave>>", self._on_drag_leave)
            except Exception:
                pass

    def _on_drop(self, event):
        self.configure(fg_color=COLORS["bg_card"])
        files = self._parse_drop_data(event.data)
        for f in files:
            self.add_video(f)

    def _on_drag_enter(self, event):
        self.configure(fg_color=COLORS["bg_elevated"])

    def _on_drag_leave(self, event):
        self.configure(fg_color=COLORS["bg_card"])

    def _parse_drop_data(self, data):
        files = []
        if "{" in data:
            import re
            files = re.findall(r"\{([^}]+)\}", data)
            remainder = re.sub(r"\{[^}]+\}", "", data).strip()
            if remainder:
                files.extend(remainder.split())
        else:
            files = data.split()
        return [f for f in files if os.path.isfile(f)]

    def _browse_files(self):
        filetypes = [
            ("影片檔案", " ".join(f"*{ext}" for ext in VIDEO_EXTENSIONS)),
            ("所有檔案", "*.*"),
        ]
        paths = filedialog.askopenfilenames(filetypes=filetypes)
        for p in paths:
            self.add_video(p)

    def add_video(self, video_path):
        ext = os.path.splitext(video_path)[1].lower()
        if ext not in VIDEO_EXTENSIONS:
            return
        for item in self.video_items:
            if item.video_path == video_path:
                return
        item = VideoItem(self.list_frame, video_path, on_remove=self._remove_video)
        item.pack(fill="x", pady=2)
        self.video_items.append(item)
        self._update_drop_label()

    def _remove_video(self, item):
        item.pack_forget()
        item.destroy()
        self.video_items.remove(item)
        self._update_drop_label()

    def _update_drop_label(self):
        if self.video_items:
            self.drop_hint.configure(text=f"已加入 {len(self.video_items)} 個影片，可繼續拖放")
        else:
            self.drop_hint.configure(text="拖放影片到這裡")

    def get_videos(self):
        return [
            {"path": item.video_path, "title": item.get_title()}
            for item in self.video_items
        ]

    def get_naming_rule(self):
        return self.naming_panel.get_naming_rule()

    def clear_all(self):
        if not self.video_items:
            return
        if not messagebox.askyesno("清除全部", f"確定要移除全部 {len(self.video_items)} 個影片？"):
            return
        for item in list(self.video_items):
            self._remove_video(item)
