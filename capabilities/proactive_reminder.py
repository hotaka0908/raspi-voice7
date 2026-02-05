"""
プロアクティブリマインダー機能

カレンダー予定を監視し、Google Maps APIで移動時間を計算、
出発10分前にAIが自発的にリマインド。
リマインド後は画像比較でユーザーの移動を検知し、
動いていなければ再度リマインドする。
"""

import base64
import json
import logging
import threading
import time
import requests
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from config import Config

# ロガー設定
logger = logging.getLogger("proactive_reminder")

# Google Calendar API（calendar.pyから取得）
try:
    from .calendar import get_calendar_service, CALENDAR_AVAILABLE
except ImportError:
    def get_calendar_service():
        return None
    CALENDAR_AVAILABLE = False

# Firebase設定（REST API経由）
import os
from dotenv import load_dotenv

env_path = os.path.expanduser("~/.ai-necklace/.env")
load_dotenv(env_path)

FIREBASE_DATABASE_URL = os.getenv("FIREBASE_DATABASE_URL", "")

# Vision機能
try:
    from .vision import capture_image_raw, get_openai_client
    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False


@dataclass
class Location:
    """位置情報"""
    latitude: float
    longitude: float
    accuracy: float = 0.0
    timestamp: int = 0  # Unix timestamp (ms)
    device_id: str = ""


@dataclass
class UpcomingEvent:
    """予定データ"""
    event_id: str
    title: str
    start_time: datetime
    location: str  # 場所（住所または名前）
    location_coords: Optional[Location] = None  # ジオコーディング結果


@dataclass
class ReminderContext:
    """リマインダー状態管理"""
    event_id: str
    event_title: str
    departure_time: datetime
    reminded_count: int = 0
    last_remind_time: Optional[datetime] = None
    initial_image: Optional[bytes] = None
    movement_check_scheduled: bool = False


# グローバル状態
_reminder_thread: Optional[threading.Thread] = None
_reminder_notify_callback: Optional[Callable] = None
_running = True
_active_reminders: Dict[str, ReminderContext] = {}
_processed_events: Set[str] = set()  # 処理済みイベントID
_last_cleanup_date: Optional[str] = None  # 最後にクリーンアップした日付


def set_reminder_notify_callback(callback: Callable) -> None:
    """リマインド通知コールバックを設定"""
    global _reminder_notify_callback
    _reminder_notify_callback = callback


def stop_reminder_thread() -> None:
    """リマインダースレッドを停止"""
    global _running
    _running = False


class GoogleMapsClient:
    """Google Maps Directions APIクライアント"""

    def __init__(self):
        try:
            self.api_key = Config.get_google_maps_api_key()
        except ValueError:
            self.api_key = None
            logger.warning("Google Maps APIキーが設定されていません")

    def get_travel_time(self, origin: Location, destination: str,
                        mode: str = "transit") -> Optional[int]:
        """
        移動時間を取得（秒単位）

        Args:
            origin: 出発地点
            destination: 目的地（住所または場所名）
            mode: 移動手段（driving, walking, bicycling, transit）

        Returns:
            移動時間（秒）、取得失敗時はNone
        """
        if not self.api_key:
            return None

        try:
            url = "https://maps.googleapis.com/maps/api/directions/json"
            params = {
                "origin": f"{origin.latitude},{origin.longitude}",
                "destination": destination,
                "mode": mode,
                "language": "ja",
                "key": self.api_key
            }

            response = requests.get(url, params=params, timeout=10)
            data = response.json()

            if data.get("status") != "OK":
                logger.warning(f"Directions API エラー: {data.get('status')}")
                return None

            routes = data.get("routes", [])
            if not routes:
                return None

            legs = routes[0].get("legs", [])
            if not legs:
                return None

            duration = legs[0].get("duration", {})
            return duration.get("value")  # 秒単位

        except Exception as e:
            logger.error(f"移動時間取得エラー: {e}")
            return None


class FirebaseLocationClient:
    """Firebaseから位置情報を取得（REST API経由）"""

    def __init__(self):
        self.db_url = FIREBASE_DATABASE_URL

    def get_current_location(self) -> Optional[Location]:
        """現在の位置情報を取得"""
        if not self.db_url:
            logger.warning("FIREBASE_DATABASE_URL が設定されていません")
            return None

        try:
            url = f"{self.db_url}/user_location.json"
            response = requests.get(url, timeout=10)

            if response.status_code != 200:
                logger.warning(f"Firebase位置情報取得失敗: {response.status_code}")
                return None

            data = response.json()
            if not data:
                return None

            # タイムスタンプチェック（10分以内）
            timestamp = data.get("timestamp", 0)
            now = int(time.time() * 1000)
            if now - timestamp > 10 * 60 * 1000:  # 10分
                logger.debug("位置情報が古いです（10分以上前）")
                return None

            return Location(
                latitude=data.get("latitude", 0),
                longitude=data.get("longitude", 0),
                accuracy=data.get("accuracy", 0),
                timestamp=timestamp,
                device_id=data.get("deviceId", "")
            )

        except Exception as e:
            logger.error(f"位置情報取得エラー: {e}")
            return None


class MovementDetector:
    """OpenAI Vision APIで画像比較による移動検知"""

    def compare_images(self, image1: bytes, image2: bytes) -> Dict[str, Any]:
        """
        2枚の画像を比較して移動を検知

        Returns:
            {moved: bool, confidence: float, reason: str}
        """
        if not VISION_AVAILABLE:
            return {"moved": False, "confidence": 0.0, "reason": "Vision API利用不可"}

        try:
            client = get_openai_client()

            # Base64エンコード
            image1_b64 = base64.b64encode(image1).decode('utf-8')
            image2_b64 = base64.b64encode(image2).decode('utf-8')

            prompt = """これは同じカメラで撮影された2枚の写真です。
撮影者が別の場所に移動したかどうかを判定してください。

判定基準:
- 背景の大きな変化（部屋が変わった、屋外に出たなど）があれば「移動した」
- 小さな角度変化、照明の変化、人の動きだけでは「移動していない」

以下のJSON形式で回答してください:
{
  "moved": true/false,
  "confidence": 0.0-1.0,
  "reason": "理由（短く）"
}"""

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image1_b64}",
                                    "detail": "low"
                                }
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image2_b64}",
                                    "detail": "low"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=200,
                response_format={"type": "json_object"}
            )

            result_text = response.choices[0].message.content
            result = json.loads(result_text)

            return {
                "moved": result.get("moved", False),
                "confidence": result.get("confidence", 0.0),
                "reason": result.get("reason", "")
            }

        except Exception as e:
            logger.error(f"画像比較エラー: {e}")
            return {"moved": False, "confidence": 0.0, "reason": f"エラー: {e}"}


class ProactiveReminderManager:
    """プロアクティブリマインダーの管理"""

    def __init__(self):
        self.maps_client = GoogleMapsClient()
        self.location_client = FirebaseLocationClient()
        self.movement_detector = MovementDetector()

    def get_upcoming_events(self, hours: int = 3) -> List[UpcomingEvent]:
        """今後N時間以内の場所付き予定を取得"""
        calendar_service = get_calendar_service()
        if not CALENDAR_AVAILABLE or not calendar_service:
            return []

        try:
            # UTCで現在時刻を取得（Google Calendar APIはRFC3339形式を要求）
            now_utc = datetime.now(timezone.utc)
            time_max_utc = now_utc + timedelta(hours=hours)

            events_result = calendar_service.events().list(
                calendarId='primary',
                timeMin=now_utc.isoformat(),
                timeMax=time_max_utc.isoformat(),
                maxResults=10,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])
            result = []

            for event in events:
                location = event.get('location', '')
                if not location:
                    continue  # 場所がない予定はスキップ

                start = event.get('start', {})
                if 'dateTime' in start:
                    # タイムゾーン付きの日時を取得し、ローカル時間に変換
                    dt_str = start['dateTime']
                    if dt_str.endswith('Z'):
                        dt_str = dt_str[:-1] + '+00:00'
                    start_time_tz = datetime.fromisoformat(dt_str)
                    # ローカル時間（naive datetime）に変換
                    start_time = start_time_tz.astimezone().replace(tzinfo=None)
                else:
                    continue  # 終日イベントはスキップ

                result.append(UpcomingEvent(
                    event_id=event.get('id', ''),
                    title=event.get('summary', '(タイトルなし)'),
                    start_time=start_time,
                    location=location
                ))

            return result

        except Exception as e:
            logger.error(f"予定取得エラー: {e}")
            return []

    def calculate_departure_time(self, event: UpcomingEvent,
                                 current_location: Location) -> Optional[datetime]:
        """出発時刻を計算"""
        # 移動時間を取得
        travel_time_sec = self.maps_client.get_travel_time(
            current_location, event.location
        )

        if travel_time_sec is None:
            # APIエラー時はデフォルト30分とする
            travel_time_sec = 30 * 60

        # 出発時刻 = 予定開始時刻 - 移動時間
        travel_time = timedelta(seconds=travel_time_sec)
        departure_time = event.start_time - travel_time

        logger.info(
            f"予定「{event.title}」: 開始 {event.start_time.strftime('%H:%M')}, "
            f"移動 {travel_time_sec // 60}分, 出発 {departure_time.strftime('%H:%M')}"
        )

        return departure_time

    def should_remind(self, departure_time: datetime) -> bool:
        """リマインドすべきかどうか"""
        now = datetime.now()
        time_until_departure = (departure_time - now).total_seconds()
        advance_seconds = Config.REMINDER_ADVANCE_MINUTES * 60

        # 出発N分前からリマインド開始
        return 0 < time_until_departure <= advance_seconds

    def send_reminder(self, event: UpcomingEvent, context: ReminderContext) -> bool:
        """リマインドを送信"""
        global _reminder_notify_callback

        if not _reminder_notify_callback:
            return False

        minutes_until = int(
            (context.departure_time - datetime.now()).total_seconds() / 60
        )

        if context.reminded_count == 0:
            message = f"「{event.title}」の時間が近づいています。あと{minutes_until}分ほどで出発の時間です。"
        else:
            message = f"まだ移動を確認できていません。「{event.title}」に間に合うよう、そろそろ出発してください。"

        try:
            _reminder_notify_callback(message)
            context.reminded_count += 1
            context.last_remind_time = datetime.now()
            logger.info(f"リマインド送信: {message}")
            return True
        except Exception as e:
            logger.error(f"リマインド送信エラー: {e}")
            return False

    def check_movement(self, context: ReminderContext) -> bool:
        """移動を検知"""
        if not VISION_AVAILABLE:
            return False

        # 現在の画像を撮影
        current_image = capture_image_raw()
        if not current_image:
            logger.warning("カメラ撮影失敗（移動検知）")
            return False

        if context.initial_image is None:
            # 初回撮影
            context.initial_image = current_image
            return False

        # 画像比較
        result = self.movement_detector.compare_images(
            context.initial_image, current_image
        )

        logger.info(
            f"移動検知結果: moved={result['moved']}, "
            f"confidence={result['confidence']:.2f}, reason={result['reason']}"
        )

        # confidence >= 0.7 で移動と判定
        return result.get("moved", False) and result.get("confidence", 0) >= 0.7


def _reminder_check_loop() -> None:
    """リマインダー監視ループ（バックグラウンドスレッド）"""
    global _running, _active_reminders, _processed_events

    manager = ProactiveReminderManager()

    while _running:
        try:
            now = datetime.now()

            # 現在位置を取得
            current_location = manager.location_client.get_current_location()
            if not current_location:
                logger.debug("位置情報が取得できません（スキップ）")
                time.sleep(Config.REMINDER_CHECK_INTERVAL)
                continue

            # 今後の予定を取得
            events = manager.get_upcoming_events(Config.REMINDER_LOOKAHEAD_HOURS)

            for event in events:
                # 処理済みチェック
                if event.event_id in _processed_events:
                    continue

                # 出発時刻を計算
                departure_time = manager.calculate_departure_time(
                    event, current_location
                )
                if not departure_time:
                    continue

                # 出発時刻が過ぎた場合
                if departure_time < now:
                    _processed_events.add(event.event_id)
                    logger.info(f"予定「{event.title}」の出発時刻が過ぎました")
                    continue

                # リマインドすべきかチェック
                if manager.should_remind(departure_time):
                    # アクティブリマインダーを作成/取得
                    if event.event_id not in _active_reminders:
                        _active_reminders[event.event_id] = ReminderContext(
                            event_id=event.event_id,
                            event_title=event.title,
                            departure_time=departure_time
                        )

                    context = _active_reminders[event.event_id]

                    # 最大リトライ回数チェック
                    if context.reminded_count >= Config.REMINDER_MAX_RETRIES:
                        logger.info(f"予定「{event.title}」の最大リマインド回数に達しました")
                        _processed_events.add(event.event_id)
                        del _active_reminders[event.event_id]
                        continue

                    # 初回リマインドまたは移動チェック後
                    if context.reminded_count == 0:
                        # 初回リマインド
                        if manager.send_reminder(event, context):
                            # 初回画像撮影
                            if VISION_AVAILABLE:
                                context.initial_image = capture_image_raw()
                                context.movement_check_scheduled = True
                    elif context.movement_check_scheduled:
                        # 移動チェック（前回リマインドから30秒後）
                        if context.last_remind_time:
                            elapsed = (now - context.last_remind_time).total_seconds()
                            if elapsed >= Config.REMINDER_MOVEMENT_CHECK_DELAY:
                                if manager.check_movement(context):
                                    # 移動を検知 → 完了
                                    logger.info(f"予定「{event.title}」: 移動を検知しました")
                                    _processed_events.add(event.event_id)
                                    del _active_reminders[event.event_id]
                                else:
                                    # 移動していない → 再リマインド
                                    manager.send_reminder(event, context)
                                    # 次の移動チェック用に再撮影
                                    if VISION_AVAILABLE:
                                        context.initial_image = capture_image_raw()

            # 古い処理済みイベントをクリーンアップ（日付が変わったらリセット）
            global _last_cleanup_date
            today = now.strftime("%Y-%m-%d")
            if _last_cleanup_date != today:
                _processed_events.clear()
                _active_reminders.clear()
                _last_cleanup_date = today
                logger.info("処理済みイベントリストをクリア（日付変更）")

        except Exception as e:
            logger.error(f"リマインダーチェックエラー: {e}")

        time.sleep(Config.REMINDER_CHECK_INTERVAL)


def start_reminder_thread() -> None:
    """リマインダー監視スレッドを開始"""
    global _reminder_thread, _running
    _running = True
    _reminder_thread = threading.Thread(target=_reminder_check_loop, daemon=True)
    _reminder_thread.start()
    logger.info("プロアクティブリマインダースレッド開始")


# エクスポート
__all__ = [
    'start_reminder_thread',
    'stop_reminder_thread',
    'set_reminder_notify_callback',
    'ProactiveReminderManager',
]
