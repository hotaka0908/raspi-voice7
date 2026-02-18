"""
OpenClaw連携Capability

OpenClaw (パーソナルAIアシスタント) との連携:
- メッセージをOpenClawに送信して応答を取得
- OpenClawの各種スキルや機能を利用
"""

import asyncio
import json
import uuid
from typing import Any, Dict, Optional
import websockets
import websockets.client

from .base import Capability, CapabilityCategory, CapabilityResult
from config import Config


# WebSocketクライアント
_openclaw_client = None
_openclaw_client_lock = asyncio.Lock()


class OpenClawClient:
    """OpenClaw Gateway WebSocketクライアント"""

    def __init__(self, url: str, token: Optional[str] = None):
        self.url = url
        self.token = token
        self.websocket: Optional[websockets.client.WebSocketClientProtocol] = None
        self._connected = False

    async def connect(self) -> bool:
        """Gatewayに接続"""
        try:
            # ヘッダーに認証情報を追加
            headers = {}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"

            self.websocket = await websockets.connect(
                self.url,
                additional_headers=headers,
                close_timeout=5,
            )
            self._connected = True
            return True
        except Exception as e:
            print(f"OpenClaw connection failed: {e}")
            return False

    async def disconnect(self):
        """接続を閉じる"""
        if self.websocket:
            await self.websocket.close()
            self._connected = False

    async def _send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """RPCリクエストを送信して応答を取得"""
        if not self._connected or not self.websocket:
            if not await self.connect():
                return {"error": "OpenClawに接続できません"}

        try:
            # リクエストフレームを作成
            request = {
                "method": method,
                "params": params,
                "id": str(uuid.uuid4()),
            }

            await self.websocket.send(json.dumps(request))

            # 応答を待機
            response_text = await self.websocket.recv()
            response = json.loads(response_text)

            if "error" in response:
                return {"error": response["error"]}

            return response.get("result", {})

        except Exception as e:
            print(f"OpenClaw RPC error: {e}")
            return {"error": str(e)}

    async def send_chat(
        self,
        message: str,
        session_key: str = "main",
        thinking: Optional[str] = None,
        timeout_ms: int = 30000,
    ) -> Dict[str, Any]:
        """チャットメッセージを送信"""
        params = {
            "sessionKey": session_key,
            "message": message,
            "idempotencyKey": str(uuid.uuid4()),
            "timeoutMs": timeout_ms,
        }
        if thinking:
            params["thinking"] = thinking

        return await self._send_request("chat.send", params)

    async def chat_history(
        self, session_key: str = "main", limit: int = 10
    ) -> Dict[str, Any]:
        """チャット履歴を取得"""
        return await self._send_request(
            "chat.history",
            {"sessionKey": session_key, "limit": limit},
        )


async def get_openclaw_client() -> Optional[OpenClawClient]:
    """OpenClawクライアントを取得（接続済み）"""
    global _openclaw_client

    async with _openclaw_client_lock:
        if _openclaw_client is None:
            url = Config.get_openclaw_url()
            token = Config.get_openclaw_token()
            _openclaw_client = OpenClawClient(url, token)

            if not await _openclaw_client.connect():
                _openclaw_client = None

    return _openclaw_client


def close_openclaw_client():
    """クライアントを閉じる（同期版、終了時用）"""
    global _openclaw_client
    _openclaw_client = None


class OpenClawChat(Capability):
    """OpenClawにメッセージを送信して応答を取得"""

    @property
    def name(self) -> str:
        return "openclaw_chat"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.VISION  # 「詳しく知る」の拡張として

    @property
    def description(self) -> str:
        return """OpenClawパーソナルアシスタントを利用する。以下の場面で使う：

■ スキル機能の実行:
- 「Notionにメモして」「GitHubでPR作って」
- 「Spotifyで音楽再生」「Slackに投稿して」
- 「カレンダーの予定確認」「メール送って」
- OpenClawで設定した各種スキル/ツールの利用

■ マルチチャネル連携:
- WhatsAppにメッセージ送信
- Slack/Discordへの投稿
- 各種チャットサービスとの連携

■ エージェント機能:
- Claude/他のAIモデルによる応答
- 高度な推論・計算・分析タスク

messageでOpenClawに送信するメッセージを渡す。
session_keyでセッションを指定（デフォルト: "main"）。"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "OpenClawに送信するメッセージ"
                },
                "session_key": {
                    "type": "string",
                    "description": "セッションキー（デフォルト: 'main'）"
                }
            },
            "required": ["message"]
        }

    async def execute_async(self, message: str, session_key: str = "main") -> CapabilityResult:
        """OpenClawチャットを実行（非同期）"""
        client = await get_openclaw_client()
        if client is None:
            return CapabilityResult.fail("OpenClawに接続できません")

        try:
            result = await client.send_chat(message, session_key=session_key)

            if "error" in result:
                error_msg = result["error"]
                if isinstance(error_msg, dict):
                    error_msg = str(error_msg)
                return CapabilityResult.fail(f"OpenClawエラー: {error_msg}")

            # 成功応答からメッセージを抽出
            # responseは { "runId": "...", "message": "..." } の形式
            if "message" in result:
                message_text = result["message"]
                if isinstance(message_text, dict):
                    # マークダウン形式ならtextフィールドを使用
                    message_text = message_text.get("text", str(message_text))
                return CapabilityResult.ok(str(message_text), data=result)

            return CapabilityResult.ok("完了しました", data=result)

        except Exception as e:
            return CapabilityResult.fail(f"OpenClaw通信エラー: {str(e)}")

    def execute(self, message: str, session_key: str = "main") -> CapabilityResult:
        """同期インターフェース（OpenAI Realtime APIは同期のみサポート）"""
        # 非同期関数を同期コンテキストで実行
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self.execute_async(message, session_key))
            return result
        finally:
            loop.close()


class OpenClawSkills(Capability):
    """OpenClawのスキル一覧を取得"""

    @property
    def name(self) -> str:
        return "openclaw_skills"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.VISION

    @property
    def description(self) -> str:
        return """OpenClawで利用可能なスキル一覧を取得する。
ユーザーが「何ができる？」「スキル教えて」と聞いた時に使用。"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self) -> CapabilityResult:
        """スキル一覧を取得"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def _get_status():
                client = await get_openclaw_client()
                if client is None:
                    return None
                # statusメソッドで情報を取得
                return await client._send_request("status", {})

            result = loop.run_until_complete(_get_status())
            loop.close()

            if result and "error" not in result:
                return CapabilityResult.ok("OpenClawに接続済みです", data=result)

            return CapabilityResult.fail("OpenClawに接続できません")

        except Exception as e:
            return CapabilityResult.fail(f"エラー: {str(e)}")


# エクスポート
OPENCLAW_CAPABILITIES = [
    OpenClawChat(),
    OpenClawSkills(),
]
