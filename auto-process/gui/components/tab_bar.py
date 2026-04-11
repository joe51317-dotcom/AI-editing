"""
TabBar — 琥珀色底線分頁切換列
"""
import customtkinter as ctk
from gui.theme import COLORS, FONT_FAMILY, FONT_SIZES


class TabBar(ctk.CTkFrame):
    """
    自訂分頁列：底線 indicator + active tint 背景。

    on_change(index: int) — 使用者點擊非 active 分頁時回呼。
    """

    def __init__(self, master, tabs: list, on_change, **kw):
        super().__init__(master, fg_color=COLORS["bg_card"], corner_radius=0, **kw)
        self._on_change = on_change
        self._active = 0
        self._buttons = []

        n = len(tabs)
        for i, label in enumerate(tabs):
            self.grid_columnconfigure(i, weight=1)
            btn = ctk.CTkButton(
                self,
                text=label,
                fg_color="transparent",
                hover_color=COLORS["bg_hover"],
                text_color=COLORS["text_secondary"],
                font=(FONT_FAMILY, FONT_SIZES["body"], "bold"),
                height=38,
                corner_radius=0,
                command=lambda idx=i: self.select(idx),
            )
            btn.grid(row=0, column=i, sticky="ew", padx=0)
            self._buttons.append(btn)

        # 底線 indicator（3px 琥珀色）
        self.grid_rowconfigure(1, minsize=3)
        self._indicator = ctk.CTkFrame(
            self,
            fg_color=COLORS["accent"],
            height=3,
            corner_radius=0,
        )

        # 分隔細線
        ctk.CTkFrame(
            self,
            fg_color=COLORS["border"],
            height=1,
            corner_radius=0,
        ).grid(row=2, column=0, columnspan=n, sticky="ew")

        self._paint()

    # ── Public ────────────────────────────────────────

    @property
    def active(self) -> int:
        return self._active

    def select(self, idx: int):
        """程式碼切換分頁（不觸發 on_change）"""
        if idx == self._active:
            return
        self._active = idx
        self._paint()
        self._on_change(idx)

    def set_active(self, idx: int):
        """靜默設定 active（不觸發 on_change）"""
        if 0 <= idx < len(self._buttons):
            self._active = idx
            self._paint()

    # ── Internal ─────────────────────────────────────

    def _paint(self):
        for i, btn in enumerate(self._buttons):
            active = (i == self._active)
            btn.configure(
                text_color=COLORS["accent"] if active else COLORS["text_secondary"],
                fg_color=COLORS["accent_soft"] if active else "transparent",
            )
        # 將 indicator 移到 active 欄（re-grid 會自動移動 widget）
        self._indicator.grid(row=1, column=self._active, sticky="ew")
