"""
メモリ系Capability

「記録する」に関する能力:
- ライフログ（自動撮影）
- 継続的な記録と振り返り
"""

import os
import subprocess
import threading
import time
import io
import wave
import numpy as np
from datetime import datetime
from typing import Any, Dict, Optional, Callable

from .base import Capability, CapabilityCategory, CapabilityResult
from .vision import camera_lock
from config import Config


# ライフログ状態管理
_lifelog_enabled = False
_lifelog_thread: Optional[threading.Thread] = None
_lifelog_photo_count = 0
_running = True

# Firebase連携（オプション）
_firebase_messenger = None

# オーディオ再生コールバック
_play_audio_callback: Optional[Callable] = None


def set_firebase_messenger(messenger) -> None:
    """Firebaseメッセンジャーを設定"""
    global _firebase_messenger
    _firebase_messenger = messenger


def set_play_audio_callback(callback: Callable) -> None:
    """音声再生コールバックを設定"""
    global _play_audio_callback
    _play_audio_callback = callback


def stop_lifelog_thread() -> None:
    """ライフログスレッドを停止"""
    global _running
    _running = False


def _generate_shutter_sound() -> Optional[bytes]:
    """シャッター音を生成"""
    try:
        sample_rate = 48000
        duration = 0.08

        samples = int(sample_rate * duration)
        t = np.linspace(0, duration, samples, False)

        noise = np.random.uniform(-1, 1, samples)
        click = np.sin(2 * np.pi * 2000 * t)
        envelope = np.exp(-t * 50)
        sound = ((noise * 0.3 + click * 0.7) * envelope * 0.4 * 32767).astype(np.int16)

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(sound.tobytes())

        return wav_buffer.getvalue()
    except Exception:
        return None


def _capture_lifelog_photo() -> bool:
    """ライフログ用の写真を撮影"""
    global _lifelog_photo_count, _firebase_messenger, _play_audio_callback

    # カメラロックを即座に試行（ユーザー操作を優先）
    if not camera_lock.acquire(blocking=False):
        return False

    try:
        # 今日のディレクトリを作成
        today = datetime.now().strftime("%Y-%m-%d")
        lifelog_dir = os.path.join(Config.LIFELOG_DIR, today)
        os.makedirs(lifelog_dir, exist_ok=True)

        # タイムスタンプ付きファイル名
        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"{timestamp}.jpg"
        image_path = os.path.join(lifelog_dir, filename)

        # 撮影
        result = subprocess.run(
            ["rpicam-still", "-o", image_path, "-t", "500",
             "--width", "1280", "--height", "960"],
            capture_output=True, timeout=10
        )

        if result.returncode == 0:
            _lifelog_photo_count += 1

            # シャッター音
            if _play_audio_callback:
                shutter = _generate_shutter_sound()
                if shutter:
                    _play_audio_callback(shutter)

            # Firebaseにアップロード
            if _firebase_messenger:
                try:
                    with open(image_path, "rb") as f:
                        photo_data = f.read()
                    _firebase_messenger.upload_lifelog_photo(photo_data, today, timestamp)
                except Exception:
                    pass

            return True
        else:
            return False

    except Exception:
        return False
    finally:
        camera_lock.release()


def _lifelog_thread_func() -> None:
    """ライフログ撮影のバックグラウンドスレッド"""
    global _running, _lifelog_enabled, _lifelog_photo_count

    last_date = datetime.now().strftime("%Y-%m-%d")
    retry_interval = 30

    while _running:
        if _lifelog_enabled:
            # 日付変更でカウントリセット
            current_date = datetime.now().strftime("%Y-%m-%d")
            if current_date != last_date:
                _lifelog_photo_count = 0
                last_date = current_date

            success = _capture_lifelog_photo()
            wait_time = Config.LIFELOG_INTERVAL if success else retry_interval
        else:
            wait_time = Config.LIFELOG_INTERVAL

        # 待機（1秒ごとにチェック）
        for _ in range(wait_time):
            if not _running:
                break
            if _lifelog_enabled:
                time.sleep(1)
            else:
                time.sleep(5)
                break


def start_lifelog_thread() -> None:
    """ライフログスレッドを開始"""
    global _lifelog_thread, _running
    _running = True
    if _lifelog_thread is None or not _lifelog_thread.is_alive():
        _lifelog_thread = threading.Thread(target=_lifelog_thread_func, daemon=True)
        _lifelog_thread.start()


class LifelogStart(Capability):
    """ライフログ開始"""

    @property
    def name(self) -> str:
        return "lifelog_start"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.MEMORY

    @property
    def description(self) -> str:
        return """自動で記録を始める。以下の場面で使う：
- 「ライフログ開始」「記録始めて」「自動撮影ON」"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self) -> CapabilityResult:
        global _lifelog_enabled

        if _lifelog_enabled:
            return CapabilityResult.ok("もう記録中です")

        _lifelog_enabled = True
        start_lifelog_thread()

        interval_min = Config.LIFELOG_INTERVAL // 60
        return CapabilityResult.ok(f"記録を始めます。{interval_min}分ごとに撮影します")


class LifelogStop(Capability):
    """ライフログ停止"""

    @property
    def name(self) -> str:
        return "lifelog_stop"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.MEMORY

    @property
    def description(self) -> str:
        return """自動記録を止める。以下の場面で使う：
- 「ライフログ停止」「記録終了」「自動撮影OFF」"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self) -> CapabilityResult:
        global _lifelog_enabled

        if not _lifelog_enabled:
            return CapabilityResult.ok("記録していませんでした")

        _lifelog_enabled = False
        return CapabilityResult.ok("記録を止めました")


class LifelogStatus(Capability):
    """ライフログステータス確認"""

    @property
    def name(self) -> str:
        return "lifelog_status"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.MEMORY

    @property
    def description(self) -> str:
        return """記録の状況を確認。以下の場面で使う：
- 「今日何枚撮った？」「記録の状態は？」"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self) -> CapabilityResult:
        global _lifelog_enabled

        status = "記録中" if _lifelog_enabled else "停止中"
        today = datetime.now().strftime("%Y-%m-%d")
        lifelog_dir = os.path.join(Config.LIFELOG_DIR, today)

        # 実際のファイル数をカウント
        actual_count = 0
        if os.path.exists(lifelog_dir):
            actual_count = len([f for f in os.listdir(lifelog_dir) if f.endswith('.jpg')])

        return CapabilityResult.ok(f"今日は{actual_count}枚撮影しました。{status}です")


# エクスポート
MEMORY_CAPABILITIES = [
    LifelogStart(),
    LifelogStop(),
    LifelogStatus(),
]
