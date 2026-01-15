"""
カレンダー系Capability

「予定」に関する能力:
- Google Calendarから予定を取得
- 予定を追加・削除
"""

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .base import Capability, CapabilityCategory, CapabilityResult
from config import Config

# Google Calendar API
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    CALENDAR_AVAILABLE = True
except ImportError:
    CALENDAR_AVAILABLE = False


# Calendar APIスコープ
CALENDAR_SCOPES = ['https://www.googleapis.com/auth/calendar']

# カレンダー状態管理
_calendar_service = None


def init_calendar() -> bool:
    """Google Calendar API初期化"""
    global _calendar_service

    if not CALENDAR_AVAILABLE:
        return False

    creds = None
    token_path = os.path.join(Config.BASE_DIR, "calendar_token.json")

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, CALENDAR_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(Config.GMAIL_CREDENTIALS_PATH):
                return False
            flow = InstalledAppFlow.from_client_secrets_file(
                Config.GMAIL_CREDENTIALS_PATH, CALENDAR_SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(token_path, 'w') as token:
            token.write(creds.to_json())

    try:
        _calendar_service = build('calendar', 'v3', credentials=creds)
        return True
    except Exception:
        return False


def _parse_datetime(dt_str: str, default_date: datetime = None) -> Optional[datetime]:
    """日時文字列をパース"""
    if default_date is None:
        default_date = datetime.now()

    # 時刻のみ（HH:MM）
    if ':' in dt_str and len(dt_str) <= 5:
        try:
            hour, minute = map(int, dt_str.split(':'))
            return default_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except:
            pass

    # 日付+時刻のパターンを試行
    patterns = [
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%m/%d %H:%M",
        "%m-%d %H:%M",
    ]
    for pattern in patterns:
        try:
            return datetime.strptime(dt_str, pattern)
        except:
            continue

    return None


def _format_event_time(event: Dict) -> str:
    """イベントの時刻を整形"""
    start = event.get('start', {})
    if 'dateTime' in start:
        dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
        return dt.strftime("%H:%M")
    elif 'date' in start:
        return "終日"
    return ""


class CalendarList(Capability):
    """予定一覧を確認"""

    @property
    def name(self) -> str:
        return "calendar_list"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.SCHEDULE

    @property
    def description(self) -> str:
        return """予定を確認する。以下の場面で使う：
- 「今日の予定は？」「明日の予定」
- 「来週の予定を教えて」
- 「今週何かある？」

daysで何日分の予定を取得するか指定（デフォルト1日）"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "取得する日数（1=今日のみ、7=1週間）"
                }
            }
        }

    def execute(self, days: int = 1) -> CapabilityResult:
        if not _calendar_service:
            return CapabilityResult.fail("今は予定を確認できません")

        try:
            now = datetime.now()
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_time = start_of_day + timedelta(days=days)

            events_result = _calendar_service.events().list(
                calendarId='primary',
                timeMin=start_of_day.isoformat() + 'Z',
                timeMax=end_time.isoformat() + 'Z',
                maxResults=10,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])

            if not events:
                if days == 1:
                    return CapabilityResult.ok("今日の予定はありません")
                else:
                    return CapabilityResult.ok(f"今後{days}日間の予定はありません")

            result_lines = []
            current_date = None

            for event in events:
                # 日付を取得
                start = event.get('start', {})
                if 'dateTime' in start:
                    event_dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
                    event_date = event_dt.date()
                elif 'date' in start:
                    event_date = datetime.fromisoformat(start['date']).date()
                else:
                    continue

                # 日付が変わったら見出し追加
                if days > 1 and event_date != current_date:
                    current_date = event_date
                    if event_date == now.date():
                        result_lines.append("【今日】")
                    elif event_date == (now + timedelta(days=1)).date():
                        result_lines.append("【明日】")
                    else:
                        result_lines.append(f"【{event_date.strftime('%m/%d')}】")

                time_str = _format_event_time(event)
                summary = event.get('summary', '(タイトルなし)')
                result_lines.append(f"- {time_str} {summary}")

            return CapabilityResult.ok("\n".join(result_lines))

        except HttpError:
            return CapabilityResult.fail("今は予定を確認できません")


class CalendarAdd(Capability):
    """予定を追加"""

    @property
    def name(self) -> str:
        return "calendar_add"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.SCHEDULE

    @property
    def description(self) -> str:
        return """予定を追加する。以下の場面で使う：
- 「明日10時に会議を入れて」
- 「来週月曜に歯医者の予定を追加」
- 「15時からミーティング」"""

    @property
    def requires_confirmation(self) -> bool:
        return True

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "予定のタイトル"
                },
                "start_time": {
                    "type": "string",
                    "description": "開始時刻（HH:MM形式、または YYYY-MM-DD HH:MM）"
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "所要時間（分）。デフォルト60分"
                },
                "date": {
                    "type": "string",
                    "description": "日付（today, tomorrow, または YYYY-MM-DD）"
                }
            },
            "required": ["title", "start_time"]
        }

    def execute(self, title: str, start_time: str,
                duration_minutes: int = 60, date: str = "today") -> CapabilityResult:
        if not _calendar_service:
            return CapabilityResult.fail("今は予定を追加できません")

        try:
            # 日付を決定
            now = datetime.now()
            if date == "today":
                base_date = now
            elif date == "tomorrow":
                base_date = now + timedelta(days=1)
            else:
                try:
                    base_date = datetime.strptime(date, "%Y-%m-%d")
                except:
                    base_date = now

            # 開始時刻をパース
            start_dt = _parse_datetime(start_time, base_date)
            if not start_dt:
                return CapabilityResult.fail("時刻が正しくありません")

            end_dt = start_dt + timedelta(minutes=duration_minutes)

            event = {
                'summary': title,
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': 'Asia/Tokyo',
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': 'Asia/Tokyo',
                },
            }

            _calendar_service.events().insert(
                calendarId='primary', body=event
            ).execute()

            time_str = start_dt.strftime("%m/%d %H:%M")
            return CapabilityResult.ok(f"{time_str}に「{title}」を追加しました")

        except HttpError:
            return CapabilityResult.fail("今は予定を追加できません")


class CalendarDelete(Capability):
    """予定を削除"""

    @property
    def name(self) -> str:
        return "calendar_delete"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.SCHEDULE

    @property
    def description(self) -> str:
        return """予定を削除する。以下の場面で使う：
- 「今日の会議をキャンセル」
- 「明日の歯医者の予定を消して」

titleで削除する予定のタイトル（部分一致）を指定"""

    @property
    def requires_confirmation(self) -> bool:
        return True

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "削除する予定のタイトル（部分一致）"
                },
                "date": {
                    "type": "string",
                    "description": "日付（today, tomorrow, または YYYY-MM-DD）"
                }
            },
            "required": ["title"]
        }

    def execute(self, title: str, date: str = "today") -> CapabilityResult:
        if not _calendar_service:
            return CapabilityResult.fail("今は予定を削除できません")

        try:
            # 日付を決定
            now = datetime.now()
            if date == "today":
                base_date = now
            elif date == "tomorrow":
                base_date = now + timedelta(days=1)
            else:
                try:
                    base_date = datetime.strptime(date, "%Y-%m-%d")
                except:
                    base_date = now

            start_of_day = base_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day + timedelta(days=1)

            # 予定を検索
            events_result = _calendar_service.events().list(
                calendarId='primary',
                timeMin=start_of_day.isoformat() + 'Z',
                timeMax=end_of_day.isoformat() + 'Z',
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])

            # タイトルで検索
            for event in events:
                summary = event.get('summary', '')
                if title.lower() in summary.lower():
                    _calendar_service.events().delete(
                        calendarId='primary', eventId=event['id']
                    ).execute()
                    return CapabilityResult.ok(f"「{summary}」を削除しました")

            return CapabilityResult.fail("その予定は見つかりません")

        except HttpError:
            return CapabilityResult.fail("今は予定を削除できません")


# エクスポート
CALENDAR_CAPABILITIES = [
    CalendarList(),
    CalendarAdd(),
    CalendarDelete(),
]
