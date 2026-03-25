"""
YouTube API 擴充模組 — 播放清單、字幕、縮圖管理
依賴 youtube_uploader.py 的認證機制。
"""
import logging

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from youtube_uploader import get_credentials, SCOPES

logger = logging.getLogger(__name__)


def get_authenticated_service(client_secret_path=None, token_path=None):
    """
    取得已認證的 YouTube API service 物件。

    Returns:
        googleapiclient.discovery.Resource | None
    """
    from config import YOUTUBE_CLIENT_SECRET, YOUTUBE_TOKEN_PATH

    client_secret_path = client_secret_path or YOUTUBE_CLIENT_SECRET
    token_path = token_path or YOUTUBE_TOKEN_PATH

    creds = get_credentials(client_secret_path, token_path)
    if not creds:
        return None

    return build("youtube", "v3", credentials=creds)


def get_channel_info(youtube):
    """
    取得登入帳號的頻道資訊。

    Returns:
        dict: {'name': str, 'id': str, 'thumbnail': str} 或 None
    """
    try:
        response = youtube.channels().list(
            part="snippet",
            mine=True,
        ).execute()

        items = response.get("items", [])
        if not items:
            return None

        snippet = items[0]["snippet"]
        return {
            "name": snippet.get("title", ""),
            "id": items[0]["id"],
            "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
        }
    except HttpError as e:
        logger.error(f"取得頻道資訊失敗: {e}")
        return None


def list_playlists(youtube):
    """
    取得登入帳號的所有播放清單。

    Returns:
        list[dict]: [{'id': str, 'title': str}, ...]
    """
    playlists = []
    try:
        request = youtube.playlists().list(
            part="snippet",
            mine=True,
            maxResults=50,
        )

        while request:
            response = request.execute()
            for item in response.get("items", []):
                playlists.append({
                    "id": item["id"],
                    "title": item["snippet"]["title"],
                })
            request = youtube.playlists().list_next(request, response)

    except HttpError as e:
        logger.error(f"取得播放清單失敗: {e}")

    return playlists


def create_playlist(youtube, title, privacy_status="unlisted"):
    """
    建立新的播放清單。

    Returns:
        dict: {'id': str, 'title': str} 或 None
    """
    try:
        response = youtube.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title,
                },
                "status": {
                    "privacyStatus": privacy_status,
                },
            },
        ).execute()

        result = {
            "id": response["id"],
            "title": response["snippet"]["title"],
        }
        logger.info(f"已建立播放清單: {result['title']} ({result['id']})")
        return result

    except HttpError as e:
        logger.error(f"建立播放清單失敗: {e}")
        return None


def add_to_playlist(youtube, video_id, playlist_id):
    """
    將影片加入播放清單。

    Returns:
        bool: 成功與否
    """
    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id,
                    },
                },
            },
        ).execute()

        logger.info(f"已加入播放清單: {playlist_id}")
        return True

    except HttpError as e:
        logger.error(f"加入播放清單失敗: {e}")
        return False


def set_thumbnail(youtube, video_id, thumbnail_path):
    """
    上傳自訂縮圖到影片。

    Args:
        thumbnail_path: 圖片路徑（JPG/PNG，建議 1280x720）

    Returns:
        bool: 成功與否
    """
    try:
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg"),
        ).execute()

        logger.info(f"已設定縮圖: {video_id}")
        return True

    except HttpError as e:
        logger.error(f"設定縮圖失敗: {e}")
        return False


def upload_caption(youtube, video_id, srt_path, language="zh-Hant", name=""):
    """
    上傳 SRT 字幕到 YouTube 影片。

    Args:
        video_id: YouTube 影片 ID
        srt_path: SRT 字幕檔路徑
        language: BCP-47 語言代碼（預設 zh-Hant 繁體中文）
        name: 字幕軌名稱

    Returns:
        bool: 成功與否
    """
    try:
        youtube.captions().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "language": language,
                    "name": name,
                },
            },
            media_body=MediaFileUpload(srt_path, mimetype="application/x-subrip"),
        ).execute()

        logger.info(f"已上傳字幕 ({language}): {video_id}")
        return True

    except HttpError as e:
        logger.error(f"上傳字幕失敗: {e}")
        return False
