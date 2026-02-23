"""
プロアクティブメール→カレンダー登録機能

バックグラウンドでメールを定期チェックし、
予定情報（日時・場所・件名）が含まれていたら
自動的にカレンダーに追加する。
"""

import base64
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set

from config import Config

# ロガー設定
logger = logging.getLogger("proactive_email_calendar")

# Gmail API
try:
    from googleapiclient.errors import HttpError
    GMAIL_AVAILABLE = True
except ImportError:
    GMAIL_AVAILABLE = False

# OpenAI API
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# グローバル状態
_email_calendar_thread: Optional[threading.Thread] = None
_notify_callback: Optional[Callable] = None
_running = True
_processed_email_ids: Set[str] = set()
_processed_ids_file = os.path.join(Config.BASE_DIR, "processed_email_ids.json")


def set_email_calendar_notify_callback(callback: Callable) -> None:
    """通知コールバックを設定"""
    global _notify_callback
    _notify_callback = callback


def stop_email_calendar_thread() -> None:
    """スレッドを停止"""
    global _running
    _running = False


def _load_processed_ids() -> None:
    """処理済みメールIDを読み込む"""
    global _processed_email_ids
    try:
        if os.path.exists(_processed_ids_file):
            with open(_processed_ids_file, 'r') as f:
                data = json.load(f)
                _processed_email_ids = set(data.get("ids", []))
                logger.info(f"処理済みメールID {len(_processed_email_ids)}件を読み込み")
    except Exception as e:
        logger.error(f"処理済みID読み込みエラー: {e}")
        _processed_email_ids = set()


def _save_processed_ids() -> None:
    """処理済みメールIDを保存"""
    try:
        with open(_processed_ids_file, 'w') as f:
            json.dump({"ids": list(_processed_email_ids)}, f)
    except Exception as e:
        logger.error(f"処理済みID保存エラー: {e}")


def _get_gmail_service():
    """Gmailサービスを取得"""
    try:
        from .communication import _gmail_service
        return _gmail_service
    except ImportError:
        return None


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
        return OpenAI(api_key=Config.OPENAI_API_KEY)
    except Exception:
        return None


def _fetch_recent_emails(max_results: int = 10) -> List[Dict[str, Any]]:
    """最近の送信済みメールを取得"""
    gmail_service = _get_gmail_service()
    if not gmail_service:
        return []

    try:
        # 過去24時間の送信済みメールを取得
        results = gmail_service.users().messages().list(
            userId='me',
            q="in:sent newer_than:1d",
            maxResults=max_results
        ).execute()

        messages = results.get('messages', [])
        emails = []

        for msg in messages:
            msg_id = msg['id']

            # 処理済みならスキップ
            if msg_id in _processed_email_ids:
                continue

            # メール詳細を取得
            msg_detail = gmail_service.users().messages().get(
                userId='me', id=msg_id, format='full'
            ).execute()

            headers = {h['name']: h['value']
                      for h in msg_detail.get('payload', {}).get('headers', [])}

            # 本文を取得
            body = ""
            payload = msg_detail.get('payload', {})

            if 'body' in payload and payload['body'].get('data'):
                body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')
            elif 'parts' in payload:
                for part in payload['parts']:
                    if part.get('mimeType') == 'text/plain' and part.get('body', {}).get('data'):
                        body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                        break

            # 本文を適度な長さに制限
            if len(body) > 2000:
                body = body[:2000]

            from_header = headers.get('From', '不明')
            from_match = re.match(r'(.+?)\s*<', from_header)
            from_name = from_match.group(1).strip() if from_match else from_header.split('@')[0]

            emails.append({
                'id': msg_id,
                'from': from_name,
                'subject': headers.get('Subject', '(件名なし)'),
                'body': body,
                'date': headers.get('Date', '')
            })

        return emails

    except HttpError as e:
        logger.error(f"メール取得エラー: {e}")
        return []


def _extract_schedule_from_email(email: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """メールから予定情報を抽出（OpenAI使用）"""
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
宛先: {email['from']}
件名: {email['subject']}
本文:
{email['body']}
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


def _add_to_calendar(schedule: Dict[str, Any], email: Dict[str, Any]) -> bool:
    """カレンダーに予定を追加"""
    calendar_service = _get_calendar_service()
    if not calendar_service:
        logger.error("カレンダーサービスが利用できません")
        return False

    try:
        # 日時をパース
        date_str = schedule.get("date", "")
        time_str = schedule.get("time", "10:00")

        try:
            start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            logger.error(f"日時パースエラー: {date_str} {time_str}")
            return False

        duration = schedule.get("duration_minutes", 60)
        end_dt = start_dt + timedelta(minutes=duration)

        # イベント作成
        title = schedule.get("title", email['subject'])
        location = schedule.get("location", "")

        event = {
            'summary': title,
            'location': location,
            'description': f"メールから自動登録\n送信者: {email['from']}\n件名: {email['subject']}",
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


def _notify_user(message: str) -> None:
    """ユーザーに通知"""
    global _notify_callback
    if _notify_callback:
        try:
            _notify_callback(message)
        except Exception as e:
            logger.error(f"通知エラー: {e}")


def _email_calendar_check_loop() -> None:
    """メールチェックループ（バックグラウンドスレッド）"""
    global _running, _processed_email_ids

    # 処理済みIDを読み込み
    _load_processed_ids()

    # 初回は少し待機（他のサービス初期化待ち）
    time.sleep(30)

    while _running:
        try:
            # 新着メールを取得
            emails = _fetch_recent_emails(max_results=5)

            for email in emails:
                # 予定情報を抽出
                schedule = _extract_schedule_from_email(email)

                if schedule:
                    # カレンダーに追加
                    if _add_to_calendar(schedule, email):
                        # ユーザーに通知
                        title = schedule.get("title", "予定")
                        date_str = schedule.get("date", "")
                        time_str = schedule.get("time", "")
                        location = schedule.get("location", "")

                        msg = f"メールから予定を追加しました。{date_str} {time_str}に「{title}」"
                        if location:
                            msg += f"、場所は{location}"

                        _notify_user(msg)

                # 処理済みとしてマーク
                _processed_email_ids.add(email['id'])

            # 処理済みIDを保存
            if emails:
                _save_processed_ids()

            # 古いIDをクリーンアップ（1000件以上なら古いものを削除）
            if len(_processed_email_ids) > 1000:
                _processed_email_ids = set(list(_processed_email_ids)[-500:])
                _save_processed_ids()

        except Exception as e:
            logger.error(f"メールチェックエラー: {e}")

        # 5分間隔でチェック
        time.sleep(300)


def start_email_calendar_thread() -> None:
    """メール→カレンダー監視スレッドを開始"""
    global _email_calendar_thread, _running
    _running = True
    _email_calendar_thread = threading.Thread(target=_email_calendar_check_loop, daemon=True)
    _email_calendar_thread.start()
    logger.info("プロアクティブメール→カレンダースレッド開始")


# エクスポート
__all__ = [
    'start_email_calendar_thread',
    'stop_email_calendar_thread',
    'set_email_calendar_notify_callback',
]
