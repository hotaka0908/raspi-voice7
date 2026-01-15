"""
スケジュール系Capability

「覚える」に関する能力:
- アラーム設定・確認・削除
- 時間が来たら思い出して伝える
"""

import os
import json
import time
import asyncio
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable

from .base import Capability, CapabilityCategory, CapabilityResult
from config import Config


# アラーム状態管理
_alarms: List[Dict] = []
_alarm_next_id = 1
_alarm_thread: Optional[threading.Thread] = None
_alarm_notify_callback: Optional[Callable] = None
_running = True


def load_alarms() -> None:
    """保存されたアラームを読み込み"""
    global _alarms, _alarm_next_id

    try:
        if os.path.exists(Config.ALARM_FILE_PATH):
            with open(Config.ALARM_FILE_PATH, 'r') as f:
                data = json.load(f)
                _alarms = data.get('alarms', [])
                _alarm_next_id = data.get('next_id', 1)
    except Exception:
        _alarms = []
        _alarm_next_id = 1


def save_alarms() -> None:
    """アラームを保存"""
    global _alarms, _alarm_next_id

    try:
        os.makedirs(os.path.dirname(Config.ALARM_FILE_PATH), exist_ok=True)
        with open(Config.ALARM_FILE_PATH, 'w') as f:
            json.dump({'alarms': _alarms, 'next_id': _alarm_next_id}, f, ensure_ascii=False)
    except Exception:
        pass


def set_alarm_notify_callback(callback: Callable) -> None:
    """アラーム通知コールバックを設定"""
    global _alarm_notify_callback
    _alarm_notify_callback = callback


def stop_alarm_thread() -> None:
    """アラームスレッドを停止"""
    global _running
    _running = False


def _alarm_check_loop() -> None:
    """アラーム監視ループ（バックグラウンドスレッド）"""
    global _running, _alarms, _alarm_notify_callback

    last_triggered = {}

    while _running:
        try:
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            alarms_to_delete = []

            for alarm in _alarms:
                if not alarm.get("enabled", True):
                    continue

                alarm_id = alarm['id']
                alarm_time = alarm['time']

                # 同じ分に複数回鳴らないように
                trigger_key = f"{alarm_id}_{current_time}"
                if trigger_key in last_triggered:
                    continue

                if alarm_time == current_time:
                    last_triggered[trigger_key] = True

                    # コールバックで通知
                    if _alarm_notify_callback:
                        message = alarm.get('message', f"{alarm['label']}の時間です")
                        try:
                            _alarm_notify_callback(f"アラームです。{message}")
                        except Exception:
                            pass

                    alarms_to_delete.append(alarm_id)

            # 発動したアラームを削除
            if alarms_to_delete:
                for alarm_id in alarms_to_delete:
                    _alarms[:] = [a for a in _alarms if a['id'] != alarm_id]
                save_alarms()

            # 古い記録をクリア
            keys_to_remove = [k for k in last_triggered if not k.endswith(current_time)]
            for k in keys_to_remove:
                del last_triggered[k]

        except Exception:
            pass

        time.sleep(10)


def start_alarm_thread() -> None:
    """アラーム監視スレッドを開始"""
    global _alarm_thread, _running
    _running = True
    _alarm_thread = threading.Thread(target=_alarm_check_loop, daemon=True)
    _alarm_thread.start()


class AlarmSet(Capability):
    """アラーム設定"""

    @property
    def name(self) -> str:
        return "alarm_set"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.SCHEDULE

    @property
    def description(self) -> str:
        return """時間を覚えておいて知らせる。以下の場面で使う：
- 「7時に起こして」「30分後に教えて」「○時にアラーム」
- 時間に関する依頼があったとき自動で時刻を計算してセット"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "time": {
                    "type": "string",
                    "description": "時刻（HH:MM形式、例: 07:00, 14:30）"
                },
                "label": {
                    "type": "string",
                    "description": "ラベル（例: 起床、会議）"
                },
                "message": {
                    "type": "string",
                    "description": "読み上げメッセージ"
                }
            },
            "required": ["time"]
        }

    def execute(self, time_str: str, label: str = "アラーム",
                message: str = "") -> CapabilityResult:
        global _alarms, _alarm_next_id

        try:
            hour, minute = map(int, time_str.split(':'))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return CapabilityResult.fail("時刻が正しくありません")
        except Exception:
            return CapabilityResult.fail("時刻が正しくありません")

        alarm = {
            "id": _alarm_next_id,
            "time": time_str,
            "label": label,
            "message": message or f"{label}の時間です",
            "enabled": True,
            "created_at": datetime.now().isoformat()
        }

        _alarms.append(alarm)
        _alarm_next_id += 1
        save_alarms()

        return CapabilityResult.ok(f"{time_str}に覚えておきます")


class AlarmList(Capability):
    """アラーム一覧確認"""

    @property
    def name(self) -> str:
        return "alarm_list"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.SCHEDULE

    @property
    def description(self) -> str:
        return """覚えていることを確認。以下の場面で使う：
- 「アラーム確認」「何時にセットしてある？」
- 既存のアラームについて聞かれたとき"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self) -> CapabilityResult:
        if not _alarms:
            return CapabilityResult.ok("覚えていることはありません")

        items = []
        for alarm in _alarms:
            status = "有効" if alarm.get("enabled", True) else "無効"
            items.append(f"{alarm['id']}. {alarm['time']} - {alarm['label']} ({status})")

        return CapabilityResult.ok("覚えていること:\n" + "\n".join(items))


class AlarmDelete(Capability):
    """アラーム削除"""

    @property
    def name(self) -> str:
        return "alarm_delete"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.SCHEDULE

    @property
    def description(self) -> str:
        return """覚えていることを忘れる。以下の場面で使う：
- 「アラーム消して」「キャンセル」"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "alarm_id": {
                    "type": "integer",
                    "description": "アラームID（番号）"
                }
            },
            "required": ["alarm_id"]
        }

    def execute(self, alarm_id: int) -> CapabilityResult:
        global _alarms

        try:
            alarm_id = int(alarm_id)
        except Exception:
            return CapabilityResult.fail("番号を教えてください")

        for i, alarm in enumerate(_alarms):
            if alarm['id'] == alarm_id:
                deleted = _alarms.pop(i)
                save_alarms()
                return CapabilityResult.ok(f"{deleted['time']}の予定を忘れました")

        return CapabilityResult.fail("その予定は見つかりません")


# エクスポート
SCHEDULE_CAPABILITIES = [
    AlarmSet(),
    AlarmList(),
    AlarmDelete(),
]
