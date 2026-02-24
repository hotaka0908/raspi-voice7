#!/usr/bin/env python3
"""
raspi-voice7 - OpenAI Realtime API版 Capability UX ベースの音声AIアシスタント

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
    OpenAIRealtimeClient,
    generate_startup_sound,
    generate_notification_sound,
    generate_reset_sound,
    FirebaseSignaling,
    get_video_call_manager,
    AIORTC_AVAILABLE,
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
    pause_lifelog,
    resume_lifelog,
    set_videocall_callbacks,
    start_reminder_thread,
    stop_reminder_thread,
    set_reminder_notify_callback,
    stop_music_player,
    set_music_audio_callbacks,
    is_music_active,
    close_openclaw_client,
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
last_button_press_time: float = 0  # ダブルクリック検出用
DOUBLE_CLICK_THRESHOLD = 0.5  # ダブルクリック判定時間（秒）

# ビデオ通話状態
_signaling: Optional[FirebaseSignaling] = None
_pending_incoming_call: Optional[dict] = None
_openai_client: Optional[OpenAIRealtimeClient] = None


def signal_handler(sig, frame):
    """終了シグナルハンドラ"""
    global running
    running = False


# ========================================
# ビデオ通話関連
# ========================================

def generate_ringtone() -> Optional[bytes]:
    """着信音を生成"""
    try:
        sample_rate = 48000
        duration = 0.3

        samples = int(sample_rate * duration)
        t = np.linspace(0, duration, samples, False)

        # 440Hz + 880Hz のダブルトーン
        tone = np.sin(2 * np.pi * 440 * t) * 0.3 + np.sin(2 * np.pi * 880 * t) * 0.2
        envelope = np.ones(samples)
        envelope[-int(samples * 0.1):] = np.linspace(1, 0, int(samples * 0.1))
        sound = (tone * envelope * 32767).astype(np.int16)

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(sound.tobytes())

        return wav_buffer.getvalue()
    except Exception:
        return None


def start_videocall_from_raspi() -> bool:
    """ラズパイからビデオ通話を発信"""
    global _signaling

    if not AIORTC_AVAILABLE or not _signaling:
        logger.warning("ビデオ通話が利用できません")
        return False

    try:
        loop = asyncio.get_event_loop()
        future = asyncio.run_coroutine_threadsafe(
            _start_outgoing_call(),
            loop
        )
        return future.result(timeout=5)
    except Exception as e:
        logger.error(f"発信エラー: {e}")
        return False


async def _start_outgoing_call() -> bool:
    """発信処理（非同期）"""
    global _signaling, _openai_client

    if not _signaling:
        return False

    try:
        video_manager = get_video_call_manager()

        # PeerConnection作成
        if not await video_manager.create_peer_connection():
            return False

        # ICE候補送信コールバック設定
        def on_ice_candidate(candidate):
            if _signaling.current_session_id:
                _signaling.send_ice_candidate(
                    _signaling.current_session_id,
                    candidate,
                    is_caller=True
                )

        video_manager.on_ice_candidate = on_ice_candidate

        # ローカルメディア開始（カメラ/マイク）
        pause_lifelog()  # ライフログ一時停止
        if not await video_manager.start_local_media():
            resume_lifelog()
            return False

        # 発信セッション作成
        session_id = _signaling.create_call()
        if not session_id:
            await video_manager.end_call()
            resume_lifelog()
            return False

        # Offer作成・送信
        offer = await video_manager.create_offer()
        if not offer:
            _signaling.end_call(session_id)
            await video_manager.end_call()
            resume_lifelog()
            return False

        _signaling.send_offer(session_id, offer)
        logger.info(f"ビデオ通話発信: {session_id}")
        return True

    except Exception as e:
        logger.error(f"発信エラー: {e}")
        resume_lifelog()
        return False


def end_videocall() -> bool:
    """ビデオ通話を終了"""
    global _signaling

    try:
        video_manager = get_video_call_manager()

        if _main_loop:
            asyncio.run_coroutine_threadsafe(
                video_manager.end_call(),
                _main_loop
            )

        if _signaling:
            _signaling.end_call()

        resume_lifelog()  # ライフログ再開
        logger.info("ビデオ通話終了")
        return True

    except Exception as e:
        logger.error(f"通話終了エラー: {e}")
        return False


def is_in_videocall() -> bool:
    """通話中かどうか"""
    video_manager = get_video_call_manager()
    return video_manager.is_in_call


def on_incoming_call(session_id: str, session: dict) -> None:
    """着信コールバック - ビデオ通話を自動応答"""
    global _pending_incoming_call, audio_handler

    logger.info(f"ビデオ通話着信: {session_id}")
    _pending_incoming_call = {"session_id": session_id, "session": session}

    # 自動応答（ボタンを押す必要なし）
    try:
        if _main_loop is None:
            logger.error("メインイベントループが未設定")
            return
        asyncio.run_coroutine_threadsafe(
            accept_incoming_call(),
            _main_loop
        )
    except Exception as e:
        logger.error(f"自動応答エラー: {e}")


def on_answer_received(session_id: str, answer: dict) -> None:
    """Answer受信コールバック"""
    logger.info(f"Answer受信: {session_id}")

    try:
        if _main_loop is None:
            logger.error("メインイベントループが未設定")
            return
        asyncio.run_coroutine_threadsafe(
            _handle_answer(answer),
            _main_loop
        )
    except Exception as e:
        logger.error(f"Answer処理エラー: {e}")


async def _handle_answer(answer: dict) -> None:
    """Answer処理（非同期）"""
    video_manager = get_video_call_manager()
    await video_manager.handle_answer(answer)


def on_ice_candidate_received(session_id: str, candidate: dict) -> None:
    """ICE候補受信コールバック"""
    global _pending_incoming_call, _signaling

    # セッションIDが現在のセッションと一致するか確認
    current_session = None
    if _pending_incoming_call:
        current_session = _pending_incoming_call.get("session_id")
    elif _signaling and _signaling.current_session_id:
        current_session = _signaling.current_session_id

    # アクティブなセッションがない場合は無視
    if not current_session:
        return

    if session_id != current_session:
        # 異なるセッションのICE候補は無視
        logger.debug(f"ICE候補無視（セッション不一致）: expected={current_session}, got={session_id}")
        return

    logger.info(f"ICE候補受信: {candidate.get('candidate', '')[:50]}...")
    try:
        if _main_loop is None:
            logger.error("メインイベントループが未設定")
            return
        asyncio.run_coroutine_threadsafe(
            _handle_ice_candidate(candidate),
            _main_loop
        )
    except Exception as e:
        logger.error(f"ICE候補処理エラー: {e}")


async def _handle_ice_candidate(candidate: dict) -> None:
    """ICE候補処理（非同期）"""
    video_manager = get_video_call_manager()
    result = await video_manager.add_ice_candidate(candidate)
    logger.info(f"ICE候補追加結果: {result}")


def on_call_ended(session_id: str) -> None:
    """通話終了コールバック"""
    global _pending_incoming_call

    logger.info(f"通話終了検出: {session_id}")
    _pending_incoming_call = None

    try:
        if _main_loop is None:
            logger.error("メインイベントループが未設定")
            return
        asyncio.run_coroutine_threadsafe(
            _cleanup_call(),
            _main_loop
        )
    except Exception as e:
        logger.error(f"通話終了処理エラー: {e}")


async def _cleanup_call() -> None:
    """通話終了クリーンアップ"""
    video_manager = get_video_call_manager()
    await video_manager.end_call()
    resume_lifelog()
    _restart_audio_handler()


def _restart_audio_handler() -> None:
    """メインオーディオストリームを再開"""
    global audio_handler
    if audio_handler:
        try:
            audio_handler.start_output_stream()
            logger.info("メインオーディオストリーム再開")
        except Exception as e:
            logger.error(f"オーディオストリーム再開エラー: {e}")


async def accept_incoming_call() -> bool:
    """着信に応答"""
    global _pending_incoming_call, _signaling, audio_handler

    if not _pending_incoming_call or not _signaling:
        return False

    session_id = _pending_incoming_call["session_id"]
    session = _pending_incoming_call["session"]

    logger.info("=== ビデオ通話応答 ===")

    try:
        video_manager = get_video_call_manager()

        # メインオーディオストリームを停止（デバイス競合回避）
        if audio_handler:
            audio_handler.stop_output_stream()
            audio_handler.stop_input_stream()
            logger.info("メインオーディオストリーム停止")

        # 応答ステータス更新（ICE候補受信のためcurrent_session_idを先に設定）
        _signaling.accept_call(session_id)
        _pending_incoming_call = None

        # PeerConnection作成
        if not await video_manager.create_peer_connection():
            _restart_audio_handler()
            return False

        # ICE候補送信コールバック設定
        def on_ice_candidate(candidate):
            logger.info(f"ICE候補をFirebaseに送信: {candidate.get('candidate', '')[:50]}...")
            _signaling.send_ice_candidate(session_id, candidate, is_caller=False)

        video_manager.on_ice_candidate = on_ice_candidate

        # ローカルメディア開始
        pause_lifelog()
        if not await video_manager.start_local_media():
            resume_lifelog()
            _restart_audio_handler()
            return False

        # Offer処理・Answer作成
        offer = session.get("offer")
        if not offer:
            await video_manager.end_call()
            resume_lifelog()
            _restart_audio_handler()
            return False

        answer = await video_manager.handle_offer(offer)
        if not answer:
            await video_manager.end_call()
            resume_lifelog()
            _restart_audio_handler()
            return False

        # Answer送信
        _signaling.send_answer(session_id, answer)
        logger.info(f"着信応答完了: {session_id}")
        logger.info("ビデオ通話接続成功")
        return True

    except Exception as e:
        logger.error(f"着信応答エラー: {e}")
        resume_lifelog()
        _restart_audio_handler()
        return False


_main_loop = None  # メインイベントループの参照

def init_videocall(loop=None) -> bool:
    """ビデオ通話初期化"""
    global _signaling, _main_loop

    if not AIORTC_AVAILABLE:
        logger.warning("aiortcがインストールされていません。ビデオ通話は無効です。")
        return False

    try:
        # メインイベントループを保存（引数で渡されたループを使用）
        _main_loop = loop or asyncio.get_running_loop()

        _signaling = FirebaseSignaling(device_id="raspi")

        # コールバック設定
        _signaling.on_incoming_call = on_incoming_call
        _signaling.on_answer_received = on_answer_received
        _signaling.on_ice_candidate = on_ice_candidate_received
        _signaling.on_call_ended = on_call_ended

        # シグナリング監視開始
        _signaling.start_listening()

        # Capability用コールバック設定
        set_videocall_callbacks(
            start_callback=start_videocall_from_raspi,
            end_callback=end_videocall,
            is_in_call_callback=is_in_videocall
        )

        logger.info("ビデオ通話初期化完了")
        return True

    except Exception as e:
        logger.error(f"ビデオ通話初期化エラー: {e}")
        return False


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

    msg_id = message.get("id", "unknown")
    logger.info(f"[受信] メッセージ受信開始: id={msg_id}")

    if not audio_handler:
        logger.error(f"[受信] audio_handlerがNone: id={msg_id}")
        return

    logger.info(f"[受信] audio_handler状態: is_playing={audio_handler.is_playing}, output_stream={audio_handler.output_stream is not None}")

    # 通知音
    notification = generate_notification_sound()
    if notification:
        logger.info(f"[受信] 通知音再生: id={msg_id}")
        audio_handler.play_audio_buffer(notification)

    try:
        audio_url = message.get("audio_url")
        if not audio_url:
            logger.warning(f"[受信] audio_urlがない: id={msg_id}")
            return

        messenger = get_firebase_messenger()
        if not messenger:
            logger.error(f"[受信] messengerがNone: id={msg_id}")
            return

        logger.info(f"[受信] 音声ダウンロード開始: id={msg_id}")
        audio_data = messenger.download_audio(audio_url)
        if not audio_data:
            logger.error(f"[受信] 音声ダウンロード失敗: id={msg_id}")
            return
        logger.info(f"[受信] 音声ダウンロード完了: id={msg_id}, size={len(audio_data)} bytes")

        filename = message.get("filename", "audio.webm")
        logger.info(f"[受信] WebM→WAV変換開始: id={msg_id}, filename={filename}")
        wav_data = convert_webm_to_wav(audio_data)
        if wav_data:
            logger.info(f"[受信] WAV変換完了: id={msg_id}, size={len(wav_data)} bytes")
            logger.info(f"[受信] 音声再生開始: id={msg_id}")
            audio_handler.play_audio_buffer(wav_data)
            logger.info(f"[受信] 音声再生完了: id={msg_id}")

            # テキストがない場合はSTTでテキスト化してFirebaseに保存
            if not message.get("text"):
                try:
                    transcribed_text = transcribe_audio(wav_data)
                    if transcribed_text:
                        message_id = message.get("id")
                        if message_id:
                            messenger.update_message_text(message_id, transcribed_text)
                            logger.info(f"受信メッセージをテキスト化: {transcribed_text[:50]}...")
                except Exception as e:
                    logger.warning(f"テキスト化エラー: {e}")
        else:
            logger.error(f"[受信] WAV変換失敗: id={msg_id}")

        messenger.mark_as_played(message.get("id"))
        logger.info(f"[受信] メッセージ処理完了: id={msg_id}")

    except Exception as e:
        logger.error(f"[受信] メッセージ受信エラー: id={msg_id}, error={e}")


def transcribe_audio(wav_data: bytes) -> Optional[str]:
    """OpenAI Whisper APIで音声を文字起こし"""
    from openai import OpenAI

    try:
        client = OpenAI(api_key=Config.get_api_key())

        # 一時ファイルに保存
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_data)
            temp_path = f.name

        try:
            with open(temp_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="ja"
                )
            return transcript.text.strip() if transcript.text else None
        finally:
            os.unlink(temp_path)

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


def send_recorded_voice_message(client: OpenAIRealtimeClient) -> bool:
    """録音した音声をスマホに送信"""
    client.reset_voice_message_mode()

    try:
        wav_buffer = record_voice_message()

        if wav_buffer is None:
            return False

        wav_buffer.seek(0)
        wav_data = wav_buffer.read()
        transcribed_text = transcribe_audio(wav_data)

        messenger = get_firebase_messenger()
        if messenger and messenger.send_message(wav_data, text=transcribed_text):
            logger.info("音声メッセージ送信完了")
            return True
        else:
            return False

    except Exception:
        return False


async def audio_input_loop(client: OpenAIRealtimeClient, audio_handler: AudioHandler):
    """音声入力ループ"""
    global running, button, is_recording, _pending_incoming_call, last_button_press_time
    chunk_count = 0

    while running:
        # ビデオ通話中は音声入力を停止
        if is_in_videocall():
            await asyncio.sleep(0.1)
            continue

        if Config.USE_BUTTON and button:
            if button.is_pressed:
                if not is_recording:
                    # ダブルクリック検出（セッションリセット）
                    current_time = time.time()
                    if current_time - last_button_press_time < DOUBLE_CLICK_THRESHOLD:
                        logger.info("=== ダブルクリック: セッションリセット ===")
                        last_button_press_time = 0  # リセット後はタイムスタンプをクリア
                        client.needs_session_reset = True
                        client.last_response_time = None
                        client.last_audio_time = None
                        # リセット音を再生
                        reset_sound = generate_reset_sound()
                        if reset_sound:
                            audio_handler.play_audio_buffer(reset_sound)
                        # ボタンが離されるまで待つ
                        while button.is_pressed and running:
                            await asyncio.sleep(0.05)
                        await asyncio.sleep(0.2)
                        continue
                    last_button_press_time = current_time

                    # 着信中なら応答
                    if _pending_incoming_call:
                        logger.info("=== ビデオ通話応答 ===")
                        success = await accept_incoming_call()
                        if success:
                            logger.info("ビデオ通話接続成功")
                        else:
                            logger.warning("ビデオ通話接続失敗")
                        await asyncio.sleep(0.5)
                        continue

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
                        client.last_audio_time = None

                        # 音楽再生中なら停止してセッションリセット（ダブルクリックと同じ処理）
                        if is_music_active():
                            stop_music_player()
                            logger.info("=== 音楽停止: セッションリセット ===")
                            client.needs_session_reset = True
                            # ボタンが離されるまで待つ
                            while button.is_pressed and running:
                                await asyncio.sleep(0.05)
                            await asyncio.sleep(0.2)
                            # リセット音を再生（会話準備完了を通知）
                            reset_sound = generate_reset_sound()
                            if reset_sound:
                                audio_handler.play_audio_buffer(reset_sound)
                            continue

                        logger.info("=== 録音開始 ===")
                        chunk_count = 0

                        if audio_handler.start_input_stream():
                            is_recording = True
                            await client.send_activity_start()
                        else:
                            continue

                # 音声送信（ノンブロッキング）
                chunk = audio_handler.read_audio_chunk()
                if chunk and len(chunk) > 0:
                    chunk_count += 1
                    if chunk_count <= 3 or chunk_count % 20 == 0:
                        logger.debug(f"チャンク {chunk_count}: {len(chunk)} bytes")
                    await client.send_audio_chunk(chunk)
            else:
                if is_recording:
                    is_recording = False
                    audio_handler.stop_input_stream()
                    # 録音時間を計算（CHUNK_SIZE / INPUT_SAMPLE_RATE * チャンク数）
                    duration = chunk_count * Config.CHUNK_SIZE / Config.INPUT_SAMPLE_RATE
                    logger.info(f"=== 録音停止 ({chunk_count}チャンク, {duration:.1f}秒) ===")

                    # 最小録音時間チェック（0.5秒未満は短すぎる）
                    if duration < 0.5:
                        logger.warning("録音が短すぎます。バッファをクリアします。")
                        await client.clear_input_buffer()
                    else:
                        await client.send_activity_end()

        await asyncio.sleep(0.01)  # 10ms（raspi-voice3と同じ）


async def main_async():
    """非同期メインループ"""
    global running, button, audio_handler

    # ビデオ通話初期化（正しいイベントループで）
    loop = asyncio.get_running_loop()
    videocall_ok = init_videocall(loop)
    print(f"ビデオ通話: {'有効' if videocall_ok else '無効'}")

    audio_handler = AudioHandler()
    audio_handler.start_output_stream()

    # コールバック設定
    set_play_audio_callback(audio_handler.play_audio_buffer)
    set_music_audio_callbacks(
        stop_callback=audio_handler.stop_output_stream,
        start_callback=audio_handler.start_output_stream,
        play_callback=audio_handler.play_audio_buffer
    )

    client = OpenAIRealtimeClient(audio_handler)
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

    # プロアクティブリマインダー通知コールバック
    def reminder_notify(message: str):
        if client.is_connected:
            try:
                asyncio.run_coroutine_threadsafe(
                    client.send_text_message(message),
                    client.loop
                )
            except Exception:
                pass

    set_reminder_notify_callback(reminder_notify)
    start_reminder_thread()

    try:
        while running:
            # セッションタイムアウトチェック（voice_message_mode中・応答中はスキップ）
            if client.last_response_time and client.is_connected:
                if not client.check_voice_message_timeout() and not client.is_responding:
                    # 最後の応答時間と最後の音声再生時間の遅い方を基準にする
                    last_activity = client.last_response_time
                    if client.last_audio_time and client.last_audio_time > last_activity:
                        last_activity = client.last_audio_time
                    elapsed = time.time() - last_activity
                    if elapsed >= Config.SESSION_RESET_TIMEOUT:
                        logger.info("--- セッションリセット ---")
                        client.needs_session_reset = True
                        client.last_response_time = None
                        client.last_audio_time = None

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
                    print("raspi-voice7 起動 (OpenAI Realtime API)")
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
        stop_reminder_thread()
        stop_music_player()
        # OpenClawクリーンアップ
        close_openclaw_client()
        # ビデオ通話クリーンアップ
        if _signaling:
            _signaling.stop_listening()


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

    # ビデオ通話初期化（asyncio.run内で行う）
    # init_videocallはmain_async内で呼び出す

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
