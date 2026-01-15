"""
Gemini Live APIクライアント

リアルタイム音声対話を管理
"""

import asyncio
import time
import logging
from typing import Optional, Callable, Any, Dict
from google import genai
from google.genai import types

from config import Config
from prompts import get_system_prompt
from capabilities import get_executor, CapabilityResult

# ロガー設定
logger = logging.getLogger("gemini")


class GeminiLiveClient:
    """Gemini Live APIクライアント"""

    def __init__(self, audio_handler, on_response_complete: Optional[Callable] = None):
        self.api_key = Config.get_api_key()
        self.audio_handler = audio_handler
        self.on_response_complete = on_response_complete

        self.client = genai.Client(api_key=self.api_key)
        self.executor = get_executor()

        self.session = None
        self.session_context = None
        self.is_connected = False
        self.is_responding = False
        self.loop = None

        self.needs_reconnect = False
        self.reconnect_count = 0
        self.needs_session_reset = False
        self.last_response_time = None

        # 音声メッセージモード
        self.voice_message_mode = False
        self.voice_message_timestamp = None

    def _get_config(self) -> Dict[str, Any]:
        """セッション設定を取得"""
        return {
            "response_modalities": ["AUDIO"],
            "system_instruction": get_system_prompt(),
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {
                        "voice_name": Config.VOICE
                    }
                }
            },
            "realtime_input_config": {
                "automatic_activity_detection": {
                    "disabled": True
                }
            },
            "thinking_config": {
                "thinking_budget": 0
            },
            "input_audio_transcription": {},
            "tools": self.executor.get_gemini_tools(),
        }

    async def connect(self) -> None:
        """接続"""
        try:
            self.session_context = self.client.aio.live.connect(
                model=Config.MODEL,
                config=self._get_config()
            )
            self.session = await self.session_context.__aenter__()
            self.is_connected = True
            self.loop = asyncio.get_event_loop()
            logger.info("Gemini Live API接続完了")
        except Exception as e:
            logger.error(f"接続エラー: {e}")
            raise

    async def disconnect(self) -> None:
        """切断"""
        if self.session_context:
            try:
                await self.session_context.__aexit__(None, None, None)
            except Exception:
                pass
            self.session_context = None
            self.session = None
            self.is_connected = False

    async def send_activity_start(self) -> None:
        """音声活動開始を通知"""
        if not self.is_connected or not self.session:
            return

        try:
            await self.session.send_realtime_input(
                activity_start=types.ActivityStart()
            )
        except Exception as e:
            logger.error(f"activity_start送信エラー: {e}")

    async def send_activity_end(self) -> None:
        """音声活動終了を通知"""
        if not self.is_connected or not self.session:
            return

        try:
            await self.session.send_realtime_input(
                activity_end=types.ActivityEnd()
            )
        except Exception as e:
            logger.error(f"activity_end送信エラー: {e}")

    async def send_audio_chunk(self, audio_data: bytes) -> None:
        """音声チャンクを送信"""
        if not self.is_connected or not self.session:
            return

        try:
            await self.session.send_realtime_input(
                audio={"data": audio_data, "mime_type": "audio/pcm;rate=16000"}
            )
        except Exception as e:
            logger.error(f"音声送信エラー: {e}")

    async def send_text_message(self, text: str) -> None:
        """テキストメッセージを送信（アラーム通知用）"""
        if not self.is_connected or not self.session:
            return

        try:
            await self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True
            )
        except Exception as e:
            logger.error(f"テキスト送信エラー: {e}")

    async def send_tool_response(self, function_responses) -> None:
        """ツール実行結果を送信"""
        if not self.is_connected or not self.session:
            return

        try:
            await self.session.send_tool_response(function_responses=function_responses)
        except Exception as e:
            logger.error(f"ツール結果送信エラー: {e}")

    async def receive_messages(self) -> None:
        """メッセージ受信ループ"""
        try:
            while self.is_connected:
                try:
                    async for response in self.session.receive():
                        await self._handle_response(response)
                        self.reconnect_count = 0
                except StopAsyncIteration:
                    continue

        except Exception as e:
            logger.error(f"受信エラー: {e}")
            self.is_connected = False
            self.needs_reconnect = True

    async def _handle_response(self, response) -> None:
        """レスポンスを処理"""
        # サーバーコンテンツ
        if hasattr(response, 'server_content') and response.server_content:
            server_content = response.server_content

            # 割り込み
            if hasattr(server_content, 'interrupted') and server_content.interrupted:
                self.is_responding = False

            # モデルのターン（音声）
            if hasattr(server_content, 'model_turn') and server_content.model_turn:
                self.is_responding = True
                for part in server_content.model_turn.parts:
                    if hasattr(part, 'inline_data') and part.inline_data:
                        if hasattr(part.inline_data, 'data') and isinstance(part.inline_data.data, bytes):
                            self.audio_handler.play_audio_chunk(part.inline_data.data)

            # ターン完了
            if hasattr(server_content, 'turn_complete') and server_content.turn_complete:
                self.is_responding = False
                self.last_response_time = time.time()
                if self.on_response_complete:
                    self.on_response_complete()

            # トランスクリプト
            if hasattr(server_content, 'output_transcription') and server_content.output_transcription:
                text = server_content.output_transcription.text
                if text and text.strip():
                    logger.info(f"[AI] {text.strip()}")

            if hasattr(server_content, 'input_transcription') and server_content.input_transcription:
                text = server_content.input_transcription.text
                if text and text.strip():
                    logger.info(f"[USER] {text.strip()}")

        # ツール呼び出し
        if hasattr(response, 'tool_call') and response.tool_call:
            function_responses = []

            for fc in response.tool_call.function_calls:
                tool_name = fc.name
                arguments = dict(fc.args) if fc.args else {}

                logger.info(f"[CAPABILITY] {tool_name} {arguments}")

                # 長時間かかる処理は別スレッドで
                if tool_name in ["voice_send_photo", "camera_capture", "gmail_send_photo"]:
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        None, lambda: self.executor.execute(tool_name, arguments)
                    )
                else:
                    result = self.executor.execute(tool_name, arguments)

                # voice_sendの場合は録音モードを有効化
                if result.data and result.data.get("start_voice_recording"):
                    self.voice_message_mode = True
                    self.voice_message_timestamp = time.time()

                function_responses.append(
                    types.FunctionResponse(
                        id=fc.id,
                        name=tool_name,
                        response={"result": result.message}
                    )
                )

            if function_responses:
                await self.send_tool_response(function_responses)

    async def reset_session(self) -> bool:
        """セッションリセット"""
        await self.disconnect()

        if not self.voice_message_mode:
            self.voice_message_mode = False
            self.voice_message_timestamp = None

        try:
            await self.connect()
            return True
        except Exception:
            self.needs_reconnect = True
            return False

    async def reconnect(self) -> bool:
        """再接続（指数バックオフ）"""
        self.reconnect_count += 1
        if self.reconnect_count > Config.MAX_RECONNECT_ATTEMPTS:
            return False

        delay = min(Config.RECONNECT_DELAY_BASE ** self.reconnect_count, 60)
        await asyncio.sleep(delay)

        await self.disconnect()
        self.needs_reconnect = False

        if not self.voice_message_mode:
            self.voice_message_mode = False
            self.voice_message_timestamp = None

        try:
            await self.connect()
            return True
        except Exception:
            self.needs_reconnect = True
            return False

    def check_voice_message_timeout(self) -> bool:
        """音声メッセージモードのタイムアウトチェック"""
        if self.voice_message_mode and self.voice_message_timestamp:
            elapsed = time.time() - self.voice_message_timestamp
            if elapsed > Config.VOICE_MESSAGE_TIMEOUT:
                self.voice_message_mode = False
                self.voice_message_timestamp = None
                return False
        return self.voice_message_mode

    def reset_voice_message_mode(self) -> None:
        """音声メッセージモードをリセット"""
        self.voice_message_mode = False
        self.voice_message_timestamp = None
