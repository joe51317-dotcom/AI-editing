"""
段落確認視窗 — 切段完成後、上傳前的「略過／合併／重編號」視窗
非阻塞設計：以 on_confirm / on_cancel callback 驅動。
"""
import os
import tempfile
import threading
import logging
import customtkinter as ctk
from PIL import Image

from gui.theme import COLORS, FONT_FAMILY, FONT_SIZES, PADDING

logger = logging.getLogger(__name__)

_THUMB_W = 96
_THUMB_H = 54
_ROW_PAD = 6


def _fmt_seconds(secs: float) -> str:
    """把秒數格式化成 HH:MM:SS"""
    secs = int(secs)
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _duration_label(secs: float) -> str:
    secs = int(secs)
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h:
        return f"{h}小時{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


class SegmentReviewDialog(ctk.CTkToplevel):
    """
    段落確認視窗。

    parts: list[list[dict]]  — 每個 part 是 [{'start': float, 'end': float}]
    on_confirm(segments: list[list[dict]]) — 使用者按確認後呼叫
    on_cancel() — 使用者取消後呼叫
    """

    def __init__(self, master, video_path: str, parts: list,
                 video_index: int, total_videos: int,
                 on_confirm, on_cancel, **kwargs):
        super().__init__(master, **kwargs)

        self._video_path = video_path
        self._parts = parts
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel

        self._rows = []       # list of row-state dicts
        self._thumb_temps = []  # temp jpg paths to clean up on close

        filename = os.path.basename(video_path)
        self.title(f"段落確認 — {filename}（第 {video_index}/{total_videos} 部）")
        self.geometry("720x520")
        self.minsize(580, 380)
        self.configure(fg_color=COLORS["bg_dark"])
        self.attributes("-topmost", True)
        self.lift()

        self._build_ui()
        self._update_episodes()
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        # 非同步抽取縮圖（不阻塞主線程）
        threading.Thread(target=self._extract_all_thumbnails, daemon=True).start()

    # ── 建立 UI ──────────────────────────────────────────────

    def _build_ui(self):
        # 說明文字
        info_bar = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=0)
        info_bar.pack(fill="x")
        ctk.CTkLabel(
            info_bar,
            text=f"共 {len(self._parts)} 個段落 — 確認哪些要匯出，可合併相鄰段落",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
        ).pack(side="left", padx=PADDING["section"], pady=8)

        # 可捲動清單
        self._scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["accent_dim"],
        )
        self._scroll.pack(fill="both", expand=True,
                          padx=PADDING["section"], pady=(PADDING["inner"], 0))
        self._scroll.grid_columnconfigure(0, weight=1)

        for i, part_segs in enumerate(self._parts):
            self._add_row(i, part_segs)

        # 底部操作列
        bottom = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=0)
        bottom.pack(fill="x", side="bottom")

        # 左側：LosslessCut 按鈕
        lc_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        lc_frame.pack(side="left", padx=PADDING["section"], pady=8)

        ctk.CTkButton(
            lc_frame,
            text="🎬 LosslessCut 微調",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_hover"],
            hover_color=COLORS["border"],
            text_color=COLORS["text_secondary"],
            height=30, corner_radius=6,
            command=self._open_losslesscut,
        ).pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            lc_frame,
            text="↻ 重載結果",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            fg_color=COLORS["bg_hover"],
            hover_color=COLORS["border"],
            text_color=COLORS["text_secondary"],
            height=30, corner_radius=6,
            command=self._reload_losslesscut,
        ).pack(side="left")

        # 右側：取消 / 確認
        btn_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        btn_frame.pack(side="right", padx=PADDING["section"], pady=8)

        ctk.CTkButton(
            btn_frame,
            text="取消",
            font=(FONT_FAMILY, FONT_SIZES["body"]),
            fg_color=COLORS["bg_hover"],
            hover_color=COLORS["error"],
            text_color=COLORS["text_primary"],
            height=36, corner_radius=8, width=80,
            command=self._cancel,
        ).pack(side="left", padx=(0, 8))

        self._confirm_btn = ctk.CTkButton(
            btn_frame,
            text="確認並繼續 ▶",
            font=(FONT_FAMILY, FONT_SIZES["body"], "bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["bg_dark"],
            height=36, corner_radius=8, width=130,
            command=self._confirm,
        )
        self._confirm_btn.pack(side="left")

    def _add_row(self, i: int, part_segs: list):
        """新增一列段落資訊"""
        start = part_segs[0]["start"]
        end = part_segs[-1]["end"]
        duration = sum(s["end"] - s["start"] for s in part_segs)

        row_frame = ctk.CTkFrame(
            self._scroll,
            fg_color=COLORS["bg_card"],
            corner_radius=8,
        )
        row_frame.grid(row=i, column=0, sticky="ew", pady=(0, _ROW_PAD))
        row_frame.grid_columnconfigure(2, weight=1)

        # 縮圖佔位（稍後填入）
        thumb_label = ctk.CTkLabel(
            row_frame,
            text="⏳",
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_dim"],
            width=_THUMB_W, height=_THUMB_H,
            fg_color=COLORS["bg_input"],
            corner_radius=4,
        )
        thumb_label.grid(row=0, column=0, rowspan=2, padx=(8, 8), pady=8)

        # Part 編號 + 時間範圍
        info_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
        info_frame.grid(row=0, column=1, sticky="w", pady=(8, 0))

        ctk.CTkLabel(
            info_frame,
            text=f"Part {i + 1}",
            font=(FONT_FAMILY, FONT_SIZES["body"], "bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left", padx=(0, 8))

        time_str = f"{_fmt_seconds(start)} ~ {_fmt_seconds(end)}"
        ctk.CTkLabel(
            info_frame,
            text=time_str,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
        ).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(
            info_frame,
            text=f"· {_duration_label(duration)}",
            font=(FONT_FAMILY, FONT_SIZES["tiny"]),
            text_color=COLORS["text_dim"],
        ).pack(side="left")

        # 控制列：匯出 checkbox + 合併按鈕 + 集數標籤
        ctrl_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
        ctrl_frame.grid(row=1, column=1, columnspan=2, sticky="w", pady=(0, 8))

        export_var = ctk.BooleanVar(value=True)
        merge_var = ctk.BooleanVar(value=False)

        ctk.CTkCheckBox(
            ctrl_frame,
            text="匯出",
            variable=export_var,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_color=COLORS["border"],
            checkmark_color=COLORS["bg_dark"],
            corner_radius=4,
            command=self._update_episodes,
        ).pack(side="left", padx=(0, 10))

        merge_btn = ctk.CTkCheckBox(
            ctrl_frame,
            text="合併入上段",
            variable=merge_var,
            font=(FONT_FAMILY, FONT_SIZES["small"]),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_color=COLORS["border"],
            checkmark_color=COLORS["bg_dark"],
            corner_radius=4,
            command=self._update_episodes,
        )
        merge_btn.pack(side="left", padx=(0, 10))
        # 第一個 part 不可合併
        if i == 0:
            merge_btn.configure(state="disabled")

        episode_label = ctk.CTkLabel(
            ctrl_frame,
            text="→ 第 1 集",
            font=(FONT_FAMILY, FONT_SIZES["small"], "bold"),
            text_color=COLORS["accent"],
        )
        episode_label.pack(side="left")

        self._rows.append({
            "export_var": export_var,
            "merge_var": merge_var,
            "merge_btn": merge_btn,
            "episode_label": episode_label,
            "thumb_label": thumb_label,
        })

    # ── 集數計算 ──────────────────────────────────────────────

    def _compute_episodes(self):
        """
        計算每個 part 的輸出狀態。
        Returns list of ("episode", ep) | ("merged", host_ep) | ("skip", None)
        """
        result = []
        ep = 0
        current_host_ep = None

        for row in self._rows:
            exported = row["export_var"].get()
            merged = row["merge_var"].get()

            if not exported:
                result.append(("skip", None))
                continue

            if merged and current_host_ep is not None:
                result.append(("merged", current_host_ep))
            else:
                ep += 1
                current_host_ep = ep
                result.append(("episode", ep))

        return result

    def _update_episodes(self):
        """重算並更新所有列的集數標籤，同時更新確認按鈕狀態"""
        episodes = self._compute_episodes()
        has_export = any(t == "episode" for t, _ in episodes)

        for i, (ep_type, ep_num) in enumerate(episodes):
            lbl = self._rows[i]["episode_label"]
            if ep_type == "episode":
                lbl.configure(text=f"→ 第 {ep_num} 集", text_color=COLORS["accent"])
            elif ep_type == "merged":
                lbl.configure(text=f"合併入第 {ep_num} 集", text_color=COLORS["text_secondary"])
            else:
                lbl.configure(text="已略過", text_color=COLORS["text_dim"])

            # 若前面沒有可合併的 part，禁用合併 checkbox
            row = self._rows[i]
            if i > 0:
                prev_has_host = any(
                    self._rows[j]["export_var"].get()
                    for j in range(i)
                )
                if prev_has_host:
                    row["merge_btn"].configure(state="normal")
                else:
                    row["merge_var"].set(False)
                    row["merge_btn"].configure(state="disabled")

        # 沒有任何匯出時，確認按鈕變灰
        if has_export:
            self._confirm_btn.configure(state="normal", text="確認並繼續 ▶")
        else:
            self._confirm_btn.configure(
                state="disabled", text="至少保留一段才能繼續")

    # ── 縮圖 ──────────────────────────────────────────────────

    def _extract_all_thumbnails(self):
        """在背景執行緒依序抽取每個 part 的縮圖"""
        from gui.thumbnail import extract_frame

        tmp_dir = tempfile.mkdtemp(prefix="aiedit_thumbs_")
        self._thumb_temps.append(tmp_dir)

        for i, part_segs in enumerate(self._parts):
            if not self.winfo_exists():
                break

            start = part_segs[0]["start"]
            end = part_segs[-1]["end"]
            mid = (start + end) / 2

            out_path = os.path.join(tmp_dir, f"thumb_{i}.jpg")
            ok = extract_frame(self._video_path, mid, out_path)

            if ok:
                self.after(0, lambda idx=i, p=out_path: self._set_thumbnail(idx, p))

    def _set_thumbnail(self, idx: int, img_path: str):
        """在主執行緒更新縮圖 label（避免跨執行緒操作 Tk widget）"""
        if not self.winfo_exists():
            return
        row = self._rows[idx]
        lbl = row.get("thumb_label")
        if not lbl or not lbl.winfo_exists():
            return
        try:
            pil_img = Image.open(img_path)
            pil_img.thumbnail((_THUMB_W, _THUMB_H), Image.LANCZOS)
            ctk_img = ctk.CTkImage(pil_img, size=(_THUMB_W, _THUMB_H))
            lbl.configure(image=ctk_img, text="")
            lbl._ctk_image = ctk_img  # keep reference
        except Exception as e:
            logger.warning(f"設定縮圖失敗 Part {idx + 1}: {e}")

    # ── LosslessCut 互通 ─────────────────────────────────────

    def _open_losslesscut(self):
        """匯出 CSV 並啟動 LosslessCut；找不到則引導使用者選擇路徑"""
        from lossless_cut_io import export_segments_csv, launch_lossless_cut, save_losslesscut_path
        from tkinter import filedialog, messagebox

        current_parts = self._collect_current_parts()
        csv_path = export_segments_csv(self._video_path, current_parts)
        logger.info(f"LosslessCut CSV: {csv_path}")

        ok = launch_lossless_cut(self._video_path)
        if not ok:
            ans = messagebox.askyesno(
                "找不到 LosslessCut",
                f"找不到 LosslessCut 可執行檔。\n"
                f"CSV 已匯出至：\n{csv_path}\n\n"
                f"要手動選擇 LosslessCut.exe 嗎？"
            )
            if ans:
                exe = filedialog.askopenfilename(
                    title="選擇 LosslessCut.exe",
                    filetypes=[("執行檔", "*.exe"), ("所有檔案", "*.*")]
                )
                if exe:
                    save_losslesscut_path(exe)
                    launch_lossless_cut(self._video_path)
        else:
            from tkinter import messagebox as mb
            mb.showinfo(
                "LosslessCut 已啟動",
                f"CSV 已匯出至：\n{csv_path}\n\n"
                f"請在 LosslessCut 中手動匯入此 CSV（File → Load Segments from CSV），\n"
                f"精修後 Export CSV，再按「↻ 重載結果」。\n\n"
                f"⚠ 注意：重載後，in-app 的「合併」設定會被重設。"
            )

    def _reload_losslesscut(self):
        """從影片旁的 CSV 重載 LosslessCut 結果"""
        from lossless_cut_io import import_segments_csv
        from tkinter import messagebox, filedialog

        # 先找影片旁的 CSV
        default_csv = os.path.splitext(self._video_path)[0] + ".llc.csv"
        if not os.path.isfile(default_csv):
            csv_path = filedialog.askopenfilename(
                title="選擇 LosslessCut CSV",
                initialdir=os.path.dirname(self._video_path),
                filetypes=[("CSV 檔案", "*.csv"), ("所有檔案", "*.*")]
            )
            if not csv_path:
                return
        else:
            csv_path = default_csv

        new_parts = import_segments_csv(csv_path)
        if not new_parts:
            messagebox.showerror("重載失敗", f"無法解析 CSV：\n{csv_path}")
            return

        # 重建 parts 並重繪列
        self._parts = new_parts
        self._rebuild_rows()
        messagebox.showinfo(
            "已重載",
            f"已從 CSV 重載 {len(new_parts)} 個段落。\n"
            f"⚠ 合併狀態已重設（CSV 不保留合併分組）。"
        )

    def _rebuild_rows(self):
        """清除並重建所有列（用於 reload 後）"""
        for widget in self._scroll.winfo_children():
            widget.destroy()
        self._rows.clear()
        self._thumb_temps.clear()

        for i, part_segs in enumerate(self._parts):
            self._add_row(i, part_segs)

        self._update_episodes()
        threading.Thread(target=self._extract_all_thumbnails, daemon=True).start()

    # ── 計算最終輸出 segments ─────────────────────────────────

    def _collect_current_parts(self) -> list:
        """回傳目前勾選狀態下的所有 parts（包含略過的），供 CSV 匯出用"""
        return self._parts

    def compute_segments(self) -> list:
        """
        依目前勾選/合併狀態，計算最終要傳給 ProcessWorker 的 list[list[dict]]。
        略過的 part 被移除，合併的 part 合入前一個 part 的 segments。
        """
        episodes_info = self._compute_episodes()

        # group_map: ep_num → list[dict] (merged segments)
        group_map = {}

        for i, (ep_type, ep_num) in enumerate(episodes_info):
            if ep_type == "skip":
                continue
            elif ep_type == "episode":
                group_map[ep_num] = list(self._parts[i])
            elif ep_type == "merged":
                group_map[ep_num].extend(self._parts[i])

        if not group_map:
            return []

        max_ep = max(group_map.keys())
        return [group_map[ep] for ep in range(1, max_ep + 1) if ep in group_map]

    # ── 動作 ────────────────────────────────────────────────

    def _confirm(self):
        segments = self.compute_segments()
        if not segments:
            return  # button should be disabled anyway
        self._cleanup()
        self.destroy()
        self._on_confirm(segments)

    def _cancel(self):
        self._cleanup()
        self.destroy()
        self._on_cancel()

    def _cleanup(self):
        """清理縮圖暫存目錄"""
        import shutil
        for d in self._thumb_temps:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
        self._thumb_temps.clear()
