"""
GUI 主題設定 — 精緻深色主題 + 琥珀色重點色
"""

# 配色（深藍黑調，比純灰更有質感）
COLORS = {
    "bg_dark": "#0f1117",       # 主背景：深藍黑
    "bg_card": "#181b25",       # 卡片：略帶藍調
    "bg_hover": "#1e2235",      # 懸停：可見但不突兀
    "bg_input": "#141720",      # 輸入框：最深
    "border": "#2a2e45",        # 邊框：低調藍灰
    "border_subtle": "#1e2235", # 超低調邊框
    "accent": "#f59f00",        # 重點色：琥珀
    "accent_hover": "#fbbf24",  # 重點懸停：亮琥珀
    "accent_dim": "#92620a",    # 重點暗色
    "accent_glow": "#f59f0022", # 重點發光（用於進度條背景）
    "text_primary": "#eef0f6",  # 主要文字
    "text_secondary": "#8b90aa", # 次要文字
    "text_dim": "#464c6b",      # 暗淡文字
    "success": "#4ade80",       # 成功：清新綠
    "error": "#f87171",         # 錯誤：柔和紅
    "warning": "#fbbf24",       # 警告：琥珀
    "progress": "#60a5fa",      # 進度條：科技藍
    "progress_track": "#1e2a45",# 進度條軌道
}

# 字體（Windows 精緻字體）
FONT_FAMILY = "Segoe UI"
FONT_FAMILY_MONO = "Cascadia Code"  # 時間輸入框用等寬字體
FONT_SIZES = {
    "title": 22,
    "heading": 17,
    "body": 15,
    "small": 14,
    "tiny": 13,
}

# 尺寸
PADDING = {
    "section": 14,
    "inner": 10,
    "small": 6,
}

CORNER_RADIUS = 10
WINDOW_SIZE = "1140x780"
WINDOW_MIN_SIZE = (960, 640)
