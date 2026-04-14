import sys
import os
from dotenv import load_dotenv, find_dotenv

# PyInstaller 打包後，exe 所在目錄作為基準
if getattr(sys, "frozen", False):
    # 打包後: exe 所在目錄
    _exe_dir = os.path.dirname(sys.executable)
    _env_file = os.path.join(_exe_dir, ".env")
    if os.path.exists(_env_file):
        load_dotenv(_env_file)
    PROJECT_ROOT = _exe_dir
else:
    load_dotenv(find_dotenv())
    # 專案根目錄（.env 所在位置）
    PROJECT_ROOT = os.path.dirname(os.path.abspath(find_dotenv())) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve(path):
    """將相對路徑轉為絕對路徑（相對於 PROJECT_ROOT）"""
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(PROJECT_ROOT, path))


# --- 靜音偵測 ---
SILENCE_NOISE_DB = int(os.getenv("SILENCE_NOISE_DB", "-30"))
SPEECH_THRESHOLD_DB = int(os.getenv("SPEECH_THRESHOLD_DB", "-20"))
SILENCE_MIN_DURATION = float(os.getenv("SILENCE_MIN_DURATION", "10"))
BREAK_THRESHOLD_SECONDS = float(os.getenv("BREAK_THRESHOLD_SECONDS", "300"))

# --- YouTube ---
YOUTUBE_CLIENT_SECRET = _resolve(os.getenv("YOUTUBE_CLIENT_SECRET", "./credentials/client_secret.json"))
YOUTUBE_TOKEN_PATH = _resolve(os.getenv("YOUTUBE_TOKEN_PATH", "./credentials/token.json"))
YOUTUBE_DEFAULT_PRIVACY = os.getenv("YOUTUBE_DEFAULT_PRIVACY", "unlisted")
YOUTUBE_DEFAULT_CATEGORY = os.getenv("YOUTUBE_DEFAULT_CATEGORY", "27")

# --- 工作資料夾 ---
INBOX_DIR = _resolve(os.getenv("INBOX_DIR", "./auto-process/inbox"))
PROCESSING_DIR = _resolve(os.getenv("PROCESSING_DIR", "./auto-process/processing"))
DONE_DIR = _resolve(os.getenv("DONE_DIR", "./auto-process/done"))
FAILED_DIR = _resolve(os.getenv("FAILED_DIR", "./auto-process/failed"))
LOG_DIR = _resolve(os.getenv("LOG_DIR", "./auto-process/logs"))

# --- 應用程式 ---
APP_VERSION = "1.27"

# --- 支援的影片格式 ---
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".mts", ".m4v"}
