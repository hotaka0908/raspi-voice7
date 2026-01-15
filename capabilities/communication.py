"""
コミュニケーション系Capability

「送る」に関する能力:
- メールの確認・送信・返信
- スマホへの音声/写真メッセージ送信
"""

import os
import re
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import Capability, CapabilityCategory, CapabilityResult
from .vision import capture_image_raw
from config import Config

# Gmail API
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GMAIL_AVAILABLE = True
except ImportError:
    GMAIL_AVAILABLE = False

# Firebase
try:
    from core.firebase_voice import FirebaseVoiceMessenger
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False


# Gmail状態管理
_gmail_service = None
_last_email_list: List[Dict] = []

# Firebase状態管理
_firebase_messenger = None


def init_gmail() -> bool:
    """Gmail API初期化"""
    global _gmail_service

    if not GMAIL_AVAILABLE:
        return False

    creds = None
    if os.path.exists(Config.GMAIL_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(
            Config.GMAIL_TOKEN_PATH, Config.GMAIL_SCOPES
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(Config.GMAIL_CREDENTIALS_PATH):
                return False
            flow = InstalledAppFlow.from_client_secrets_file(
                Config.GMAIL_CREDENTIALS_PATH, Config.GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)

        os.makedirs(os.path.dirname(Config.GMAIL_TOKEN_PATH), exist_ok=True)
        with open(Config.GMAIL_TOKEN_PATH, 'w') as token:
            token.write(creds.to_json())

    try:
        _gmail_service = build('gmail', 'v1', credentials=creds)
        return True
    except Exception:
        return False


def init_firebase(on_message_callback=None) -> bool:
    """Firebase初期化"""
    global _firebase_messenger

    if not FIREBASE_AVAILABLE:
        return False

    try:
        _firebase_messenger = FirebaseVoiceMessenger(
            device_id="raspi",
            on_message_received=on_message_callback
        )
        _firebase_messenger.start_listening(poll_interval=1.5)
        return True
    except Exception:
        return False


def get_firebase_messenger():
    """Firebaseメッセンジャーを取得"""
    return _firebase_messenger


class GmailList(Capability):
    """メール一覧を確認"""

    @property
    def name(self) -> str:
        return "gmail_list"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.COMMUNICATION

    @property
    def description(self) -> str:
        return """メールを確認する。以下の場面で使う：
- 「メールある？」「新着メールは？」「メール来てる？」
- 「○○さんからメール来てる？」（from:で検索）
- メールについて話題が出たとき"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "検索クエリ（例: is:unread, from:xxx@gmail.com）"
                },
                "max_results": {
                    "type": "integer",
                    "description": "取得件数（デフォルト5）"
                }
            }
        }

    def execute(self, query: str = "is:unread", max_results: int = 5) -> CapabilityResult:
        global _last_email_list

        if not _gmail_service:
            return CapabilityResult.fail("今はメールを確認できません")

        try:
            results = _gmail_service.users().messages().list(
                userId='me', q=query, maxResults=max_results
            ).execute()

            messages = results.get('messages', [])
            if not messages:
                return CapabilityResult.ok("新しいメールはありません")

            email_list = []
            _last_email_list = []

            for i, msg in enumerate(messages, 1):
                msg_detail = _gmail_service.users().messages().get(
                    userId='me', id=msg['id'], format='metadata',
                    metadataHeaders=['From', 'Subject', 'Date']
                ).execute()

                headers = {h['name']: h['value']
                          for h in msg_detail.get('payload', {}).get('headers', [])}
                from_header = headers.get('From', '不明')
                from_match = re.match(r'(.+?)\s*<', from_header)
                from_name = from_match.group(1).strip() if from_match else from_header.split('@')[0]

                email_info = {
                    'id': msg['id'],
                    'from': from_name,
                    'from_email': from_header,
                    'subject': headers.get('Subject', '(件名なし)'),
                }
                _last_email_list.append(email_info)
                email_list.append(f"{i}. {from_name}さんから: {email_info['subject']}")

            return CapabilityResult.ok("メール一覧:\n" + "\n".join(email_list))

        except HttpError:
            return CapabilityResult.fail("今はメールを確認できません")


class GmailRead(Capability):
    """メール本文を読む"""

    @property
    def name(self) -> str:
        return "gmail_read"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.COMMUNICATION

    @property
    def description(self) -> str:
        return """メールの本文を読む。以下の場面で使う：
- 「1番目のメールを読んで」「さっきのメール詳しく」
- gmail_listの後、特定のメールについて聞かれたとき"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "メールID（番号: 1, 2, 3など）"
                }
            },
            "required": ["message_id"]
        }

    def execute(self, message_id: str) -> CapabilityResult:
        if not _gmail_service:
            return CapabilityResult.fail("今はメールを読めません")

        # 番号で指定された場合
        if isinstance(message_id, int) or (isinstance(message_id, str) and message_id.isdigit()):
            idx = int(message_id) - 1
            if 0 <= idx < len(_last_email_list):
                message_id = _last_email_list[idx]['id']
            else:
                return CapabilityResult.fail("そのメールは見つかりません")

        try:
            msg = _gmail_service.users().messages().get(
                userId='me', id=message_id, format='full'
            ).execute()

            headers = {h['name']: h['value']
                      for h in msg.get('payload', {}).get('headers', [])}
            body = ""
            payload = msg.get('payload', {})

            if 'body' in payload and payload['body'].get('data'):
                body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')
            elif 'parts' in payload:
                for part in payload['parts']:
                    if part.get('mimeType') == 'text/plain' and part.get('body', {}).get('data'):
                        body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                        break

            if len(body) > 500:
                body = body[:500] + "...(以下省略)"

            from_header = headers.get('From', '不明')
            from_match = re.match(r'(.+?)\s*<', from_header)
            from_name = from_match.group(1).strip() if from_match else from_header

            return CapabilityResult.ok(
                f"送信者: {from_name}\n件名: {headers.get('Subject', '(件名なし)')}\n\n本文:\n{body}"
            )

        except HttpError:
            return CapabilityResult.fail("今はメールを読めません")


class GmailSend(Capability):
    """新規メール送信"""

    @property
    def name(self) -> str:
        return "gmail_send"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.COMMUNICATION

    @property
    def description(self) -> str:
        return """メールを送る。以下の場面で使う：
- 「○○さんにメール送って」「メールを書いて」
- 宛先・件名・本文を確認してから送信"""

    @property
    def requires_confirmation(self) -> bool:
        return True  # 送信は確認が必要

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "宛先メールアドレス"},
                "subject": {"type": "string", "description": "件名"},
                "body": {"type": "string", "description": "本文"}
            },
            "required": ["to", "subject", "body"]
        }

    def execute(self, to: str, subject: str, body: str) -> CapabilityResult:
        if not _gmail_service:
            return CapabilityResult.fail("今はメールを送れません")

        try:
            message = MIMEText(body)
            message['to'] = to
            message['subject'] = subject
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

            _gmail_service.users().messages().send(
                userId='me', body={'raw': raw}
            ).execute()

            to_name = to.split('@')[0]
            return CapabilityResult.ok(f"{to_name}さんに送りました")

        except HttpError:
            return CapabilityResult.fail("今はメールを送れません")


class GmailReply(Capability):
    """メール返信"""

    @property
    def name(self) -> str:
        return "gmail_reply"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.COMMUNICATION

    @property
    def description(self) -> str:
        return """メールに返信する。以下の場面で使う：
- 「返信して」「了解と返しておいて」
- 直前に読んだメールに対する返信を依頼されたとき"""

    @property
    def requires_confirmation(self) -> bool:
        return True

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "返信するメールの番号（1, 2, 3など）"
                },
                "body": {"type": "string", "description": "返信本文"}
            },
            "required": ["message_id", "body"]
        }

    def execute(self, message_id: str, body: str) -> CapabilityResult:
        if not _gmail_service:
            return CapabilityResult.fail("今は返信できません")

        # 番号で指定された場合
        to_email = None
        if isinstance(message_id, int) or (isinstance(message_id, str) and message_id.isdigit()):
            idx = int(message_id) - 1
            if 0 <= idx < len(_last_email_list):
                actual_id = _last_email_list[idx]['id']
                to_email = _last_email_list[idx].get('from_email')
            else:
                return CapabilityResult.fail("そのメールは見つかりません")
        else:
            actual_id = message_id

        try:
            original = _gmail_service.users().messages().get(
                userId='me', id=actual_id, format='metadata',
                metadataHeaders=['From', 'Subject', 'Message-ID', 'References', 'Reply-To']
            ).execute()

            headers = {h['name']: h['value']
                      for h in original.get('payload', {}).get('headers', [])}
            to_raw = to_email or headers.get('Reply-To') or headers.get('From', '')

            match = re.search(r'<([^>]+)>', to_raw)
            to = match.group(1) if match else to_raw.strip()

            subject = headers.get('Subject', '')
            if not subject.startswith('Re:'):
                subject = 'Re: ' + subject

            thread_id = original.get('threadId')

            message = MIMEText(body)
            message['to'] = to
            message['subject'] = subject

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            _gmail_service.users().messages().send(
                userId='me', body={'raw': raw, 'threadId': thread_id}
            ).execute()

            to_name = to.split('@')[0]
            return CapabilityResult.ok(f"{to_name}さんに返信しました")

        except HttpError:
            return CapabilityResult.fail("今は返信できません")


class GmailSendPhoto(Capability):
    """写真付きメール送信"""

    @property
    def name(self) -> str:
        return "gmail_send_photo"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.COMMUNICATION

    @property
    def description(self) -> str:
        return """写真を撮ってメールで送る。以下の場面で使う：
- 「この写真を○○に送って」「写真付きでメールして」
- 目の前のものをメールで共有したいとき"""

    @property
    def requires_confirmation(self) -> bool:
        return True

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "宛先メールアドレス（省略時は直前のメール相手）"
                },
                "subject": {"type": "string", "description": "件名"},
                "body": {"type": "string", "description": "本文"}
            }
        }

    def execute(self, to: str = None, subject: str = "写真を送ります",
                body: str = "") -> CapabilityResult:
        if not _gmail_service:
            return CapabilityResult.fail("今はメールを送れません")

        if not to:
            if not _last_email_list:
                return CapabilityResult.fail("送り先を教えてください")
            to_raw = _last_email_list[0].get('from_email', '')
            match = re.search(r'<([^>]+)>', to_raw)
            to = match.group(1) if match else to_raw.strip()

        # 写真を撮影
        img_data = capture_image_raw()
        if not img_data:
            return CapabilityResult.fail("今は写真が撮れません")

        try:
            message = MIMEMultipart()
            message['to'] = to
            message['subject'] = subject
            message.attach(MIMEText(body or "写真を送ります。", 'plain'))

            img_part = MIMEBase('image', 'jpeg')
            img_part.set_payload(img_data)
            encoders.encode_base64(img_part)
            filename = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            img_part.add_header('Content-Disposition', 'attachment', filename=filename)
            message.attach(img_part)

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            _gmail_service.users().messages().send(userId='me', body={'raw': raw}).execute()

            to_name = to.split('@')[0]
            return CapabilityResult.ok(f"{to_name}さんに写真を送りました")

        except Exception:
            return CapabilityResult.fail("今は写真を送れません")


class VoiceSend(Capability):
    """スマホに音声メッセージ送信"""

    @property
    def name(self) -> str:
        return "voice_send"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.COMMUNICATION

    @property
    def description(self) -> str:
        return """スマホに音声メッセージを送る。以下の場面で使う：
- 「スマホにメッセージ送って」「スマホに連絡」
- 「妻/夫に伝えて」（スマホを持っている相手への連絡）"""

    @property
    def requires_confirmation(self) -> bool:
        return True

    def _get_parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self) -> CapabilityResult:
        if not _firebase_messenger:
            return CapabilityResult.fail("今はスマホに連絡できません")

        # 録音モードを開始するフラグを返す
        # 実際の録音はメインループで処理
        return CapabilityResult.ok(
            "どうぞ",
            data={"start_voice_recording": True}
        )


class VoiceSendPhoto(Capability):
    """スマホに写真送信"""

    @property
    def name(self) -> str:
        return "voice_send_photo"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.COMMUNICATION

    @property
    def description(self) -> str:
        return """写真を撮ってスマホに送る。以下の場面で使う：
- 「スマホに写真を送って」「今見てるものをスマホに」"""

    @property
    def requires_confirmation(self) -> bool:
        return True

    def _get_parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self) -> CapabilityResult:
        if not _firebase_messenger:
            return CapabilityResult.fail("今はスマホに送れません")

        # 写真を撮影
        photo_data = capture_image_raw()
        if not photo_data:
            return CapabilityResult.fail("今は写真が撮れません")

        try:
            if _firebase_messenger.send_photo_message(photo_data):
                return CapabilityResult.ok("スマホに写真を送りました")
            else:
                return CapabilityResult.fail("今は写真を送れません")
        except Exception:
            return CapabilityResult.fail("今は写真を送れません")


# エクスポート
COMMUNICATION_CAPABILITIES = [
    GmailList(),
    GmailRead(),
    GmailSend(),
    GmailReply(),
    GmailSendPhoto(),
    VoiceSend(),
    VoiceSendPhoto(),
]
