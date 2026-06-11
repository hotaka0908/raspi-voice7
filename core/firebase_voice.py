"""
Firebase Voice Messaging Module

ラズパイとスマホ間で音声メッセージをやり取りするためのモジュール
Firebase Realtime Database + Cloud Storage を使用
REST API経由でアクセス（サービスアカウント不要）
認証は firebase_auth モジュールのIDトークンを使用
"""

import os
import time
import logging
import requests
import threading
from typing import Optional, Callable, Dict, List, Any
from dotenv import load_dotenv

from . import firebase_auth

# ロガー設定
logger = logging.getLogger("firebase_voice")

# 環境変数の読み込み
env_path = os.path.expanduser("~/.ai-necklace/.env")
load_dotenv(env_path)

# Firebase設定
FIREBASE_CONFIG = {
    "apiKey": os.getenv("FIREBASE_API_KEY", ""),
    "databaseURL": os.getenv("FIREBASE_DATABASE_URL", ""),
    "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET", ""),
}


class FirebaseVoiceMessenger:
    """Firebase を使った音声メッセージング"""

    def __init__(self, device_id: str = "raspi",
                 on_message_received: Optional[Callable] = None):
        self.device_id = device_id
        self.on_message_received = on_message_received
        self.db_url = FIREBASE_CONFIG["databaseURL"]
        self.storage_bucket = FIREBASE_CONFIG["storageBucket"]
        self.api_key = FIREBASE_CONFIG["apiKey"]
        self.running = False
        self.listener_thread = None
        self.processed_ids = set()

    def _upload_to_storage(self, object_path: str, data: bytes,
                           content_type: str) -> Optional[str]:
        """Cloud Storageにアップロードし、閲覧用URL（ダウンロードトークン付き）を返す"""
        storage_url = f"https://firebasestorage.googleapis.com/v0/b/{self.storage_bucket}/o"
        encoded_path = requests.utils.quote(object_path, safe='')
        upload_url = f"{storage_url}/{encoded_path}"

        headers = {"Content-Type": content_type}
        headers.update(firebase_auth.storage_auth_headers())
        try:
            response = requests.post(upload_url, headers=headers, data=data, timeout=30)
        except requests.exceptions.RequestException as e:
            logger.error(f"Storageアップロードエラー ({object_path}): {e}")
            return None

        if response.status_code != 200:
            logger.error(f"Storageアップロード失敗 ({object_path}): "
                         f"{response.status_code} {response.text[:200]}")
            return None

        media_url = f"{storage_url}/{encoded_path}?alt=media"
        # ルールを auth != null に締めてもスマホ側の<img>等で表示できるよう、
        # ダウンロードトークンをURLに付与する
        try:
            token = (response.json().get("downloadTokens") or "").split(",")[0]
            if token:
                media_url += f"&token={token}"
        except Exception:
            pass
        return media_url

    def _db_request(self, method: str, path: str, payload: Any = None,
                    timeout: float = 10) -> Optional[requests.Response]:
        """Realtime DBへの認証付きRESTリクエスト。通信エラー時はNone。"""
        url = f"{self.db_url}/{path}"
        try:
            return requests.request(method, url, json=payload,
                                    params=firebase_auth.db_auth_params(),
                                    timeout=timeout)
        except requests.exceptions.RequestException as e:
            logger.error(f"Firebase DBリクエストエラー ({method} {path}): {e}")
            return None

    def upload_audio(self, audio_data: bytes, filename: str = None) -> Optional[str]:
        """音声データをFirebase Storageにアップロード"""
        if filename is None:
            timestamp = int(time.time() * 1000)
            filename = f"{self.device_id}_{timestamp}.wav"
        return self._upload_to_storage(f"audio/{filename}", audio_data, "audio/wav")

    def upload_photo(self, photo_data: bytes, filename: str = None) -> Optional[str]:
        """写真データをFirebase Storageにアップロード"""
        if filename is None:
            timestamp = int(time.time() * 1000)
            filename = f"{self.device_id}_{timestamp}.jpg"
        return self._upload_to_storage(f"photos/{filename}", photo_data, "image/jpeg")

    def send_message(self, audio_data: bytes, text: Optional[str] = None) -> bool:
        """音声メッセージを送信"""
        timestamp = int(time.time() * 1000)
        filename = f"{self.device_id}_{timestamp}.wav"
        audio_url = self.upload_audio(audio_data, filename)

        if not audio_url:
            return False

        message_data = {
            "from": self.device_id,
            "audio_url": audio_url,
            "filename": filename,
            "timestamp": timestamp,
            "played": False,
        }

        if text:
            message_data["text"] = text

        response = self._db_request("POST", "messages.json", message_data)
        return response is not None and response.status_code == 200

    def send_photo_message(self, photo_data: bytes, text: Optional[str] = None) -> bool:
        """写真メッセージを送信"""
        timestamp = int(time.time() * 1000)
        filename = f"{self.device_id}_{timestamp}.jpg"
        photo_url = self.upload_photo(photo_data, filename)

        if not photo_url:
            return False

        message_data = {
            "from": self.device_id,
            "photo_url": photo_url,
            "filename": filename,
            "timestamp": timestamp,
            "played": False,
            "type": "photo",
        }

        if text:
            message_data["text"] = text

        response = self._db_request("POST", "messages.json", message_data)
        return response is not None and response.status_code == 200

    def upload_lifelog_photo(self, photo_data: bytes, date: str, time_str: str,
                             analysis: str = "",
                             location: Optional[Dict[str, Any]] = None) -> bool:
        """ライフログ写真をFirebaseにアップロード

        Args:
            photo_data: 写真のバイナリデータ
            date: 日付 (YYYY-MM-DD)
            time_str: 時刻 (HHMMSS)
            analysis: 写真の分析結果
            location: 位置情報 (latitude, longitude, accuracy, source)
        """
        filename = f"{time_str}.jpg"
        photo_url = self._upload_to_storage(f"lifelogs/{date}/{filename}",
                                            photo_data, "image/jpeg")
        if not photo_url:
            return False

        timestamp = int(time.time() * 1000)
        time_formatted = f"{time_str[:2]}:{time_str[2:4]}"

        doc_data = {
            "deviceId": self.device_id,
            "timestamp": timestamp,
            "time": time_formatted,
            "photoUrl": photo_url,
            "analyzed": bool(analysis),
            "analysis": analysis
        }

        # 位置情報があれば追加
        if location:
            doc_data["location"] = location

        response = self._db_request("PUT", f"lifelogs/{date}/{time_str}.json", doc_data)
        return response is not None and response.status_code == 200

    def get_lifelogs_for_date(self, date: str) -> List[Dict]:
        """指定日のライフログエントリを取得

        Args:
            date: 日付 (YYYY-MM-DD)

        Returns:
            ライフログエントリのリスト（時刻順）
        """
        try:
            response = self._db_request("GET", f"lifelogs/{date}.json")
            if response is None or response.status_code != 200:
                return []

            data = response.json()
            if not data:
                return []

            entries = []
            for time_str, entry in data.items():
                if isinstance(entry, dict):
                    entry["time_key"] = time_str
                    entries.append(entry)

            # 時刻でソート
            entries.sort(key=lambda x: x.get("time_key", ""))
            return entries
        except Exception:
            return []

    def save_lifelog_summary(self, date: str, summary: str) -> bool:
        """日のサマリーをFirebaseに保存

        Args:
            date: 日付 (YYYY-MM-DD)
            summary: AIが生成したサマリー

        Returns:
            成功時True
        """
        summary_data = {
            "summary": summary,
            "updatedAt": int(time.time() * 1000)
        }
        response = self._db_request("PUT", f"lifelogs_summary/{date}.json", summary_data)
        return response is not None and response.status_code == 200

    def get_messages(self, limit: int = 10, unplayed_only: bool = False) -> List[Dict]:
        """メッセージ一覧を取得"""
        response = self._db_request("GET", "messages.json")
        if response is None:
            return []

        if response.status_code != 200:
            logger.error(f"[POLLING] Firebase応答エラー: {response.status_code}")
            return []

        try:
            data = response.json()
        except ValueError as e:
            logger.error(f"[POLLING] Firebase応答のJSON解析エラー: {e}")
            return []

        if not data:
            return []

        messages = []
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            value["id"] = key
            if value.get("from") != self.device_id:
                if not unplayed_only or not value.get("played", False):
                    messages.append(value)

        messages.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return messages[:limit]

    def download_audio(self, audio_url: str) -> Optional[bytes]:
        """音声データをダウンロード"""
        try:
            response = requests.get(audio_url, timeout=30,
                                    headers=firebase_auth.storage_auth_headers())
            if response.status_code == 200:
                return response.content
            logger.error(f"音声ダウンロード失敗: {response.status_code}")
        except requests.exceptions.RequestException as e:
            logger.error(f"音声ダウンロードエラー: {e}")
        return None

    def mark_as_played(self, message_id: str) -> None:
        """メッセージを再生済みにマーク"""
        if not message_id:
            return
        self._db_request("PUT", f"messages/{message_id}/played.json", True)

    def update_message_text(self, message_id: str, text: str) -> bool:
        """メッセージのテキストを更新"""
        response = self._db_request("PUT", f"messages/{message_id}/text.json", text)
        return response is not None and response.status_code == 200

    def start_listening(self, poll_interval: float = 3.0, max_processed_ids: int = 100) -> None:
        """新着メッセージの監視を開始

        Args:
            poll_interval: ポーリング間隔（秒）
            max_processed_ids: processed_idsの最大保持数
        """
        self.running = True
        self.processed_ids = set()

        # 起動時刻を記録（この時刻より前のメッセージは処理しない）
        startup_timestamp = int(time.time() * 1000)

        def poll_loop():
            # 既存メッセージを記録（リトライ付き）
            logger.info("[POLLING] ポーリング開始、既存メッセージを記録中...")
            max_retries = 3
            for attempt in range(max_retries):
                messages = self.get_messages(limit=20)
                if messages:
                    for msg in messages:
                        self.processed_ids.add(msg.get("id"))
                    logger.info(f"[POLLING] 既存メッセージ {len(self.processed_ids)} 件を記録")
                    break
                elif attempt < max_retries - 1:
                    logger.warning(f"[POLLING] 既存メッセージ取得失敗、リトライ {attempt + 1}/{max_retries}")
                    time.sleep(2)
                else:
                    logger.warning("[POLLING] 既存メッセージ取得失敗、起動時刻ベースでフィルタリング")

            poll_count = 0
            while self.running:
                try:
                    poll_count += 1
                    messages = self.get_messages(limit=15, unplayed_only=False)

                    # 現在取得中のメッセージIDのセット
                    current_ids = {m.get("id") for m in messages}

                    # processed_idsを現在のメッセージに存在するもののみに絞る
                    # （古いメッセージが削除されたらprocessed_idsからも削除）
                    # 取得失敗で空リストが返った直後に縮小すると全消えして
                    # 重複再生するため、取得できた時のみ縮小する
                    if messages and len(self.processed_ids) > max_processed_ids:
                        self.processed_ids = self.processed_ids & current_ids
                        logger.debug(f"[POLLING] processed_ids縮小: {len(self.processed_ids)} 件")

                    # 10回に1回、または新着メッセージがある時にログ出力
                    new_messages = [m for m in messages if m.get("id") not in self.processed_ids]
                    if new_messages:
                        logger.info(f"[POLLING] 新着メッセージ {len(new_messages)} 件検出")

                    for msg in reversed(messages):
                        msg_id = msg.get("id")

                        if msg_id in self.processed_ids:
                            continue

                        # 起動時刻より前のメッセージはスキップ（フォールバック）
                        msg_timestamp = msg.get("timestamp", 0)
                        if msg_timestamp < startup_timestamp:
                            logger.debug(f"[POLLING] 起動前メッセージをスキップ: id={msg_id}")
                            self.processed_ids.add(msg_id)
                            continue

                        logger.info(f"[POLLING] メッセージ処理開始: id={msg_id}, from={msg.get('from')}")

                        if self.on_message_received:
                            try:
                                self.on_message_received(msg)
                                logger.info(f"[POLLING] メッセージ処理完了: id={msg_id}")
                            except Exception as e:
                                logger.error(f"[POLLING] コールバックエラー: {e}")

                        # 処理試行済みとして記録（再起動時にリトライ可能）
                        # mark_as_playedはコールバック内で成功時のみ呼び出される
                        self.processed_ids.add(msg_id)

                except Exception as e:
                    logger.error(f"[POLLING] ポーリングエラー: {e}")

                time.sleep(poll_interval)

        self.listener_thread = threading.Thread(target=poll_loop, daemon=True)
        self.listener_thread.start()

    def stop_listening(self) -> None:
        """監視を停止"""
        self.running = False
        if self.listener_thread:
            self.listener_thread.join(timeout=5)

    def send_detail_info(self, image_data: bytes, brief_analysis: str,
                         detail_analysis: str, original_prompt: str) -> bool:
        """詳細情報をFirebaseに送信

        Args:
            image_data: 画像バイナリ
            brief_analysis: 簡潔な分析結果
            detail_analysis: 詳細な分析結果（Markdown形式）
            original_prompt: 元の質問

        Returns:
            成功時True
        """
        timestamp = int(time.time() * 1000)
        filename = f"{self.device_id}_{timestamp}.jpg"

        # 1. Storage: /detail_photos/{filename} に画像保存
        image_url = self._upload_to_storage(f"detail_photos/{filename}",
                                            image_data, "image/jpeg")
        if not image_url:
            return False

        # 2. Realtime DB: /detail_info に詳細情報保存
        detail_data = {
            "deviceId": self.device_id,
            "timestamp": timestamp,
            "imageUrl": image_url,
            "briefAnalysis": brief_analysis,
            "detailAnalysis": detail_analysis,
            "originalPrompt": original_prompt,
            "read": False
        }

        response = self._db_request("POST", "detail_info.json", detail_data)
        if response is None or response.status_code != 200:
            if response is not None:
                logger.error(f"詳細情報の保存失敗: {response.status_code} {response.text[:200]}")
            return False
        return True
