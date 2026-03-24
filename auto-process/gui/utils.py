"""
時間解析工具 — 將使用者輸入的時間字串轉換為 segment 格式
"""
import re


def parse_time_str(time_str):
    """
    解析時間字串為秒數。

    支援格式：
        - "HH:MM:SS" 或 "HH：MM：SS"（全形冒號）
        - "MM:SS"
        - 純秒數（整數或浮點數）

    Returns:
        float: 秒數
    Raises:
        ValueError: 格式無法解析
    """
    s = time_str.strip()
    # 全形冒號 → 半形
    s = s.replace("：", ":")

    # 嘗試 HH:MM:SS
    m = re.match(r"^(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)$", s)
    if m:
        h, mi, sec = float(m.group(1)), float(m.group(2)), float(m.group(3))
        return h * 3600 + mi * 60 + sec

    # 嘗試 MM:SS
    m = re.match(r"^(\d+):(\d{1,2}(?:\.\d+)?)$", s)
    if m:
        mi, sec = float(m.group(1)), float(m.group(2))
        return mi * 60 + sec

    # 嘗試純秒數
    m = re.match(r"^(\d+(?:\.\d+)?)$", s)
    if m:
        return float(m.group(1))

    raise ValueError(f"無法解析時間格式: '{time_str.strip()}'")


def parse_time_segments(text):
    """
    解析多行時間輸入為 parts 格式。

    格式：每行一個片段 '開始時間 - 結束時間'
    空白行分隔不同輸出檔案（不同 Part）。
    同一個 Part 內的多行會合併到同一個輸出檔案。

    分隔符支援: - – — ~

    範例輸入:
        00:00 - 45:30
        50:00 - 1:30:00

    回傳:
        parts = [[{'start': 0.0, 'end': 2730.0}, {'start': 3000.0, 'end': 5400.0}]]
        errors = []

    多 Part 範例:
        00:00 - 45:30

        50:00 - 1:30:00

    回傳:
        parts = [[{'start': 0.0, 'end': 2730.0}], [{'start': 3000.0, 'end': 5400.0}]]

    Args:
        text: 多行時間字串

    Returns:
        tuple[list[list[dict]], list[str]]: (parts, errors)
    """
    errors = []
    all_parts = []
    current_segments = []

    # 分隔符 regex：半形減號、en dash、em dash、波浪號
    separator_re = re.compile(r"\s*[-–—~]\s*")

    lines = text.strip().split("\n")

    for line_num, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()

        # 空行 → 切換到下一個 Part
        if not line:
            if current_segments:
                all_parts.append(current_segments)
                current_segments = []
            continue

        # 跳過純註解行
        if line.startswith("#"):
            continue

        # 分割開始-結束
        parts_split = separator_re.split(line, maxsplit=1)
        if len(parts_split) != 2:
            errors.append(f"第 {line_num} 行格式錯誤: '{raw_line.strip()}'（需要 '開始 - 結束'）")
            continue

        start_str, end_str = parts_split

        try:
            start = parse_time_str(start_str)
        except ValueError:
            errors.append(f"第 {line_num} 行開始時間無法解析: '{start_str.strip()}'")
            continue

        try:
            end = parse_time_str(end_str)
        except ValueError:
            errors.append(f"第 {line_num} 行結束時間無法解析: '{end_str.strip()}'")
            continue

        if end <= start:
            errors.append(f"第 {line_num} 行結束時間 ({end_str.strip()}) 必須大於開始時間 ({start_str.strip()})")
            continue

        current_segments.append({"start": start, "end": end})

    # 收集最後一組
    if current_segments:
        all_parts.append(current_segments)

    return all_parts, errors
