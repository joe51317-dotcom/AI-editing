"""
影片選擇面板 — 拖放區域 + 影片清單 + 命名規則
"""
import os
import customtkinter as ctk
from tkinter import filedialog

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

        ctk.CTkLabel(
            info_frame,
            text=f"{filename}  ({size_mb:.0f} MB)",
            font=(FONT_FAMILY, FONT_SIZES["body"]),
            text_color=COLORS["text_primary"],
            anchor="w",
        ).pack(side="top", anchor="w")

        # 標題編輯
        title_frame = ctk.CTkFrame(info_frame, fg_color="transparent")
        title_frame.pack(side="top", anchor="w", fill="x", pady=(2, 0))

        ctk.CTkLabel(
            title_frame,
            text="標題:",
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
            height=26,
            corner_radius=4,
        )
        self.title_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # 移除按鈕
        ctk.CTkButton(
            self,
            text="✕",
            width=30,
            height=30,
            font=(FONT_FAMILY, FONT_SIZES["body"]),
            fg_color="transparent",
            hover_color=COLORS["error"],
            text_color=COLORS["text_secondary"],
            command=self._remove,
        ).pack(side="right", padx=PADDING["small"])

    def _remove(self):
        self.on_remove(self)

    def get_title(self):
        return self.title_var.get().strip()


class VideoPanel(ctk.CTkFrame):
    """影片選擇與管理面板"""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=COLORS["bg_card"], corner_radius=CORNER_RADIUS, **kwargs)
        self.video_items = []

        # 標題
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=PADDING["section"], pady=(PADDING["inner"], 0))

        ctk.CTkLabel(
            header,
            text="影片",
            font=(FONT_FAMILY, FONT_SIZES["heading"], "bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left")

        ctk.CTkButton(
            header,
            text="瀏覽檔案",
            width=90,
            height=28,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            corner_radius=6,
            command=self._browse_files,
        ).pack(side="right")

        # 拖放區域
        self.drop_area = ctk.CTkFrame(
            self,
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            border_width=2,
            corner_radius=CORNER_RADIUS,
            height=80,
        )
        self.drop_area.pack(fill="x", padx=PADDING["section"], pady=PADDING["inner"])
        self.drop_area.pack_propagate(False)

        self.drop_label = ctk.CTkLabel(
            self.drop_area,
            text="拖放影片檔案到此處",
            font=(FONT_FAMILY, FONT_SIZES["body"]),
            text_color=COLORS["text_dim"],
        )
        self.drop_label.place(relx=0.5, rely=0.5, anchor="center")

        # 影片清單（可捲動）
        self.list_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            height=0,
        )
        self.list_frame.pack(fill="x", padx=PADDING["section"], pady=(0, PADDING["inner"]))

        # 命名規則
        naming_frame = ctk.CTkFrame(self, fg_color="transparent")
        naming_frame.pack(fill="x", padx=PADDING["section"], pady=(0, PADDING["inner"]))

        ctk.CTkLabel(
            naming_frame,
            text="命名規則:",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
        ).pack(side="left")

        self.naming_var = ctk.StringVar(value="{filename}")
        ctk.CTkEntry(
            naming_frame,
            textvariable=self.naming_var,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_input"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            width=200,
            height=26,
            corner_radius=4,
        ).pack(side="left", padx=(4, 8))

        ctk.CTkLabel(
            naming_frame,
            text="可用: {filename} {date} {index} {part}",
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            text_color=COLORS["text_dim"],
        ).pack(side="left")

    def setup_dnd(self, root):
        """設定拖放功能（需要 tkinterdnd2）"""
        try:
            self.drop_area.drop_target_register("DND_Files")
            self.drop_area.dnd_bind("<<Drop>>", self._on_drop)
            self.drop_area.dnd_bind("<<DragEnter>>", self._on_drag_enter)
            self.drop_area.dnd_bind("<<DragLeave>>", self._on_drag_leave)
        except Exception:
            # tkinterdnd2 不可用時靜默略過
            pass

    def _on_drop(self, event):
        """處理拖放事件"""
        self.drop_area.configure(border_color=COLORS["border"])
        files = self._parse_drop_data(event.data)
        for f in files:
            self.add_video(f)

    def _on_drag_enter(self, event):
        self.drop_area.configure(border_color=COLORS["accent"])

    def _on_drag_leave(self, event):
        self.drop_area.configure(border_color=COLORS["border"])

    def _parse_drop_data(self, data):
        """解析拖放資料（處理 Windows 的大括號路徑格式）"""
        files = []
        # Windows 拖放可能用大括號包裹含空格路徑
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
        """開啟檔案選擇對話框"""
        filetypes = [
            ("影片檔案", " ".join(f"*{ext}" for ext in VIDEO_EXTENSIONS)),
            ("所有檔案", "*.*"),
        ]
        paths = filedialog.askopenfilenames(filetypes=filetypes)
        for p in paths:
            self.add_video(p)

    def add_video(self, video_path):
        """加入影片到清單"""
        ext = os.path.splitext(video_path)[1].lower()
        if ext not in VIDEO_EXTENSIONS:
            return

        # 避免重複
        for item in self.video_items:
            if item.video_path == video_path:
                return

        item = VideoItem(
            self.list_frame,
            video_path,
            on_remove=self._remove_video,
        )
        item.pack(fill="x", pady=2)
        self.video_items.append(item)
        self._update_drop_label()

    def _remove_video(self, item):
        """從清單移除影片"""
        item.pack_forget()
        item.destroy()
        self.video_items.remove(item)
        self._update_drop_label()

    def _update_drop_label(self):
        """更新拖放區文字"""
        if self.video_items:
            self.drop_label.configure(text=f"已加入 {len(self.video_items)} 個影片（可繼續拖放）")
        else:
            self.drop_label.configure(text="拖放影片檔案到此處")

    def get_videos(self):
        """取得所有影片路徑與標題"""
        return [
            {"path": item.video_path, "title": item.get_title()}
            for item in self.video_items
        ]

    def get_naming_rule(self):
        return self.naming_var.get()

    def clear_all(self):
        """清空所有影片"""
        for item in list(self.video_items):
            self._remove_video(item)
