@echo off
chcp 65001 >nul
echo ============================================
echo   Auto-Process 課程影片自動剪輯 - 安裝程式
echo ============================================
echo.

:: 檢查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 找不到 Python，請先安裝 Python 3.8+
    pause
    exit /b 1
)

:: 檢查 FFmpeg
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 找不到 FFmpeg，請先安裝並加入 PATH
    pause
    exit /b 1
)

echo [1/4] 安裝 Python 依賴...
pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [ERROR] 安裝依賴失敗
    pause
    exit /b 1
)

echo.
echo [2/4] 建立工作資料夾...
mkdir "%~dp0inbox" 2>nul
mkdir "%~dp0processing" 2>nul
mkdir "%~dp0done" 2>nul
mkdir "%~dp0failed" 2>nul
mkdir "%~dp0logs" 2>nul

echo.
echo [3/4] 設定環境變數...
if not exist "%~dp0..\.env" (
    copy "%~dp0..\.env.example" "%~dp0..\.env" >nul
    echo [!] 已建立 .env 檔案，請編輯以下設定：
    echo     %~dp0..\.env
    echo.
    echo     需要設定：
    echo     - YOUTUBE_CLIENT_SECRET: Google OAuth2 憑證路徑
    echo     - YOUTUBE_TOKEN_PATH: Token 儲存路徑
) else (
    echo     .env 已存在，跳過。
)

echo.
echo [4/4] 檢查 YouTube 認證...
if not exist "%~dp0..\credentials" (
    mkdir "%~dp0..\credentials" 2>nul
    echo [!] 請將 Google OAuth2 的 client_secret.json 放到：
    echo     %~dp0..\credentials\client_secret.json
    echo.
    echo     然後執行以下指令完成首次認證：
    echo     python "%~dp0youtube_uploader.py" --auth
)

echo.
echo ============================================
echo   安裝完成！
echo.
echo   手動裁剪:  python "%~dp0course_trimmer.py" "影片.mp4"
echo   啟動監聽:  "%~dp0run_daemon.bat"
echo   YouTube:   python "%~dp0youtube_uploader.py" --auth
echo ============================================
pause
