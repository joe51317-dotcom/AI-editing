# Auto-Process 課程影片自動剪輯系統

將影片放到指定資料夾，自動移除靜音段落、按休息時間切成多段、上傳到 YouTube。

## 系統流程

```
影片放入 inbox/
    ↓
偵測靜音（FFmpeg silencedetect，約 30-60 秒）
    ↓
移除開頭/結尾空白
    ↓
遇到 >5 分鐘休息 → 切成多段影片
    ↓
輸出: 原檔名-1.mp4, 原檔名-2.mp4, ...
    ↓
自動上傳到 YouTube（unlisted）
    ↓
完成後移到 done/
```

**裁剪速度**：使用 FFmpeg stream copy（無重新編碼），2 小時影片約 1-2 分鐘完成。

## 安裝（首次設定）

### 前置需求

- Python 3.8 以上
- FFmpeg（須在系統 PATH 中）

### 步驟

```bash
# 1. 進入目錄
cd auto-process

# 2. 一鍵安裝（安裝 Python 套件 + 建立工作資料夾）
setup.bat

# 3. 編輯設定檔（安裝程式會自動建立）
#    打開 ..\.env，設定 YouTube 憑證路徑
notepad ..\.env

# 4. YouTube 認證（首次需要，之後自動）
python youtube_uploader.py --auth
```

### YouTube API 設定

1. 到 [Google Cloud Console](https://console.cloud.google.com/) 建立專案
2. 啟用 **YouTube Data API v3**
3. 建立 **OAuth 2.0 用戶端 ID**（桌面應用程式）
4. 下載 `client_secret.json`，放到 `credentials/` 資料夾
5. 執行 `python youtube_uploader.py --auth`，瀏覽器會跳出認證頁面
6. 認證完成後會自動儲存 `token.json`，之後上傳不需要再認證

## 使用方式

### 方式一：自動模式（Daemon 監聽）

啟動 daemon 後，只要把影片丟進 `inbox/` 資料夾就會自動處理。

```bash
# 啟動（擇一）
run_daemon.bat           # 點兩下啟動
python daemon.py         # 或用指令啟動

# 停止
Ctrl+C
```

Daemon 啟動後：
1. 把影片檔案複製或移動到 `auto-process/inbox/`
2. 系統偵測到新檔案 → 等待複製完成 → 自動裁剪 → 自動上傳
3. 完成後影片移到 `done/`，失敗的移到 `failed/`
4. 日誌在 `logs/` 目錄

### 方式二：手動裁剪（不上傳）

```bash
cd auto-process
python course_trimmer.py "D:\Videos\課程錄影.mp4"
```

輸出會在影片同目錄：`課程錄影-1.mp4`, `課程錄影-2.mp4`, ...

### 方式三：手動上傳

```bash
cd auto-process
python youtube_uploader.py "D:\Videos\課程錄影-1.mp4"
python youtube_uploader.py "D:\Videos\課程錄影-1.mp4" --title "第一堂課" --privacy unlisted
```

## 輸出規則

| 情況 | 輸出 |
|------|------|
| 無休息時間，只有頭尾空白 | `影片-1.mp4`（1 個檔案） |
| 1 段 >5 分鐘休息 | `影片-1.mp4`, `影片-2.mp4` |
| 2 段休息 | `影片-1.mp4`, `影片-2.mp4`, `影片-3.mp4` |
| 無靜音 | 不產生檔案（直接上傳原始影片） |

## 資料夾說明

```
auto-process/
├── inbox/        ← 放影片的地方（daemon 監聽此目錄）
├── processing/   ← 處理中（自動移入，勿手動操作）
├── done/         ← 已完成
├── failed/       ← 處理失敗（可手動移回 inbox 重試）
└── logs/         ← 日誌（每日一個檔案）
```

## 設定參數（.env）

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `SPEECH_THRESHOLD_DB` | -20 | 講課語音門檻。低於此 dB 的聲音視為環境音/靜音，用於偵測休息和開頭結尾 |
| `SILENCE_NOISE_DB` | -30 | 完全靜音判定閾值（備用） |
| `SILENCE_MIN_DURATION` | 10 | 最短靜音秒數。低於此秒數的安靜不會被偵測 |
| `BREAK_THRESHOLD_SECONDS` | 300 | 休息判定秒數。超過此長度的靜音會切成獨立影片 |
| `YOUTUBE_DEFAULT_PRIVACY` | unlisted | YouTube 隱私設定：unlisted / public / private |
| `YOUTUBE_DEFAULT_CATEGORY` | 27 | YouTube 分類（27 = 教育） |

### 調整建議

- 如果休息時間**仍沒被偵測到**（環境音很大聲）：把 `SPEECH_THRESHOLD_DB` 調高（例如 -15）
- 如果**講課中被誤判為安靜**（講師說話很小聲）：把 `SPEECH_THRESHOLD_DB` 調低（例如 -25）
- 可以先用 `ffmpeg -i video.mp4 -af volumedetect -f null -` 查看影片的平均音量，再決定門檻
- 如果想用 **3 分鐘**作為切割點：把 `BREAK_THRESHOLD_SECONDS` 改為 180

## 移植到其他電腦

1. 複製整個 `auto-process/` 資料夾到新電腦
2. 確認新電腦有 Python 3.8+ 和 FFmpeg
3. 執行 `setup.bat`
4. 把 `client_secret.json` 放到 `credentials/`（或從原電腦複製整個 `credentials/` 資料夾）
5. 執行 `python youtube_uploader.py --auth` 認證新電腦
6. 啟動 `run_daemon.bat`

> `token.json` 是綁定帳號的，每台電腦需要各自認證一次。
> `client_secret.json` 可以共用（同一個 Google Cloud 專案）。

## 支援的影片格式

.mp4, .mov, .mkv, .avi, .mts, .m4v

## 故障排除

| 問題 | 解決方式 |
|------|----------|
| daemon 沒反應 | 確認影片是支援的格式，檢查 `logs/` 日誌 |
| 影片卡在 processing/ | daemon 可能中斷了，把檔案移回 inbox/ 再重啟 daemon |
| YouTube 上傳失敗 | 檢查 `credentials/token.json` 是否存在，執行 `--auth` 重新認證 |
| 靜音偵測不準確 | 調整 `.env` 中的 `SILENCE_NOISE_DB` 和 `SILENCE_MIN_DURATION` |
| 每日上傳數量限制 | YouTube API 預設配額每天約 6 部影片，需到 Google Cloud 申請增加 |
