"""
Firebase Voice Messaging Module

ラズパイとスマホ間で音声メッセージをやり取りするためのモジュール
Firebase Realtime Database + Cloud Storage を使用
REST API経由でアクセス（サービスアカウント不要）
"""

import os
import time
import requests
import threading
from typing import Optional, Callable, Dict, List, Any
from dotenv import load_dotenv

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

    def upload_audio(self, audio_data: bytes, filename: str = None) -> Optional[str]:
        """音声データをFirebase Storageにアップロード"""
        if filename is None:
            timestamp = int(time.time() * 1000)
            filename = f"{self.device_id}_{timestamp}.wav"

        storage_url = f"https://firebasestorage.googleapis.com/v0/b/{self.storage_bucket}/o"
        encoded_path = requests.utils.quote(f"audio/{filename}", safe='')
        upload_url = f"{storage_url}/{encoded_path}"

        headers = {"Content-Type": "audio/wav"}
        response = requests.post(upload_url, headers=headers, data=audio_data)

        if response.status_code == 200:
            return f"{storage_url}/{encoded_path}?alt=media"
        return None

    def upload_photo(self, photo_data: bytes, filename: str = None) -> Optional[str]:
        """写真データをFirebase Storageにアップロード"""
        if filename is None:
            timestamp = int(time.time() * 1000)
            filename = f"{self.device_id}_{timestamp}.jpg"

        storage_url = f"https://firebasestorage.googleapis.com/v0/b/{self.storage_bucket}/o"
        encoded_path = requests.utils.quote(f"audio/{filename}", safe='')
        upload_url = f"{storage_url}/{encoded_path}"

        headers = {"Content-Type": "image/jpeg"}
        response = requests.post(upload_url, headers=headers, data=photo_data)

        if response.status_code == 200:
            return f"{storage_url}/{encoded_path}?alt=media"
        return None

    def send_message(self, audio_data: bytes, text: str = None) -> bool:
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

        db_url = f"{self.db_url}/messages.json"
        response = requests.post(db_url, json=message_data)
        return response.status_code == 200

    def send_photo_message(self, photo_data: bytes, text: str = None) -> bool:
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

        db_url = f"{self.db_url}/messages.json"
        response = requests.post(db_url, json=message_data)
        return response.status_code == 200

    def upload_lifelog_photo(self, photo_data: bytes, date: str, time_str: str) -> bool:
        """ライフログ写真をFirebaseにアップロード"""
        filename = f"{time_str}.jpg"
        storage_url = f"https://firebasestorage.googleapis.com/v0/b/{self.storage_bucket}/o"
        encoded_path = requests.utils.quote(f"lifelogs/{date}/{filename}", safe='')
        upload_url = f"{storage_url}/{encoded_path}"

        headers = {"Content-Type": "image/jpeg"}
        response = requests.post(upload_url, headers=headers, data=photo_data)

        if response.status_code != 200:
            return False

        photo_url = f"{storage_url}/{encoded_path}?alt=media"
        timestamp = int(time.time() * 1000)
        time_formatted = f"{time_str[:2]}:{time_str[2:4]}"

        doc_data = {
            "deviceId": self.device_id,
            "timestamp": timestamp,
            "time": time_formatted,
            "photoUrl": photo_url,
            "analyzed": False,
            "analysis": ""
        }

        db_url = f"{self.db_url}/lifelogs/{date}/{time_str}.json"
        response = requests.put(db_url, json=doc_data)
        return True

    def get_messages(self, limit: int = 10, unplayed_only: bool = False) -> List[Dict]:
        """メッセージ一覧を取得"""
        db_url = f"{self.db_url}/messages.json"
        response = requests.get(db_url)

        if response.status_code != 200:
            return []

        data = response.json()
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
        response = requests.get(audio_url)
        if response.status_code == 200:
            return response.content
        return None

    def mark_as_played(self, message_id: str) -> None:
        """メッセージを再生済みにマーク"""
        db_url = f"{self.db_url}/messages/{message_id}/played.json"
        requests.put(db_url, json=True)

    def update_message_text(self, message_id: str, text: str) -> bool:
        """メッセージのテキストを更新"""
        db_url = f"{self.db_url}/messages/{message_id}/text.json"
        response = requests.put(db_url, json=text)
        return response.status_code == 200

    def start_listening(self, poll_interval: float = 3.0) -> None:
        """新着メッセージの監視を開始"""
        self.running = True
        self.processed_ids = set()

        def poll_loop():
            # 既存メッセージを記録
            messages = self.get_messages(limit=20)
            for msg in messages:
                self.processed_ids.add(msg.get("id"))

            while self.running:
                try:
                    messages = self.get_messages(limit=15, unplayed_only=False)

                    for msg in reversed(messages):
                        msg_id = msg.get("id")

                        if msg_id in self.processed_ids:
                            continue

                        if self.on_message_received:
                            self.on_message_received(msg)

                        self.processed_ids.add(msg_id)
                        self.mark_as_played(msg_id)

                except Exception:
                    pass

                time.sleep(poll_interval)

        self.listener_thread = threading.Thread(target=poll_loop, daemon=True)
        self.listener_thread.start()

    def stop_listening(self) -> None:
        """監視を停止"""
        self.running = False
        if self.listener_thread:
            self.listener_thread.join(timeout=5)
