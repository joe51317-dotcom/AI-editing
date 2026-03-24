---
description: 課程影片自動剪輯 — 裁剪靜音 + 上傳 YouTube
allowed-tools: Bash, Read, Edit, Write, Glob, Grep
---

你正在管理課程影片自動處理系統。系統會裁剪影片中的靜音段落（開頭/結尾空白、中間 >5 分鐘的休息），並上傳到 YouTube。

## 系統架構

所有程式碼在 `auto-process/` 目錄：
- `course_trimmer.py` — 裁剪器（可獨立執行）
- `silence_detector.py` — FFmpeg silencedetect 靜音偵測
- `video_renderer.py` — FFmpeg stream copy 無損裁剪
- `youtube_uploader.py` — YouTube API 上傳
- `daemon.py` — 資料夾監聽 daemon
- `config.py` — 從 .env 讀取設定

## 工作資料夾

- **inbox/**: 放影片的地方（daemon 監聽此目錄）
- **processing/**: 處理中的影片
- **done/**: 已完成的影片
- **failed/**: 處理失敗的影片
- **logs/**: 日誌檔案

## 可用操作

根據使用者的要求執行對應操作：

### 手動處理影片
```bash
cd auto-process
python course_trimmer.py "影片路徑.mp4"
```

### 上傳到 YouTube
```bash
cd auto-process
python youtube_uploader.py "影片路徑.mp4" --title "標題" --privacy unlisted
```

### 查看 daemon 狀態
讀取最新的日誌檔案：`auto-process/logs/daemon_*.log`
檢查 daemon 程序：`tasklist | findstr python`

### 啟動/停止 daemon
```bash
cd auto-process
python daemon.py        # 前景啟動
# 或
start run_daemon.bat    # 背景啟動
```

### 重試失敗任務
將 `failed/` 中的影片移回 `inbox/`：
```bash
move auto-process\failed\*.mp4 auto-process\inbox\
```

### 設定
讀取/編輯 `.env` 檔案中的設定項目。

### YouTube 認證
```bash
cd auto-process
python youtube_uploader.py --auth
```

## 注意事項
- 所有路徑設定在專案根目錄的 `.env` 檔案
- YouTube 需要先完成 OAuth2 認證（首次執行 `--auth`）
- daemon 重啟時會自動處理 inbox 中殘留的影片
- 影片格式支援: .mp4, .mov, .mkv, .avi, .mts, .m4v
