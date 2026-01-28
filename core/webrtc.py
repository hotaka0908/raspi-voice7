"""
WebRTC Video Call Manager

aiortcを使用したWebRTCビデオ通話管理
ラズパイのカメラ/マイクを使用して双方向通話を実現
"""

import asyncio
import logging
import subprocess
import io
import wave
import numpy as np
from typing import Optional, Callable, Dict, Any
from fractions import Fraction

from config import Config

logger = logging.getLogger("conversation")

# aiortcのインポート（インストールされていない場合のフォールバック）
try:
    from aiortc import (
        RTCPeerConnection,
        RTCSessionDescription,
        RTCIceCandidate,
        RTCConfiguration,
        RTCIceServer,
        MediaStreamTrack,
    )
    from aiortc.contrib.media import MediaPlayer, MediaRecorder
    from av import VideoFrame, AudioFrame
    AIORTC_AVAILABLE = True
except ImportError:
    AIORTC_AVAILABLE = False
    logger.warning("aiortcがインストールされていません。WebRTCは無効です。")

    # ダミークラス（aiortc未インストール時）
    class MediaStreamTrack:
        kind = "unknown"
        def __init__(self): pass
        async def recv(self): pass

    class VideoFrame:
        def __init__(self, **kwargs): pass

    class AudioFrame:
        def __init__(self, **kwargs): pass

    class RTCConfiguration:
        def __init__(self, **kwargs): pass

    class RTCIceServer:
        def __init__(self, **kwargs): pass

    class RTCPeerConnection:
        def __init__(self, **kwargs): pass

    class RTCSessionDescription:
        def __init__(self, **kwargs): pass

    class RTCIceCandidate:
        def __init__(self, **kwargs): pass


class CameraVideoTrack(MediaStreamTrack):
    """Raspberry Piカメラからのビデオトラック"""

    kind = "video"

    def __init__(self):
        super().__init__()
        self._process = None
        self._running = False
        self._frame_count = 0

    async def start(self):
        """カメラ開始"""
        if self._running:
            return

        self._running = True
        # rpicam-vidでRAW出力（YUV420）
        cmd = [
            "rpicam-vid",
            "-t", "0",  # 無限
            "--width", str(Config.VIDEO_WIDTH),
            "--height", str(Config.VIDEO_HEIGHT),
            "--framerate", str(Config.VIDEO_FPS),
            "--codec", "yuv420",
            "-o", "-",  # stdout
            "-n",  # プレビューなし
        ]

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=Config.VIDEO_WIDTH * Config.VIDEO_HEIGHT * 3 // 2
            )
            logger.info("カメラストリーム開始")
        except Exception as e:
            logger.error(f"カメラ起動エラー: {e}")
            self._running = False

    async def stop(self):
        """カメラ停止"""
        self._running = False
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        logger.info("カメラストリーム停止")

    async def recv(self):
        """フレーム取得"""
        if not self._running or not self._process:
            await asyncio.sleep(1.0 / Config.VIDEO_FPS)
            # 黒フレームを返す
            frame = VideoFrame(width=Config.VIDEO_WIDTH, height=Config.VIDEO_HEIGHT, format="yuv420p")
            frame.pts = self._frame_count
            frame.time_base = Fraction(1, Config.VIDEO_FPS)
            self._frame_count += 1
            return frame

        # YUV420データを読み込み
        frame_size = Config.VIDEO_WIDTH * Config.VIDEO_HEIGHT * 3 // 2
        try:
            data = self._process.stdout.read(frame_size)
            if len(data) != frame_size:
                await asyncio.sleep(0.01)
                return await self.recv()

            frame = VideoFrame(width=Config.VIDEO_WIDTH, height=Config.VIDEO_HEIGHT, format="yuv420p")
            frame.planes[0].update(data[:Config.VIDEO_WIDTH * Config.VIDEO_HEIGHT])
            frame.planes[1].update(data[Config.VIDEO_WIDTH * Config.VIDEO_HEIGHT:Config.VIDEO_WIDTH * Config.VIDEO_HEIGHT * 5 // 4])
            frame.planes[2].update(data[Config.VIDEO_WIDTH * Config.VIDEO_HEIGHT * 5 // 4:])
            frame.pts = self._frame_count
            frame.time_base = Fraction(1, Config.VIDEO_FPS)
            self._frame_count += 1
            return frame

        except Exception as e:
            logger.debug(f"フレーム読み取りエラー: {e}")
            await asyncio.sleep(0.01)
            return await self.recv()


class AudioTrackFromDevice(MediaStreamTrack):
    """USBマイクからのオーディオトラック"""

    kind = "audio"

    def __init__(self):
        super().__init__()
        self._running = False
        self._audio = None
        self._stream = None
        self._sample_rate = 48000
        self._channels = 1
        self._samples_per_frame = 960  # 20ms at 48kHz
        self._pts = 0

    async def start(self):
        """マイク開始"""
        if self._running:
            return

        try:
            import pyaudio
            self._audio = pyaudio.PyAudio()
            self._stream = self._audio.open(
                format=pyaudio.paInt16,
                channels=self._channels,
                rate=self._sample_rate,
                input=True,
                input_device_index=Config.INPUT_DEVICE_INDEX,
                frames_per_buffer=self._samples_per_frame
            )
            self._running = True
            logger.info("マイクストリーム開始")
        except Exception as e:
            logger.error(f"マイク起動エラー: {e}")
            self._running = False

    async def stop(self):
        """マイク停止"""
        self._running = False
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._audio:
            try:
                self._audio.terminate()
            except Exception:
                pass
            self._audio = None
        logger.info("マイクストリーム停止")

    async def recv(self):
        """オーディオフレーム取得"""
        if not self._running or not self._stream:
            await asyncio.sleep(0.02)
            # 無音フレームを返す
            frame = AudioFrame(format="s16", layout="mono", samples=self._samples_per_frame)
            frame.sample_rate = self._sample_rate
            frame.pts = self._pts
            frame.time_base = Fraction(1, self._sample_rate)
            self._pts += self._samples_per_frame
            return frame

        try:
            data = self._stream.read(self._samples_per_frame, exception_on_overflow=False)
            audio_data = np.frombuffer(data, dtype=np.int16)

            frame = AudioFrame(format="s16", layout="mono", samples=len(audio_data))
            frame.sample_rate = self._sample_rate
            frame.planes[0].update(audio_data.tobytes())
            frame.pts = self._pts
            frame.time_base = Fraction(1, self._sample_rate)
            self._pts += len(audio_data)
            return frame

        except Exception as e:
            logger.debug(f"オーディオ読み取りエラー: {e}")
            await asyncio.sleep(0.02)
            return await self.recv()


class VideoCallManager:
    """WebRTCビデオ通話マネージャー"""

    def __init__(self):
        self.pc: Optional[RTCPeerConnection] = None
        self.video_track: Optional[CameraVideoTrack] = None
        self.audio_track: Optional[AudioTrackFromDevice] = None
        self.is_in_call = False
        self._remote_audio_track = None

        # コールバック
        self.on_remote_track: Optional[Callable[[MediaStreamTrack], None]] = None
        self.on_connection_state_change: Optional[Callable[[str], None]] = None
        self.on_ice_candidate: Optional[Callable[[Dict], None]] = None

        # オーディオ出力
        self._audio_output = None
        self._output_stream = None

    def _get_rtc_configuration(self) -> RTCConfiguration:
        """RTCConfiguration作成"""
        ice_servers = []
        for server in Config.ICE_SERVERS:
            urls = server.get("urls")
            username = server.get("username")
            credential = server.get("credential")

            if username and credential:
                ice_servers.append(RTCIceServer(
                    urls=urls,
                    username=username,
                    credential=credential
                ))
            else:
                ice_servers.append(RTCIceServer(urls=urls))

        return RTCConfiguration(iceServers=ice_servers)

    async def create_peer_connection(self) -> bool:
        """PeerConnection作成"""
        if not AIORTC_AVAILABLE:
            logger.error("aiortcが利用できません")
            return False

        try:
            config = self._get_rtc_configuration()
            self.pc = RTCPeerConnection(configuration=config)

            @self.pc.on("connectionstatechange")
            async def on_connection_state():
                state = self.pc.connectionState
                logger.info(f"接続状態: {state}")
                if self.on_connection_state_change:
                    self.on_connection_state_change(state)

                if state == "connected":
                    self.is_in_call = True
                elif state in ("failed", "closed", "disconnected"):
                    self.is_in_call = False

            @self.pc.on("icecandidate")
            async def on_ice_candidate(candidate):
                logger.info(f"ICE候補イベント: {candidate}")
                if candidate and candidate.candidate and self.on_ice_candidate:
                    logger.info(f"ICE候補送信: {candidate.candidate[:50]}...")
                    self.on_ice_candidate({
                        "candidate": candidate.candidate,
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex,
                    })

            @self.pc.on("track")
            async def on_track(track):
                logger.info(f"リモートトラック受信: {track.kind}")
                if track.kind == "audio":
                    self._remote_audio_track = track
                    asyncio.create_task(self._play_remote_audio(track))
                if self.on_remote_track:
                    self.on_remote_track(track)

            return True

        except Exception as e:
            logger.error(f"PeerConnection作成エラー: {e}")
            return False

    async def _play_remote_audio(self, track: MediaStreamTrack):
        """リモートオーディオを再生"""
        try:
            import pyaudio
            self._audio_output = pyaudio.PyAudio()
            self._output_stream = self._audio_output.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=48000,
                output=True,
                output_device_index=Config.OUTPUT_DEVICE_INDEX,
                frames_per_buffer=960
            )

            while self.is_in_call:
                try:
                    frame = await asyncio.wait_for(track.recv(), timeout=1.0)
                    # PyAVフレームからバイト列を取得
                    if hasattr(frame, 'to_ndarray'):
                        audio_data = frame.to_ndarray().tobytes()
                    else:
                        audio_data = bytes(frame.planes[0])

                    if audio_data and self._output_stream:
                        self._output_stream.write(audio_data)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.debug(f"リモートオーディオ再生エラー: {e}")
                    break

        except Exception as e:
            logger.error(f"オーディオ出力エラー: {e}")
        finally:
            if self._output_stream:
                try:
                    self._output_stream.stop_stream()
                    self._output_stream.close()
                except Exception:
                    pass
            if self._audio_output:
                try:
                    self._audio_output.terminate()
                except Exception:
                    pass

    async def start_local_media(self) -> bool:
        """ローカルメディア開始"""
        if not self.pc:
            return False

        try:
            # ビデオトラック
            self.video_track = CameraVideoTrack()
            await self.video_track.start()
            self.pc.addTrack(self.video_track)

            # オーディオトラック
            self.audio_track = AudioTrackFromDevice()
            await self.audio_track.start()
            self.pc.addTrack(self.audio_track)

            logger.info("ローカルメディア開始")
            return True

        except Exception as e:
            logger.error(f"ローカルメディア開始エラー: {e}")
            return False

    async def create_offer(self) -> Optional[Dict]:
        """Offer SDP作成"""
        if not self.pc:
            return None

        try:
            offer = await self.pc.createOffer()
            await self.pc.setLocalDescription(offer)
            return {
                "type": offer.type,
                "sdp": offer.sdp,
            }
        except Exception as e:
            logger.error(f"Offer作成エラー: {e}")
            return None

    async def handle_offer(self, offer: Dict) -> Optional[Dict]:
        """Offer SDPを処理してAnswerを作成"""
        if not self.pc:
            return None

        try:
            rtc_offer = RTCSessionDescription(
                sdp=offer["sdp"],
                type=offer["type"]
            )
            await self.pc.setRemoteDescription(rtc_offer)

            answer = await self.pc.createAnswer()
            await self.pc.setLocalDescription(answer)

            return {
                "type": answer.type,
                "sdp": answer.sdp,
            }
        except Exception as e:
            logger.error(f"Offer処理エラー: {e}")
            return None

    async def handle_answer(self, answer: Dict) -> bool:
        """Answer SDPを処理"""
        if not self.pc:
            return False

        try:
            rtc_answer = RTCSessionDescription(
                sdp=answer["sdp"],
                type=answer["type"]
            )
            await self.pc.setRemoteDescription(rtc_answer)
            return True
        except Exception as e:
            logger.error(f"Answer処理エラー: {e}")
            return False

    async def add_ice_candidate(self, candidate: Dict) -> bool:
        """ICE候補追加"""
        if not self.pc:
            return False

        try:
            rtc_candidate = RTCIceCandidate(
                sdpMid=candidate.get("sdpMid"),
                sdpMLineIndex=candidate.get("sdpMLineIndex"),
                candidate=candidate.get("candidate"),
            )
            await self.pc.addIceCandidate(rtc_candidate)
            return True
        except Exception as e:
            logger.debug(f"ICE候補追加エラー: {e}")
            return False

    async def end_call(self) -> None:
        """通話終了"""
        self.is_in_call = False

        if self.video_track:
            await self.video_track.stop()
            self.video_track = None

        if self.audio_track:
            await self.audio_track.stop()
            self.audio_track = None

        if self.pc:
            await self.pc.close()
            self.pc = None

        logger.info("ビデオ通話終了")


# シングルトン
_video_call_manager: Optional[VideoCallManager] = None


def get_video_call_manager() -> VideoCallManager:
    """VideoCallManagerのシングルトンを取得"""
    global _video_call_manager
    if _video_call_manager is None:
        _video_call_manager = VideoCallManager()
    return _video_call_manager
