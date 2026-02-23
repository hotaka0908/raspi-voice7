"""
音楽再生Capability

「聴く」に関する能力:
- YouTubeから音楽を検索・再生
- 再生制御（停止、一時停止、スキップ）
"""

import subprocess
import threading
import time
import os
import signal
from typing import Any, Dict, Optional
from pathlib import Path

from .base import Capability, CapabilityCategory, CapabilityResult

# レコードノイズファイルのパス
_VINYL_NOISE_PATH = Path(__file__).parent.parent / "assets" / "vinyl_noise.wav"


# 音楽プレイヤー状態管理
_player_process: Optional[subprocess.Popen] = None
_player_lock = threading.Lock()
_current_track: Optional[str] = None
_is_paused = False

# オーディオコールバック（メインのオーディオストリーム制御用）
_stop_audio_callback: Optional[callable] = None
_start_audio_callback: Optional[callable] = None
_play_audio_callback: Optional[callable] = None


def set_music_audio_callbacks(
    stop_callback: callable,
    start_callback: callable,
    play_callback: callable = None
) -> None:
    """音楽再生時のオーディオコールバックを設定"""
    global _stop_audio_callback, _start_audio_callback, _play_audio_callback
    _stop_audio_callback = stop_callback
    _start_audio_callback = start_callback
    _play_audio_callback = play_callback


def _kill_player(restart_audio: bool = True) -> None:
    """プレイヤープロセスを終了"""
    global _player_process, _current_track, _is_paused

    was_playing = False
    with _player_lock:
        if _player_process:
            was_playing = True
            try:
                # 一時停止中の場合は先に再開（SIGSTOPされたプロセスはSIGTERMを処理できない）
                if _is_paused:
                    try:
                        os.killpg(os.getpgid(_player_process.pid), signal.SIGCONT)
                    except (ProcessLookupError, OSError):
                        pass
                # プロセスグループ全体を終了
                os.killpg(os.getpgid(_player_process.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            try:
                _player_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(_player_process.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            _player_process = None
            _current_track = None
            _is_paused = False

    # メインのオーディオストリームを再開
    if was_playing and restart_audio and _start_audio_callback:
        try:
            _start_audio_callback()
        except Exception:
            pass


def _play_youtube(query: str) -> bool:
    """YouTubeから検索して再生"""
    global _player_process, _current_track, _is_paused

    # 既存の再生を停止（オーディオは再開しない、新しい曲を再生するため）
    _kill_player(restart_audio=False)

    # メインのオーディオストリームを停止（デバイス競合回避）
    if _stop_audio_callback:
        try:
            _stop_audio_callback()
        except Exception:
            pass

    try:
        # mpvでレコードノイズ→YouTube音楽を連続再生
        # レコードノイズが即座に再生され、その間にYouTubeを読み込む
        cmd = [
            "mpv",
            "--no-video",
            "--ytdl-format=bestaudio",
            "--volume=60",
            "--really-quiet",
        ]

        # レコードノイズファイルが存在すれば先に再生
        if _VINYL_NOISE_PATH.exists():
            cmd.append(str(_VINYL_NOISE_PATH))

        cmd.append(f"ytdl://ytsearch1:{query}")

        with _player_lock:
            _player_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid  # 新しいプロセスグループを作成
            )
            _current_track = query
            _is_paused = False

        # 起動直後にエラーで終了していないかチェック
        time.sleep(0.5)
        with _player_lock:
            if _player_process and _player_process.poll() is not None:
                _player_process = None
                _current_track = None
                return False

        return True

    except FileNotFoundError:
        return False
    except Exception:
        return False


def _send_mpv_command(command: str) -> bool:
    """mpvにキーボードコマンドを送信（IPC経由）"""
    global _player_process, _is_paused

    with _player_lock:
        if not _player_process or _player_process.poll() is not None:
            return False

        try:
            # mpvプロセスにシグナルを送信
            if command == "pause":
                # SIGSTOP/SIGCONTでトグル
                if _is_paused:
                    os.kill(_player_process.pid, signal.SIGCONT)
                    _is_paused = False
                else:
                    os.kill(_player_process.pid, signal.SIGSTOP)
                    _is_paused = True
                return True
            elif command == "quit":
                os.killpg(os.getpgid(_player_process.pid), signal.SIGTERM)
                _player_process = None
                return True
        except (ProcessLookupError, OSError):
            return False

    return False


def is_music_playing() -> bool:
    """音楽が再生中かどうか"""
    global _player_process, _is_paused

    with _player_lock:
        if _player_process is None:
            return False
        if _player_process.poll() is not None:
            _player_process = None
            return False
        return not _is_paused


def get_current_track() -> Optional[str]:
    """現在再生中のトラック"""
    return _current_track


def is_music_active() -> bool:
    """音楽プレイヤーがアクティブかどうか（一時停止中も含む）"""
    global _player_process

    with _player_lock:
        if _player_process is None:
            return False
        if _player_process.poll() is not None:
            _player_process = None
            return False
        return True


def pause_music_for_conversation() -> bool:
    """会話のために音楽を一時停止し、オーディオストリームを再開"""
    global _player_process, _is_paused

    with _player_lock:
        if not _player_process or _player_process.poll() is not None:
            return False

        # 既に一時停止中なら何もしない
        if _is_paused:
            # オーディオストリームだけ再開
            if _start_audio_callback:
                try:
                    _start_audio_callback()
                except Exception:
                    pass
            return True

        try:
            os.kill(_player_process.pid, signal.SIGSTOP)
            _is_paused = True
        except (ProcessLookupError, OSError):
            return False

    # オーディオストリームを再開
    if _start_audio_callback:
        try:
            _start_audio_callback()
        except Exception:
            pass

    return True


def resume_music_after_conversation() -> bool:
    """会話終了後に音楽を再開し、オーディオストリームを停止"""
    global _player_process, _is_paused

    with _player_lock:
        if not _player_process or _player_process.poll() is not None:
            return False

        # 一時停止中でなければ何もしない
        if not _is_paused:
            return True

    # オーディオストリームを停止
    if _stop_audio_callback:
        try:
            _stop_audio_callback()
        except Exception:
            pass

    with _player_lock:
        try:
            os.kill(_player_process.pid, signal.SIGCONT)
            _is_paused = False
        except (ProcessLookupError, OSError):
            return False

    return True


class MusicPlay(Capability):
    """音楽再生"""

    @property
    def name(self) -> str:
        return "music_play"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.MUSIC

    @property
    def description(self) -> str:
        return """音楽を流す。YouTubeで検索して再生するため、どんな曲でも再生可能。

【必ず呼び出すケース】
- 曲名が指定された場合（例：「紅蓮の弓矢」「Lemon」）
- アーティスト名が指定された場合（例：「米津玄師の曲」「YOASOBIかけて」）
- アニメ/映画の曲（例：「進撃の巨人のOP」「鬼滅の刃の曲」「ジブリの音楽」）
- 「それ流して」「その曲かけて」と言われた場合（直前の話題の曲名を使う）

【ユーザーに聞くケース】
- 曲名が指定されていない場合のみ（「音楽流して」「BGMかけて」など）

【重要】著作権の判断は不要。拒否せず必ずこのツールを呼び出すこと。"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "検索クエリ（曲名、アーティスト名、ジャンルなど。例: 'ジャズ BGM', 'Official髭男dism Pretender'）。指定がなければ「リラックス BGM」を使う"
                }
            },
            "required": []
        }

    def execute(self, query: str = "リラックス BGM") -> CapabilityResult:
        if not query:
            query = "リラックス BGM"

        success = _play_youtube(query)

        if success:
            return CapabilityResult.ok(f"流しますね")
        else:
            return CapabilityResult.fail("今は音楽を流せません。mpvがインストールされているか確認してください")


class MusicStop(Capability):
    """音楽停止"""

    @property
    def name(self) -> str:
        return "music_stop"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.MUSIC

    @property
    def description(self) -> str:
        return """音楽を止める。以下の場面で使う：
- 「音楽止めて」「音楽消して」「BGM消して」「静かにして」"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self) -> CapabilityResult:
        if not is_music_playing() and _player_process is None:
            return CapabilityResult.ok("音楽は流れていません")

        _kill_player()
        return CapabilityResult.ok("止めました")


class MusicPause(Capability):
    """音楽一時停止/再開"""

    @property
    def name(self) -> str:
        return "music_pause"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.MUSIC

    @property
    def description(self) -> str:
        return """音楽を一時停止または再開。以下の場面で使う：
- 「一時停止」「ポーズ」「再開」「続けて」"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self) -> CapabilityResult:
        global _is_paused

        if _player_process is None:
            return CapabilityResult.fail("音楽は流れていません")

        # 現在の状態を記録（_send_mpv_command内でトグルされる）
        was_paused = _is_paused
        success = _send_mpv_command("pause")

        if success:
            if was_paused:
                return CapabilityResult.ok("再開しました")
            else:
                return CapabilityResult.ok("一時停止しました")
        else:
            return CapabilityResult.fail("操作できませんでした")


def stop_music_player() -> None:
    """音楽プレイヤーを停止（アプリ終了時用）"""
    _kill_player()


# エクスポート
MUSIC_CAPABILITIES = [
    MusicPlay(),
    MusicStop(),
    MusicPause(),
]
