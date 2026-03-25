"""
課程影片處理工具 — GUI 入口
雙擊此檔案啟動應用程式。
"""
import sys
import io
import os
import logging
from logging.handlers import RotatingFileHandler

# UTF-8 編碼
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# 確保 auto-process/ 目錄在 Python 路徑中
app_dir = os.path.dirname(os.path.abspath(__file__))
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

# 設定 logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# 日誌寫入檔案（RotatingFileHandler）
_log_dir = os.path.join(os.path.expanduser("~"), ".auto-process-gui")
os.makedirs(_log_dir, exist_ok=True)
_file_handler = RotatingFileHandler(
    os.path.join(_log_dir, "app.log"),
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
logging.getLogger().addHandler(_file_handler)

logger = logging.getLogger(__name__)


def check_dependencies():
    """檢查 GUI 必要依賴"""
    missing = []

    try:
        import customtkinter
    except ImportError:
        missing.append("customtkinter")

    try:
        import PIL
    except ImportError:
        missing.append("Pillow")

    if missing:
        print(f"缺少依賴: {', '.join(missing)}")
        print(f"請執行: pip install {' '.join(missing)}")
        sys.exit(1)


def main():
    check_dependencies()

    # 嘗試使用 tkinterdnd2（拖放支援）
    try:
        from tkinterdnd2 import TkinterDnD
        import customtkinter as ctk

        # Patch customtkinter to use TkinterDnD
        class DnDCTk(ctk.CTk, TkinterDnD.DnDWrapper):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.TkdndVersion = TkinterDnD._require(self)

        # 替換 CTk 類別
        ctk.CTk = DnDCTk
        logger.info("拖放功能已啟用 (tkinterdnd2)")
    except ImportError:
        logger.info("tkinterdnd2 未安裝，拖放功能停用（可用瀏覽按鈕選擇檔案）")
    except Exception as e:
        logger.warning(f"拖放初始化失敗: {e}")

    # 啟動 GUI
    from gui.app import AutoProcessApp

    logger.info("啟動課程影片處理工具...")
    app = AutoProcessApp()

    # 設定視窗圖示（如果存在）
    icon_path = os.path.join(app_dir, "app_icon.ico")
    if os.path.exists(icon_path):
        try:
            app.iconbitmap(icon_path)
        except Exception:
            pass

    app.mainloop()


if __name__ == "__main__":
    main()
