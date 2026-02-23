"""
OpenAI Realtime APIクライアント

リアルタイム音声対話を管理
"""

import asyncio
import base64
import json
import time
import logging
from typing import Optional, Callable, Any, Dict
import websockets

from config import Config
from prompts import get_system_prompt
from capabilities import get_executor, CapabilityResult

# ロガー設定（main.pyと同じロガーを使用）
logger = logging.getLogger("conversation")


class OpenAIRealtimeClient:
    """OpenAI Realtime APIクライアント"""

    def __init__(self, audio_handler, on_response_complete: Optional[Callable] = None):
        self.api_key = Config.get_api_key()
        self.audio_handler = audio_handler
        self.on_response_complete = on_response_complete

        self.executor = get_executor()

        self.ws = None
        self.is_connected = False
        self.is_responding = False
        self.loop = None

        self.needs_reconnect = False
        self.reconnect_count = 0
        self.needs_session_reset = False
        self.last_response_time = None
        self.last_audio_time = None  # 最後に音声を再生した時間

        # 音声メッセージモード
        self.voice_message_mode = False
        self.voice_message_timestamp = None

        # 保留中のツール呼び出し
        self._pending_tool_calls = {}
        self._current_response_id = None

    def _get_session_config(self) -> Dict[str, Any]:
        """セッション設定を取得"""
        return {
            "modalities": ["text", "audio"],
            "instructions": get_system_prompt(),
            "voice": Config.VOICE,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {
                "model": "whisper-1",
                "language": "ja"  # 日本語を明示的に指定
            },
            "turn_detection": None,  # 手動制御
            "tools": self.executor.get_openai_tools(),
        }

    async def connect(self) -> None:
        """接続"""
        try:
            url = f"wss://api.openai.com/v1/realtime?model={Config.MODEL}"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "OpenAI-Beta": "realtime=v1"
            }

            self.ws = await websockets.connect(url, additional_headers=headers)
            self.is_connected = True
            self.loop = asyncio.get_event_loop()

            # セッション設定を送信
            await self._send_event("session.update", {
                "session": self._get_session_config()
            })

            logger.info("OpenAI Realtime API接続完了")
        except Exception as e:
            logger.error(f"接続エラー: {e}")
            raise

    async def disconnect(self) -> None:
        """切断"""
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
            self.is_connected = False

    async def _send_event(self, event_type: str, data: Dict[str, Any] = None) -> None:
        """イベントを送信"""
        if not self.ws:
            return

        event = {"type": event_type}
        if data:
            event.update(data)

        try:
            await self.ws.send(json.dumps(event))
        except Exception as e:
            logger.error(f"イベント送信エラー: {e}")

    async def send_activity_start(self) -> None:
        """音声活動開始を通知"""
        # 録音開始時にバッファをクリア（raspi-voice3と同様）
        await self.clear_input_buffer()

    async def clear_input_buffer(self) -> None:
        """入力バッファをクリア"""
        if not self.is_connected or not self.ws:
            return
        try:
            await self._send_event("input_audio_buffer.clear")
        except Exception as e:
            logger.error(f"バッファクリアエラー: {e}")

    async def send_activity_end(self) -> None:
        """音声活動終了を通知（レスポンス生成をトリガー）"""
        if not self.is_connected or not self.ws:
            return

        try:
            # 音声バッファをコミット
            await self._send_event("input_audio_buffer.commit")
            # レスポンス生成を要求
            await self._send_event("response.create")
        except Exception as e:
            logger.error(f"activity_end送信エラー: {e}")

    async def send_audio_chunk(self, audio_data: bytes) -> None:
        """音声チャンクを送信"""
        if not self.is_connected or not self.ws:
            return

        try:
            # Base64エンコードして送信
            audio_base64 = base64.b64encode(audio_data).decode('utf-8')
            await self._send_event("input_audio_buffer.append", {
                "audio": audio_base64
            })
        except Exception as e:
            logger.error(f"音声送信エラー: {e}")

    async def send_text_message(self, text: str) -> None:
        """テキストメッセージを送信（アラーム通知用）"""
        if not self.is_connected or not self.ws:
            return

        try:
            # 会話アイテムとしてテキストを追加
            await self._send_event("conversation.item.create", {
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": text
                        }
                    ]
                }
            })
            # レスポンス生成を要求
            await self._send_event("response.create")
        except Exception as e:
            logger.error(f"テキスト送信エラー: {e}")

    async def send_tool_response(self, call_id: str, result: str) -> None:
        """ツール実行結果を送信"""
        if not self.is_connected or not self.ws:
            return

        try:
            await self._send_event("conversation.item.create", {
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": result
                }
            })
            # レスポンス生成を要求
            await self._send_event("response.create")
        except Exception as e:
            logger.error(f"ツール結果送信エラー: {e}")

    async def receive_messages(self) -> None:
        """メッセージ受信ループ"""
        try:
            while self.is_connected and self.ws:
                try:
                    message = await self.ws.recv()
                    event = json.loads(message)
                    await self._handle_event(event)
                    self.reconnect_count = 0
                except websockets.ConnectionClosed:
                    logger.warning("WebSocket接続が閉じられました")
                    self.is_connected = False
                    self.needs_reconnect = True
                    break

        except Exception as e:
            logger.error(f"受信エラー: {e}")
            self.is_connected = False
            self.needs_reconnect = True

    async def _handle_event(self, event: Dict[str, Any]) -> None:
        """イベントを処理"""
        event_type = event.get("type", "")

        # セッション作成完了
        if event_type == "session.created":
            logger.info("セッション作成完了")

        # セッション更新完了
        elif event_type == "session.updated":
            logger.info("セッション設定更新完了")

        # エラー
        elif event_type == "error":
            error = event.get("error", {})
            logger.error(f"APIエラー: {error.get('message', 'Unknown error')}")

        # レスポンス開始
        elif event_type == "response.created":
            self.is_responding = True
            self._current_response_id = event.get("response", {}).get("id")

        # 音声デルタ
        elif event_type == "response.audio.delta":
            audio_base64 = event.get("delta", "")
            if audio_base64:
                audio_data = base64.b64decode(audio_base64)
                self.audio_handler.play_audio_chunk(audio_data)
                self.last_audio_time = time.time()

        # 音声トランスクリプト
        elif event_type == "response.audio_transcript.delta":
            text = event.get("delta", "")
            if text:
                logger.debug(f"[AI transcript delta] {text}")

        elif event_type == "response.audio_transcript.done":
            text = event.get("transcript", "")
            if text:
                logger.info(f"[AI] {text}")

        # 入力音声トランスクリプト
        elif event_type == "conversation.item.input_audio_transcription.completed":
            text = event.get("transcript", "")
            if text:
                logger.info(f"[USER] {text}")

        # 入力音声トランスクリプト失敗
        elif event_type == "conversation.item.input_audio_transcription.failed":
            error = event.get("error", {})
            logger.error(f"音声認識失敗: {error.get('message', 'Unknown error')}")

        # ツール呼び出し
        elif event_type == "response.function_call_arguments.done":
            call_id = event.get("call_id")
            name = event.get("name")
            arguments_str = event.get("arguments", "{}")

            try:
                arguments = json.loads(arguments_str)
            except json.JSONDecodeError:
                arguments = {}

            logger.info(f"[CAPABILITY] {name} {arguments}")

            # 長時間かかる処理は別スレッドで
            if name in ["voice_send_photo", "camera_capture", "gmail_send_photo"]:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, lambda: self.executor.execute(name, arguments)
                )
            else:
                result = self.executor.execute(name, arguments)

            # voice_sendの場合は録音モードを有効化
            if result.data and result.data.get("start_voice_recording"):
                self.voice_message_mode = True
                self.voice_message_timestamp = time.time()

            # ツール結果を送信
            await self.send_tool_response(call_id, result.message)

        # レスポンス完了
        elif event_type == "response.done":
            self.is_responding = False
            self.last_response_time = time.time()
            self._current_response_id = None
            if self.on_response_complete:
                self.on_response_complete()

        # 割り込み
        elif event_type == "input_audio_buffer.speech_started":
            # ユーザーが話し始めた
            pass

        elif event_type == "input_audio_buffer.speech_stopped":
            # ユーザーが話し終わった
            pass

        elif event_type == "input_audio_buffer.committed":
            # 音声バッファがコミットされた
            pass

        elif event_type == "rate_limits.updated":
            # レート制限情報
            pass

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
