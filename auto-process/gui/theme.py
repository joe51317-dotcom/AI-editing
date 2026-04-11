"""
GUI 主題設定 — 精緻深色主題 + 琥珀色重點色
"""

# 配色（深藍黑調，多層次）
COLORS = {
    # 底層背景
    "bg_dark": "#0b0d14",        # 主背景：極深藍黑
    "bg_card": "#151824",        # 卡片：略帶藍調
    "bg_elevated": "#1c2033",    # 浮起層：焦點卡片
    "bg_hover": "#232740",       # 懸停
    "bg_input": "#10131c",       # 輸入框：最深
    "bg_subtle": "#12151f",      # 極低調底

    # 邊框
    "border": "#2a2f48",
    "border_subtle": "#1c2033",

    # 重點色：琥珀
    "accent": "#f59f00",
    "accent_hover": "#fbbf24",
    "accent_dim": "#7a4f0a",
    "accent_glow": "#f59f0022",
    "accent_soft": "#f59f0011",  # 分頁底色、卡片 tint

    # 文字
    "text_primary": "#eef0f6",
    "text_secondary": "#8b90aa",
    "text_tertiary": "#5e6382",  # 低調輔助文字
    "text_dim": "#464c6b",

    # 狀態
    "success": "#4ade80",
    "error": "#f87171",
    "warning": "#fbbf24",
    "progress": "#60a5fa",       # 進度條：科技藍
    "progress_track": "#1e2a45",
}

# 字體（Windows 系統內建，CJK 場景優化）
FONT_FAMILY = "Microsoft JhengHei UI"   # 繁中 UI 字體，中英混排最漂亮
FONT_FAMILY_MONO = "Cascadia Mono"      # 數字/時間/日誌（fallback: Consolas）
FONT_FAMILY_DISPLAY = "Segoe UI Semibold"  # 品牌列大標題

FONT_SIZES = {
    "display": 22,   # 品牌列
    "heading": 15,   # 卡片標題
    "body": 14,
    "small": 13,
    "tiny": 12,
    "micro": 11,     # badge、輔助標籤
}

# 尺寸
PADDING = {
    "xl": 18,
    "section": 14,
    "inner": 10,
    "small": 6,
    "tiny": 4,
}

CORNER_RADIUS = 10
CORNER_SMALL = 6
WINDOW_SIZE = "1200x820"
WINDOW_MIN_SIZE = (980, 620)
