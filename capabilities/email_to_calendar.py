"""
メール送信時の予定自動登録

メール送信後に呼び出され、
予定情報（日時・場所・件名）が含まれていたら
自動的にカレンダーに追加する。
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from config import Config

logger = logging.getLogger("email_to_calendar")

# OpenAI API
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# 通知コールバック
_notify_callback: Optional[Callable] = None


def set_email_calendar_notify_callback(callback: Callable) -> None:
    """通知コールバックを設定"""
    global _notify_callback
    _notify_callback = callback


def _get_calendar_service():
    """カレンダーサービスを取得"""
    try:
        from .calendar import get_calendar_service
        return get_calendar_service()
    except ImportError:
        return None


def _get_openai_client() -> Optional[OpenAI]:
    """OpenAIクライアントを取得"""
    if not OPENAI_AVAILABLE:
        return None
    try:
        api_key = Config.get_api_key()
        return OpenAI(api_key=api_key)
    except Exception:
        return None


def _extract_schedule(to: str, subject: str, body: str) -> Optional[Dict[str, Any]]:
    """メールから予定情報を抽出"""
    client = _get_openai_client()
    if not client:
        return None

    today = datetime.now()

    prompt = f"""以下は自分が送信したメールです。自分が約束した予定をカレンダーに登録すべきか判断してください。

【登録すべき例】
- 「○日○時に会いましょう」「○日○時でお願いします」
- 打ち合わせ、会議、ミーティング、食事、飲み会の約束
- 具体的な日時（○月○日、○日○時、明日○時など）が明記されている

【登録しない例】
- 曖昧な日時（「来週あたり」「今度」「近いうち」）
- 期限やデッドライン（「○日までに提出します」は予定ではない）
- 過去の日付
- 相手に日程を聞いているだけ（「いつがいいですか？」）

今日は{today.strftime('%Y年%m月%d日')}です。

---
宛先: {to}
件名: {subject}
本文:
{body[:1500]}
---

予定情報がある場合は以下のJSON形式で返してください:
{{
  "has_schedule": true,
  "title": "予定のタイトル",
  "date": "YYYY-MM-DD",
  "time": "HH:MM",
  "location": "場所（わかる場合）",
  "duration_minutes": 60
}}

予定情報がない場合:
{{
  "has_schedule": false
}}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "あなたはメールから予定情報を抽出するアシスタントです。JSONのみを返してください。"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=300,
            response_format={"type": "json_object"}
        )

        result_text = response.choices[0].message.content
        result = json.loads(result_text)

        if result.get("has_schedule"):
            logger.info(f"予定を検出: {result}")
            return result
        return None

    except Exception as e:
        logger.error(f"予定抽出エラー: {e}")
        return None


def _add_to_calendar(schedule: Dict[str, Any], to: str, subject: str) -> bool:
    """カレンダーに予定を追加"""
    calendar_service = _get_calendar_service()
    if not calendar_service:
        logger.error("カレンダーサービスが利用できません")
        return False

    try:
        date_str = schedule.get("date", "")
        time_str = schedule.get("time", "10:00")

        try:
            start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            logger.error(f"日時パースエラー: {date_str} {time_str}")
            return False

        duration = schedule.get("duration_minutes", 60)
        end_dt = start_dt + timedelta(minutes=duration)

        title = schedule.get("title", subject)
        location = schedule.get("location", "")

        # 宛先から名前を抽出
        to_name = to.split('@')[0] if '@' in to else to

        event = {
            'summary': title,
            'location': location,
            'description': f"メール送信時に自動登録\n宛先: {to_name}",
            'start': {
                'dateTime': start_dt.isoformat(),
                'timeZone': 'Asia/Tokyo',
            },
            'end': {
                'dateTime': end_dt.isoformat(),
                'timeZone': 'Asia/Tokyo',
            },
        }

        calendar_service.events().insert(
            calendarId='primary', body=event
        ).execute()

        logger.info(f"カレンダーに追加: {title} @ {start_dt.strftime('%m/%d %H:%M')}")
        return True

    except Exception as e:
        logger.error(f"カレンダー追加エラー: {e}")
        return False


def check_and_add_schedule(to: str, subject: str, body: str) -> Optional[str]:
    """
    メール送信後に呼び出す。予定があればカレンダーに追加。

    Returns:
        追加した場合は通知メッセージ、なければNone
    """
    schedule = _extract_schedule(to, subject, body)

    if not schedule:
        return None

    if _add_to_calendar(schedule, to, subject):
        title = schedule.get("title", "予定")
        date_str = schedule.get("date", "")
        time_str = schedule.get("time", "")
        location = schedule.get("location", "")

        msg = f"{date_str} {time_str}の「{title}」をカレンダーに追加しました"
        if location:
            msg += f"（{location}）"

        return msg

    return None
