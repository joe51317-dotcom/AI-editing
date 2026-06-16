"""
LosslessCut 互通 — CSV 匯出/匯入 + 啟動程式
CSV 格式：start_seconds,end_seconds,label（無標題行）
注意：CSV 是 flat segments，不保留 in-app 的「合併分組」狀態。
reload 後合併狀態會消失，UI 已明確告知。
"""
import os
import csv
import shutil
import subprocess
import logging

logger = logging.getLogger(__name__)


def export_segments_csv(video_path: str, parts: list) -> str:
    """
    將段落清單寫成 LosslessCut 相容的 CSV，放在影片旁邊。

    Args:
        video_path: 影片完整路徑
        parts: list[list[dict]]，每個 part 是 [{'start': float, 'end': float}, ...]

    Returns:
        CSV 檔案路徑
    """
    csv_path = os.path.splitext(video_path)[0] + ".llc.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for part_idx, part_segments in enumerate(parts, start=1):
            for seg in part_segments:
                writer.writerow([seg["start"], seg["end"], f"Part {part_idx}"])
    logger.info(f"已匯出 LosslessCut CSV: {csv_path}")
    return csv_path


def import_segments_csv(csv_path: str):
    """
    解析 LosslessCut CSV，回傳 list[list[dict]]（每行一個 part）。
    Returns None on parse failure.

    注意：CSV 是 flat rows，每行視為獨立 part（合併分組已遺失）。
    """
    try:
        parts = []
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row:
                    continue
                first = row[0].strip()
                if not first or first.startswith("#"):
                    continue
                if len(row) < 2:
                    continue
                try:
                    start = float(first)
                    end = float(row[1].strip())
                    if end > start:
                        parts.append([{"start": start, "end": end}])
                except ValueError:
                    continue
        if not parts:
            logger.warning(f"CSV 解析結果為空: {csv_path}")
            return None
        logger.info(f"已匯入 {len(parts)} 個段落從 {csv_path}")
        return parts
    except Exception as e:
        logger.error(f"匯入 CSV 失敗: {e}")
        return None


def launch_lossless_cut(video_path: str) -> bool:
    """
    啟動 LosslessCut，開啟指定影片。
    搜尋順序：config.LOSSLESSCUT_PATH → PATH → 常見安裝路徑

    Returns:
        True 若成功啟動，False 若找不到 LosslessCut
    """
    candidates = []

    try:
        import config
        if config.LOSSLESSCUT_PATH:
            candidates.append(config.LOSSLESSCUT_PATH)
    except ImportError:
        pass

    # PATH search
    for name in ("LosslessCut", "losslesscut", "LosslessCut.exe"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    # Common Windows install locations
    candidates += [
        r"C:\Program Files\LosslessCut\LosslessCut.exe",
        r"C:\Program Files (x86)\LosslessCut\LosslessCut.exe",
        os.path.join(os.path.expanduser("~"), "AppData", "Local", "Programs",
                     "LosslessCut", "LosslessCut.exe"),
    ]

    for exe in candidates:
        if exe and os.path.isfile(exe):
            try:
                subprocess.Popen([exe, video_path])
                logger.info(f"已啟動 LosslessCut: {exe}")
                return True
            except Exception as e:
                logger.warning(f"啟動失敗 ({exe}): {e}")

    logger.warning("找不到 LosslessCut 可執行檔")
    return False


def save_losslesscut_path(exe_path: str):
    """將使用者選擇的 LosslessCut 路徑存入設定，並更新執行時的 config 值。"""
    try:
        from gui.settings_store import load_settings, save_settings
        s = load_settings()
        s["losslesscut_path"] = exe_path
        save_settings(s)
    except Exception as e:
        logger.warning(f"儲存 LosslessCut 路徑失敗: {e}")
    try:
        import config
        config.LOSSLESSCUT_PATH = exe_path
    except ImportError:
        pass
