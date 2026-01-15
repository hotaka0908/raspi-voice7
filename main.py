#!/usr/bin/env python3
"""
raspi-voice6 - Capability UX ベースの音声AIアシスタント

ユーザーの意図を理解し、適切な能力を選択・組み合わせ、
世界を代行して実行する「翻訳層」として機能する。
"""

import os
import sys
import signal
import asyncio
import time
import io
import wave
import subprocess
import tempfile
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional

import numpy as np

from config import Config
from core import (
    AudioHandler,
    GeminiLiveClient,
    generate_startup_sound,
    generate_notification_sound
)
from capabilities import (
    init_gmail,
    init_firebase,
    init_calendar,
    get_firebase_messenger,
    load_alarms,
    start_alarm_thread,
    stop_alarm_thread,
    set_alarm_notify_callback,
    start_lifelog_thread,
    stop_lifelog_thread,
    set_firebase_messenger,
    set_play_audio_callback,
)

# systemdで実行時にprint出力をリアルタイムで表示
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ログ設定
os.makedirs(Config.LOG_DIR, exist_ok=True)
logger = logging.getLogger("conversation")
logger.setLevel(logging.INFO)

file_handler = RotatingFileHandler(
    os.path.join(Config.LOG_DIR, "conversation.log"),
    maxBytes=10*1024*1024,
    backupCount=5,
    encoding='utf-8'
)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
))
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(message)s', datefmt='%H:%M:%S'
))
logger.addHandler(console_handler)

# GPIO
try:
    from gpiozero import Button
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False

# グローバル状態
running = True
button: Optional[object] = None
is_recording = False
audio_handler: Optional[AudioHandler] = None


def signal_handler(sig, frame):
    """終了シグナルハンドラ"""
    global running
    running = False


def convert_webm_to_wav(audio_data: bytes) -> Optional[bytes]:
    """WebM音声をWAV形式に変換"""
    try:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as webm_file:
            webm_file.write(audio_data)
            webm_path = webm_file.name

        wav_path = webm_path.replace(".webm", ".wav")

        result = subprocess.run([
            "ffmpeg", "-y", "-i", webm_path,
            "-ar", str(Config.OUTPUT_SAMPLE_RATE), "-ac", "1", "-f", "wav", wav_path
        ], capture_output=True, timeout=30)

        if result.returncode != 0:
            return None

        with open(wav_path, "rb") as f:
            wav_data = f.read()

        os.unlink(webm_path)
        os.unlink(wav_path)
        return wav_data

    except Exception:
        return None


def on_voice_message_received(message):
    """スマホからの音声メッセージを受信"""
    global audio_handler

    if not audio_handler:
        return

    # 通知音
    notification = generate_notification_sound()
    if notification:
        audio_handler.play_audio_buffer(notification)

    try:
        audio_url = message.get("audio_url")
        if not audio_url:
            return

        messenger = get_firebase_messenger()
        if not messenger:
            return

        audio_data = messenger.download_audio(audio_url)
        if not audio_data:
            return

        filename = message.get("filename", "audio.webm")
        wav_data = convert_webm_to_wav(audio_data)
        if wav_data:
            audio_handler.play_audio_buffer(wav_data)

            # テキストがない場合はSTTでテキスト化してFirebaseに保存
            if not message.get("text"):
                try:
                    transcribed_text = transcribe_audio_with_gemini(wav_data)
                    if transcribed_text:
                        message_id = message.get("id")
                        if message_id:
                            messenger.update_message_text(message_id, transcribed_text)
                            logger.info(f"受信メッセージをテキスト化: {transcribed_text[:50]}...")
                except Exception as e:
                    logger.warning(f"テキスト化エラー: {e}")

        messenger.mark_as_played(message.get("id"))

    except Exception as e:
        logger.error(f"メッセージ受信エラー: {e}")


def transcribe_audio_with_gemini(wav_data: bytes) -> Optional[str]:
    """Gemini APIで音声を文字起こし"""
    from google import genai
    from google.genai import types

    try:
        client = genai.Client(api_key=Config.get_api_key())
        response = client.models.generate_content(
            model="gemini-2.5-flash-preview-05-20",
            contents=[
                types.Part.from_text(
                    text="日本語の音声です。話された内容をそのまま正確に文字起こししてください。句読点を適切に入れてください。余計な説明や補足は不要です。"
                ),
                types.Part.from_bytes(data=wav_data, mime_type="audio/wav")
            ]
        )

        if response and response.text:
            return response.text.strip()
        return None

    except Exception:
        return None


def record_voice_message() -> Optional[io.BytesIO]:
    """音声メッセージを録音"""
    global running, button, audio_handler

    if not audio_handler:
        return None

    # 音声メッセージ用のサンプルレート（スマホ互換性のため24kHz）
    voice_msg_sample_rate = Config.RECEIVE_SAMPLE_RATE  # 24kHz

    try:
        stream = audio_handler.audio.open(
            format=8,  # paInt16
            channels=Config.CHANNELS,
            rate=voice_msg_sample_rate,
            input=True,
            input_device_index=Config.INPUT_DEVICE_INDEX,
            frames_per_buffer=Config.CHUNK_SIZE
        )
    except Exception:
        return None

    frames = []
    start_time = time.time()

    try:
        while True:
            if not running:
                break

            if time.time() - start_time > 60:
                break

            if button and not button.is_pressed:
                break

            try:
                data = stream.read(Config.CHUNK_SIZE, exception_on_overflow=False)
                frames.append(data)
            except Exception:
                break
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass

    if len(frames) < 5:
        return None

    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, 'wb') as wf:
        wf.setnchannels(Config.CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(voice_msg_sample_rate)
        wf.writeframes(b''.join(frames))

    wav_buffer.seek(0)
    return wav_buffer


def send_recorded_voice_message(client: GeminiLiveClient) -> bool:
    """録音した音声をスマホに送信"""
    client.reset_voice_message_mode()

    try:
        wav_buffer = record_voice_message()

        if wav_buffer is None:
            return False

        wav_buffer.seek(0)
        wav_data = wav_buffer.read()
        transcribed_text = transcribe_audio_with_gemini(wav_data)

        messenger = get_firebase_messenger()
        if messenger and messenger.send_message(wav_data, text=transcribed_text):
            logger.info("音声メッセージ送信完了")
            return True
        else:
            return False

    except Exception:
        return False


async def audio_input_loop(client: GeminiLiveClient, audio_handler: AudioHandler):
    """音声入力ループ"""
    global running, button, is_recording

    while running:
        if Config.USE_BUTTON and button:
            if button.is_pressed:
                if not is_recording:
                    if client.needs_session_reset or not client.is_connected:
                        await asyncio.sleep(0.1)
                        continue

                    # 音声メッセージモード
                    if client.check_voice_message_timeout():
                        logger.info("=== 音声メッセージ録音開始 ===")
                        is_recording = True
                        loop = asyncio.get_event_loop()
                        success = await loop.run_in_executor(
                            None, lambda: send_recorded_voice_message(client)
                        )
                        is_recording = False
                        if success:
                            logger.info("音声メッセージ送信完了")
                        else:
                            logger.info("音声メッセージ送信失敗")
                        client.needs_session_reset = True
                        continue
                    else:
                        client.last_response_time = None
                        logger.info("=== 録音開始 ===")

                        if audio_handler.start_input_stream():
                            is_recording = True
                            await client.send_activity_start()
                        else:
                            continue

                # 音声送信
                chunk = audio_handler.read_audio_chunk()
                if chunk and len(chunk) > 0:
                    await client.send_audio_chunk(chunk)
            else:
                if is_recording:
                    is_recording = False
                    audio_handler.stop_input_stream()
                    logger.info("=== 録音停止 ===")
                    await client.send_activity_end()

        await asyncio.sleep(0.01)


async def main_async():
    """非同期メインループ"""
    global running, button, audio_handler

    audio_handler = AudioHandler()
    audio_handler.start_output_stream()

    # コールバック設定
    set_play_audio_callback(audio_handler.play_audio_buffer)

    client = GeminiLiveClient(audio_handler)
    receive_task = None
    input_task = None
    first_start = True

    # アラーム通知コールバック
    def alarm_notify(message: str):
        if client.is_connected:
            try:
                asyncio.run_coroutine_threadsafe(
                    client.send_text_message(message),
                    client.loop
                )
            except Exception:
                pass

    set_alarm_notify_callback(alarm_notify)
    start_alarm_thread()
    start_lifelog_thread()

    try:
        while running:
            # セッションタイムアウトチェック
            if client.last_response_time and client.is_connected:
                elapsed = time.time() - client.last_response_time
                if elapsed >= Config.SESSION_RESET_TIMEOUT:
                    logger.info("--- セッションリセット ---")
                    client.needs_session_reset = True
                    client.last_response_time = None

            # セッションリセット
            if client.needs_session_reset and client.is_connected:
                client.needs_session_reset = False

                if receive_task and not receive_task.done():
                    receive_task.cancel()
                    try:
                        await receive_task
                    except asyncio.CancelledError:
                        pass

                await client.reset_session()
                receive_task = asyncio.create_task(client.receive_messages())

            # 接続
            if not client.is_connected:
                if client.needs_reconnect:
                    success = await client.reconnect()
                    if not success:
                        if client.reconnect_count > Config.MAX_RECONNECT_ATTEMPTS:
                            running = False
                        continue
                else:
                    try:
                        await client.connect()
                    except Exception:
                        await asyncio.sleep(5)
                        continue

                if receive_task is None or receive_task.done():
                    receive_task = asyncio.create_task(client.receive_messages())
                if input_task is None or input_task.done():
                    input_task = asyncio.create_task(
                        audio_input_loop(client, audio_handler)
                    )

                if first_start:
                    print("\n" + "=" * 50)
                    print("raspi-voice6 起動")
                    print("=" * 50)
                    print("ボタンを押して話しかけてください")
                    print("=" * 50 + "\n")

                    startup_sound = generate_startup_sound()
                    if startup_sound:
                        audio_handler.play_audio_buffer(startup_sound)
                    first_start = False

            await asyncio.sleep(0.1)

        # タスクキャンセル
        if receive_task:
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass
        if input_task:
            input_task.cancel()
            try:
                await input_task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error(f"エラー: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.disconnect()
        audio_handler.cleanup()
        stop_alarm_thread()
        stop_lifelog_thread()


def main():
    """エントリーポイント"""
    global running, button

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # APIキー確認
    try:
        Config.get_api_key()
    except ValueError as e:
        print(f"エラー: {e}")
        sys.exit(1)

    # Gmail初期化
    gmail_ok = init_gmail()
    print(f"Gmail: {'有効' if gmail_ok else '無効'}")

    # カレンダー初期化
    calendar_ok = init_calendar()
    print(f"カレンダー: {'有効' if calendar_ok else '無効'}")

    # Firebase初期化
    firebase_ok = init_firebase(on_voice_message_received)
    print(f"Firebase: {'有効' if firebase_ok else '無効'}")

    if firebase_ok:
        messenger = get_firebase_messenger()
        set_firebase_messenger(messenger)

    # アラーム読み込み
    load_alarms()

    # ボタン初期化
    if Config.USE_BUTTON and GPIO_AVAILABLE:
        try:
            button = Button(Config.BUTTON_PIN, pull_up=True, bounce_time=0.1)
            print(f"ボタン: GPIO{Config.BUTTON_PIN}")
        except Exception as e:
            print(f"ボタン初期化エラー: {e}")
            button = None
            Config.USE_BUTTON = False
    else:
        button = None
        Config.USE_BUTTON = False

    asyncio.run(main_async())
    print("終了しました")


if __name__ == "__main__":
    main()
