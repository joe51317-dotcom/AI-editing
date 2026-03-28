"""
設定持久化 — 自動儲存/載入使用者偏好設定
"""
import os
import json
import logging

logger = logging.getLogger(__name__)

SETTINGS_DIR = os.path.join(os.path.expanduser("~"), ".auto-process-gui")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")

DEFAULTS = {
    "output_dir": os.path.expanduser("~/Desktop"),
    "trim_mode": "auto",
    "naming_mode": "original",
    "custom_naming": "{filename}",
    "upload_enabled": True,
    "privacy_status": "不公開",
    "window_geometry": None,
    "intro_outro_enabled": False,
    "intro_path": None,
    "outro_path": None,
    "intro_duration": "3",
    "outro_duration": "3",
    "fade_duration": "0.5",
}


def load_settings():
    """載入設定，不存在則回傳預設值"""
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # 合併預設值（處理新增欄位）
            result = {**DEFAULTS, **saved}
            return result
    except Exception as e:
        logger.warning(f"載入設定失敗，使用預設值: {e}")
    return {**DEFAULTS}


def save_settings(settings):
    """儲存設定到 JSON"""
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"儲存設定失敗: {e}")
