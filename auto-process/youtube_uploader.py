"""
YouTube 上傳模組 — 使用 YouTube Data API v3 + OAuth2
首次使用需執行: python youtube_uploader.py --auth
之後可自動上傳（使用 refresh token）。
"""
import sys
import io
import os
import re
import argparse
import logging
import time
import http.client
import httplib2

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

# Resumable upload 重試設定
MAX_RETRIES = 5
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]
RETRIABLE_EXCEPTIONS = (httplib2.HttpLib2Error, IOError, http.client.NotConnected,
                        http.client.IncompleteRead, http.client.ImproperConnectionState,
                        http.client.CannotSendRequest, http.client.CannotSendHeader,
                        http.client.ResponseNotReady, http.client.BadStatusLine)


def get_credentials(client_secret_path, token_path):
    """
    取得 OAuth2 憑證。如果 token 過期會自動 refresh。

    Args:
        client_secret_path: client_secret.json 路徑
        token_path: token.json 儲存路徑

    Returns:
        google.oauth2.credentials.Credentials
    """
    creds = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # 檢查 scope 是否匹配（舊 token 可能只有 youtube.upload）
    if creds and creds.valid:
        token_scopes = set(creds.scopes or [])
        required_scopes = set(SCOPES)
        if not required_scopes.issubset(token_scopes):
            logger.info("Token scope 不足，需要重新認證...")
            creds = None

    if creds and creds.expired and creds.refresh_token:
        logger.info("Token 已過期，自動更新中...")
        try:
            creds.refresh(Request())
            _save_token(creds, token_path)
        except Exception:
            logger.info("Token refresh 失敗，重新認證...")
            creds = None

    if not creds or not creds.valid:
        if not os.path.exists(client_secret_path):
            logger.error(f"找不到 client_secret.json: {client_secret_path}")
            logger.error("請先到 Google Cloud Console 下載 OAuth2 憑證。")
            raise FileNotFoundError(
                f"找不到 OAuth2 憑證: {os.path.basename(client_secret_path)}\n"
                "請到 Google Cloud Console 下載並放到 credentials/ 目錄"
            )

        flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
        creds = flow.run_local_server(port=0)
        _save_token(creds, token_path)
        logger.info("認證成功！Token 已儲存。")

    return creds


def _save_token(creds, token_path):
    """儲存 token 到檔案"""
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        f.write(creds.to_json())


def title_from_filename(video_path):
    """從檔名衍生影片標題（移除副檔名和 _trimmed 後綴）"""
    name = os.path.splitext(os.path.basename(video_path))[0]
    name = re.sub(r"_trimmed$", "", name)
    # 將底線和連字號替換為空格
    name = name.replace("_", " ").replace("-", " ")
    return name.strip()


def upload_video(video_path, title=None, description="", tags=None,
                 category_id=None, privacy_status=None,
                 client_secret_path=None, token_path=None,
                 progress_callback=None):
    """
    上傳影片到 YouTube（resumable upload）。

    Args:
        video_path: 影片檔案路徑
        title: 影片標題（預設從檔名衍生）
        description: 影片說明
        tags: 標籤清單
        category_id: YouTube 分類 ID（預設從 .env）
        privacy_status: 隱私設定（預設從 .env）
        client_secret_path: OAuth2 client secret 路徑（預設從 .env）
        token_path: token 儲存路徑（預設從 .env）
        progress_callback: 可選回呼 callback(progress_pct) 0-100

    Returns:
        str | None: YouTube 影片 ID，失敗則 None
    """
    from config import (YOUTUBE_CLIENT_SECRET, YOUTUBE_TOKEN_PATH,
                        YOUTUBE_DEFAULT_PRIVACY, YOUTUBE_DEFAULT_CATEGORY)

    client_secret_path = client_secret_path or YOUTUBE_CLIENT_SECRET
    token_path = token_path or YOUTUBE_TOKEN_PATH
    privacy_status = privacy_status or YOUTUBE_DEFAULT_PRIVACY
    category_id = category_id or YOUTUBE_DEFAULT_CATEGORY
    title = title or title_from_filename(video_path)
    tags = tags or []

    if not os.path.exists(video_path):
        logger.error(f"影片不存在: {video_path}")
        return None

    # 取得認證
    creds = get_credentials(client_secret_path, token_path)
    if not creds:
        return None

    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/*",
        resumable=True,
        chunksize=50 * 1024 * 1024,  # 50MB chunks
    )

    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    logger.info(f"開始上傳: {title}")
    logger.info(f"  隱私: {privacy_status} | 分類: {category_id}")

    video_id = _resumable_upload(request, video_path, progress_callback)
    return video_id


def _resumable_upload(request, video_path, progress_callback=None):
    """執行 resumable upload，含重試邏輯"""
    response = None
    retry = 0
    file_size = os.path.getsize(video_path) / (1024 * 1024)

    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                logger.info(f"  上傳進度: {progress}% ({file_size:.0f}MB)")
                if progress_callback:
                    progress_callback(progress)
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS_CODES:
                retry = _handle_retry(retry, f"HTTP {e.resp.status}")
                if retry is None:
                    return None
            else:
                logger.error(f"上傳失敗 (HTTP {e.resp.status}): {e.content.decode(errors='replace')}")
                return None
        except RETRIABLE_EXCEPTIONS as e:
            retry = _handle_retry(retry, str(e))
            if retry is None:
                return None

    video_id = response.get("id")
    logger.info(f"上傳完成! Video ID: {video_id}")
    logger.info(f"  https://youtu.be/{video_id}")
    return video_id


def _handle_retry(retry, error_msg):
    """處理重試邏輯，返回新的 retry 計數，超過上限則返回 None"""
    if retry >= MAX_RETRIES:
        logger.error(f"重試次數已達上限 ({MAX_RETRIES}): {error_msg}")
        return None
    retry += 1
    wait = 2 ** retry
    logger.warning(f"重試 {retry}/{MAX_RETRIES} (等待 {wait}s): {error_msg}")
    time.sleep(wait)
    return retry


def main():
    parser = argparse.ArgumentParser(description="YouTube 影片上傳工具")
    parser.add_argument("video_path", nargs="?", help="影片檔案路徑")
    parser.add_argument("--auth", action="store_true", help="僅執行 OAuth2 認證（首次設定用）")
    parser.add_argument("--title", default=None, help="影片標題")
    parser.add_argument("--description", default="", help="影片說明")
    parser.add_argument("--privacy", default=None, help="隱私設定: unlisted/public/private")
    args = parser.parse_args()

    if args.auth:
        from config import YOUTUBE_CLIENT_SECRET, YOUTUBE_TOKEN_PATH
        creds = get_credentials(YOUTUBE_CLIENT_SECRET, YOUTUBE_TOKEN_PATH)
        if creds:
            print("YouTube 認證成功！可以開始自動上傳了。")
        else:
            print("認證失敗，請檢查 client_secret.json 路徑。")
            sys.exit(1)
        return

    if not args.video_path:
        parser.print_help()
        sys.exit(1)

    video_id = upload_video(
        args.video_path,
        title=args.title,
        description=args.description,
        privacy_status=args.privacy,
    )

    if video_id:
        print(f"\n上傳成功: https://youtu.be/{video_id}")
    else:
        print("\n上傳失敗。")
        sys.exit(1)


if __name__ == "__main__":
    main()
