"""
オーディオハンドラ

マイク入力・スピーカー出力の管理
"""

import io
import wave
import numpy as np
import pyaudio
from typing import Optional, Callable

from config import Config


def find_audio_device(p: pyaudio.PyAudio, device_type: str = "input") -> Optional[int]:
    """オーディオデバイスを自動検出"""
    input_target_names = ["usbmic", "USB PnP Sound", "USB Audio", "USB PnP Audio"]
    output_target_names = ["usbspk", "UACDemo", "USB Audio", "USB PnP Audio"]
    target_names = input_target_names if device_type == "input" else output_target_names

    # USBデバイスを探す
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        name = info.get("name", "")

        if device_type == "input" and info.get("maxInputChannels", 0) > 0:
            for target in target_names:
                if target in name:
                    return i
        elif device_type == "output" and info.get("maxOutputChannels", 0) > 0:
            for target in target_names:
                if target in name:
                    return i

    # フォールバック
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if device_type == "input" and info.get("maxInputChannels", 0) > 0:
            return i
        elif device_type == "output" and info.get("maxOutputChannels", 0) > 0:
            return i

    return None


def resample_audio(audio_data: bytes, from_rate: int, to_rate: int) -> bytes:
    """オーディオをリサンプリング"""
    if from_rate == to_rate:
        return audio_data

    audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)

    ratio = from_rate / to_rate
    if ratio == int(ratio) and ratio > 1:
        factor = int(ratio)
        trim_length = (len(audio_array) // factor) * factor
        trimmed = audio_array[:trim_length]
        resampled = trimmed.reshape(-1, factor).mean(axis=1)
    else:
        original_length = len(audio_array)
        target_length = int(original_length * to_rate / from_rate)
        indices = np.linspace(0, original_length - 1, target_length)
        resampled = np.interp(indices, np.arange(original_length), audio_array)

    return resampled.astype(np.int16).tobytes()


class AudioHandler:
    """オーディオ入出力ハンドラ"""

    def __init__(self):
        self.audio = pyaudio.PyAudio()
        self.input_stream = None
        self.output_stream = None
        self.is_recording = False
        self.is_playing = False

    def start_input_stream(self) -> bool:
        """マイク入力開始"""
        input_device = Config.INPUT_DEVICE_INDEX
        if input_device is None:
            input_device = find_audio_device(self.audio, "input")

        if input_device is None:
            return False

        try:
            self.input_stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=Config.CHANNELS,
                rate=Config.INPUT_SAMPLE_RATE,
                input=True,
                input_device_index=input_device,
                frames_per_buffer=Config.CHUNK_SIZE
            )
            self.is_recording = True
            return True
        except Exception:
            return False

    def stop_input_stream(self) -> None:
        """マイク入力停止"""
        if self.input_stream:
            self.is_recording = False
            try:
                self.input_stream.stop_stream()
                self.input_stream.close()
            except Exception:
                pass
            self.input_stream = None

    def read_audio_chunk(self) -> Optional[bytes]:
        """音声チャンクを読み取り（16kHzにリサンプリング）"""
        if self.input_stream and self.is_recording:
            try:
                data = self.input_stream.read(Config.CHUNK_SIZE, exception_on_overflow=False)
                return resample_audio(data, Config.INPUT_SAMPLE_RATE, Config.SEND_SAMPLE_RATE)
            except Exception:
                pass
        return None

    def start_output_stream(self) -> None:
        """スピーカー出力開始"""
        output_device = Config.OUTPUT_DEVICE_INDEX
        if output_device is None:
            output_device = find_audio_device(self.audio, "output")

        try:
            self.output_stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=Config.CHANNELS,
                rate=Config.OUTPUT_SAMPLE_RATE,
                output=True,
                output_device_index=output_device,
                frames_per_buffer=Config.CHUNK_SIZE * 2
            )
            self.is_playing = True
        except Exception:
            self.output_stream = None
            self.is_playing = False

    def stop_output_stream(self) -> None:
        """スピーカー出力停止"""
        if self.output_stream:
            self.is_playing = False
            try:
                self.output_stream.stop_stream()
                self.output_stream.close()
            except Exception:
                pass
            self.output_stream = None

    def play_audio_chunk(self, audio_data: bytes) -> None:
        """Gemini出力（24kHz）を48kHzにリサンプリングして再生"""
        if self.output_stream and self.is_playing:
            try:
                resampled = resample_audio(
                    audio_data, Config.RECEIVE_SAMPLE_RATE, Config.OUTPUT_SAMPLE_RATE
                )
                self.output_stream.write(resampled)
            except Exception:
                pass

    def play_audio_buffer(self, audio_data: bytes) -> None:
        """完全な音声バッファを再生（WAVデータ）"""
        if audio_data is None:
            return

        try:
            wav_buffer = io.BytesIO(audio_data)
            with wave.open(wav_buffer, 'rb') as wf:
                original_rate = wf.getframerate()
                frames = wf.readframes(wf.getnframes())

                if original_rate != Config.OUTPUT_SAMPLE_RATE:
                    audio_np = np.frombuffer(frames, dtype=np.int16)
                    ratio = Config.OUTPUT_SAMPLE_RATE / original_rate
                    new_length = int(len(audio_np) * ratio)
                    indices = np.linspace(0, len(audio_np) - 1, new_length).astype(int)
                    resampled = audio_np[indices]
                    frames = resampled.astype(np.int16).tobytes()

                if self.output_stream and self.is_playing:
                    chunk_size = 4096
                    for i in range(0, len(frames), chunk_size):
                        self.output_stream.write(frames[i:i+chunk_size])

        except Exception:
            pass

    def cleanup(self) -> None:
        """クリーンアップ"""
        self.stop_input_stream()
        self.stop_output_stream()
        if self.audio:
            self.audio.terminate()


def generate_startup_sound() -> Optional[bytes]:
    """起動完了音を生成（3音の上昇メロディ）"""
    try:
        sample_rate = 48000
        frequencies = [523, 659, 784]  # C5, E5, G5
        duration = 0.12
        gap_duration = 0.05

        sounds = []
        for i, freq in enumerate(frequencies):
            t = np.linspace(0, duration, int(sample_rate * duration), False)
            envelope = np.minimum(t / 0.02, 1) * np.minimum((duration - t) / 0.02, 1)
            tone = (np.sin(2 * np.pi * freq * t) * envelope * 0.35 * 32767).astype(np.int16)
            sounds.append(tone)
            if i < len(frequencies) - 1:
                gap = np.zeros(int(sample_rate * gap_duration), dtype=np.int16)
                sounds.append(gap)

        sound = np.concatenate(sounds)

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(sound.tobytes())

        return wav_buffer.getvalue()
    except Exception:
        return None


def generate_notification_sound() -> Optional[bytes]:
    """通知音を生成"""
    try:
        sample_rate = 48000
        duration1 = 0.15
        duration2 = 0.1

        t1 = np.linspace(0, duration1, int(sample_rate * duration1), False)
        tone1 = (np.sin(2 * np.pi * 880 * t1) * 0.3 * 32767).astype(np.int16)

        gap = np.zeros(int(sample_rate * 0.1), dtype=np.int16)

        t2 = np.linspace(0, duration2, int(sample_rate * duration2), False)
        tone2 = (np.sin(2 * np.pi * 1320 * t2) * 0.2 * 32767).astype(np.int16)

        sound = np.concatenate([tone1, gap, tone2])

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(sound.tobytes())

        return wav_buffer.getvalue()
    except Exception:
        return None
