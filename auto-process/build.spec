# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build spec for 課程影片處理工具
# 執行: pyinstaller build.spec

import os
import customtkinter

block_cipher = None

# customtkinter 資源路徑
ctk_path = os.path.dirname(customtkinter.__file__)

# tkinterdnd2 DLL 路徑（拖放支援）
try:
    import tkinterdnd2
    dnd_path = os.path.dirname(tkinterdnd2.__file__)
    dnd_data = [(dnd_path, 'tkinterdnd2')]
except ImportError:
    dnd_data = []

a = Analysis(
    ['main_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        # customtkinter 主題資源
        (ctk_path, 'customtkinter'),
        # .env 範本
        ('../.env.example', '.'),
        # 鼎愛品牌資源
        ('assets', 'assets'),
    ] + dnd_data,
    hiddenimports=[
        'customtkinter',
        'PIL',
        'PIL._tkinter_finder',
        # Google API
        'google_auth_oauthlib',
        'googleapiclient',
        'googleapiclient.discovery',
        'google.auth.transport.requests',
        'google.oauth2.credentials',
        # keyring（安全存放 YouTube token）
        'keyring',
        'keyring.backends',
        'keyring.backends.Windows',
        'jaraco.classes',
        'jaraco.functools',
        'jaraco.context',
        'backports.tarfile',
        # 拖放支援
        'tkinterdnd2',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 科學計算（未使用）
        'matplotlib', 'mpl_toolkits',
        'numpy',
        # 互動式 Python shell（未使用）
        'IPython', 'ipykernel', 'ipython_genutils',
        'jedi', 'parso',
        # 測試框架（運行時不需要）
        'pytest', '_pytest',
        'unittest',
        # 文件工具（未使用）
        'docutils', 'sphinx',
        # 安裝工具（運行時不需要）
        'setuptools', 'pkg_resources._vendor', '_distutils_hack',
        'lib2to3',
        # tkinter 測試（未使用）
        'tkinter.test',
        'test',
    ],
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
    name='AIEdit',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/app.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AIEdit',
)
