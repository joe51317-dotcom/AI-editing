"""
自動版本遞增 + 打包腳本
執行方式：python bump_and_build.py
或透過 build.bat 呼叫
"""
import io
import re
import sys
import os
import shutil
import subprocess

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.py")
DIST_DIR = os.path.join(SCRIPT_DIR, "dist", "AIEdit")      # onedir 輸出目錄
OUTPUT_DIR = os.path.join(REPO_ROOT, "AIEdit-dist")         # repo 根目錄下的發佈目錄


def bump_version():
    """讀取並遞增 config.py 中的 APP_VERSION（MAJOR.BUILD 格式）"""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        content = f.read()

    m = re.search(r'APP_VERSION\s*=\s*"(\d+)\.(\d+)"', content)
    if not m:
        print("❌ 找不到 APP_VERSION（需為 MAJOR.BUILD 格式，如 1.0）")
        sys.exit(1)

    major = int(m.group(1))
    build = int(m.group(2)) + 1
    new_version = f"{major}.{build}"

    new_content = re.sub(
        r'APP_VERSION\s*=\s*"[\d.]+"',
        f'APP_VERSION = "{new_version}"',
        content,
    )
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"✅ 版本更新: {major}.{build - 1} → {new_version}")
    return new_version


def run(cmd, cwd=None):
    result = subprocess.run(cmd, cwd=cwd or SCRIPT_DIR)
    if result.returncode != 0:
        print(f"❌ 指令失敗: {' '.join(cmd)}")
        sys.exit(result.returncode)


def main():
    # Step 1: 版本遞增
    new_version = bump_version()

    # Step 2: PyInstaller 打包
    print("\n📦 開始打包...")
    run(["python", "-m", "PyInstaller", "build.spec", "--noconfirm"])

    # Step 3: 複製 onedir 目錄到 repo 根目錄
    if os.path.isdir(DIST_DIR):
        if os.path.exists(OUTPUT_DIR):
            shutil.rmtree(OUTPUT_DIR)
        shutil.copytree(DIST_DIR, OUTPUT_DIR)
        print(f"✅ 發佈目錄已複製到 {OUTPUT_DIR}")
        print(f"   分發方式：壓縮 AIEdit-dist/ 整個資料夾給同事")
    else:
        print(f"⚠️  找不到 {DIST_DIR}，跳過複製")

    # Step 4: git commit + push
    print("\n🔖 提交版本更新...")
    subprocess.run(
        ["git", "add", "auto-process/config.py"],
        cwd=REPO_ROOT,
    )
    subprocess.run(
        ["git", "commit", "-m", f"chore: bump version to {new_version}"],
        cwd=REPO_ROOT,
    )
    subprocess.run(["git", "push"], cwd=REPO_ROOT)

    print(f"\n🎉 完成！AIEdit v{new_version} 已打包。執行檔位於 AIEdit-dist/AIEdit.exe")


if __name__ == "__main__":
    main()
