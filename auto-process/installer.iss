; Inno Setup Script for 課程影片處理工具
; 產生安裝檔: AutoProcessSetup.exe
;
; 使用方式:
;   1. 先執行 PyInstaller: pyinstaller build.spec
;   2. 下載 Inno Setup: https://jrsoftware.org/isinfo.php
;   3. 用 Inno Setup 編譯此腳本

#define MyAppName "課程影片處理工具"
#define MyAppNameEn "AutoProcess"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Auto-Process"
#define MyAppExeName "AutoProcess.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=AutoProcessSetup
; UncommentForIcon: SetupIconFile=app_icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "chinese_traditional"; MessagesFile: "compiler:Languages\ChineseTraditional.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "其他選項:"; Flags: checked

[Files]
; PyInstaller 打包輸出的所有檔案
Source: "dist\AutoProcess\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; FFmpeg（如果已下載到 ffmpeg/ 目錄）
Source: "ffmpeg\ffmpeg.exe"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion skipifsourcedoesntexist
Source: "ffmpeg\ffprobe.exe"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion skipifsourcedoesntexist

; credentials 範本目錄
Source: "credentials\.gitkeep"; DestDir: "{app}\credentials"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即啟動 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 清理自動產生的檔案
Type: filesandordirs; Name: "{app}\credentials\token.json"
Type: filesandordirs; Name: "{app}\ffmpeg"
Type: filesandordirs; Name: "{app}\logs"
