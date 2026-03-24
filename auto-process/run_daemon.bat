@echo off
chcp 65001 >nul
echo 啟動 Auto-Process Daemon...
echo 按 Ctrl+C 停止
echo.
cd /d "%~dp0"
python daemon.py
pause
