# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build spec for 課程影片處理工具
# 執行: pyinstaller build.spec

import os
import customtkinter

block_cipher = None

# customtkinter 資源路徑
ctk_path = os.path.dirname(customtkinter.__file__)

a = Analysis(
    ['main_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        # customtkinter 主題資源
        (ctk_path, 'customtkinter'),
        # .env 範本
        ('../.env.example', '.'),
    ],
    hiddenimports=[
        'customtkinter',
        'PIL',
        'PIL._tkinter_finder',
        'google_auth_oauthlib',
        'googleapiclient',
        'googleapiclient.discovery',
        'google.auth.transport.requests',
        'google.oauth2.credentials',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AutoProcess',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 無 console 視窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='app_icon.ico',  # 取消註解以設定圖示
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AutoProcess',
)
