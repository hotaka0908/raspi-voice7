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
import re
import numpy as np
from typing import Optional, Callable, Dict, Any
from fractions import Fraction

from config import Config

logger = logging.getLogger("conversation")


def parse_ice_candidate(candidate_str: str) -> Optional[Dict]:
    """ICE候補文字列をパースしてaiortc用のパラメータを抽出

    例: candidate:3490444625 1 udp 2113937151 192.168.11.2 54321 typ host ...
    """
    if not candidate_str:
        return None

    # "candidate:" プレフィックスを除去
    if candidate_str.startswith("candidate:"):
        candidate_str = candidate_str[10:]

    parts = candidate_str.split()
    if len(parts) < 8:
        return None

    try:
        result = {
            "foundation": parts[0],
            "component": int(parts[1]),
            "protocol": parts[2].lower(),
            "priority": int(parts[3]),
            "ip": parts[4],
            "port": int(parts[5]),
            # parts[6] は "typ"
            "type": parts[7],
        }

        # オプションパラメータ
        for i in range(8, len(parts) - 1, 2):
            key = parts[i]
            val = parts[i + 1]
            if key == "raddr":
                result["relatedAddress"] = val
            elif key == "rport":
                result["relatedPort"] = int(val)
            elif key == "tcptype":
                result["tcpType"] = val

        return result
    except (ValueError, IndexError) as e:
        logger.debug(f"ICE候補パースエラー: {e}")
        return None

# aiortcのデバッグログを有効化
logging.getLogger("aioice").setLevel(logging.DEBUG)
logging.getLogger("aiortc").setLevel(logging.DEBUG)

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

        # YUV420データを読み込み（非同期で実行）
        frame_size = Config.VIDEO_WIDTH * Config.VIDEO_HEIGHT * 3 // 2
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._process.stdout.read, frame_size)
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
            # 非同期で読み込み
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None,
                lambda: self._stream.read(self._samples_per_frame, exception_on_overflow=False)
            )
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

        # 保留中のICE候補（PC作成前に受信した場合）
        self._pending_ice_candidates: list = []

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
                ice_state = self.pc.iceConnectionState
                logger.info(f"接続状態: {state}, ICE状態: {ice_state}")
                if self.on_connection_state_change:
                    self.on_connection_state_change(state)

                if state == "connected":
                    self.is_in_call = True
                elif state in ("failed", "closed", "disconnected"):
                    self.is_in_call = False

            @self.pc.on("iceconnectionstatechange")
            async def on_ice_connection_state():
                logger.info(f"ICE接続状態変更: {self.pc.iceConnectionState}")

            @self.pc.on("icegatheringstatechange")
            async def on_ice_gathering_state():
                logger.info(f"ICE収集状態変更: {self.pc.iceGatheringState}")

            @self.pc.on("icecandidate")
            async def on_ice_candidate(candidate):
                logger.info(f"ICE候補イベント発火: candidate={candidate}")
                if candidate:
                    logger.info(f"ICE候補詳細: candidate.candidate={getattr(candidate, 'candidate', None)}")
                if candidate and hasattr(candidate, 'candidate') and candidate.candidate and self.on_ice_candidate:
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
        logger.info(f"リモートオーディオ再生開始: is_in_call={self.is_in_call}")

        # is_in_callがTrueになるまで少し待つ（接続完了前にon_trackが発火する場合）
        for _ in range(50):  # 最大5秒待機
            if self.is_in_call:
                break
            await asyncio.sleep(0.1)

        if not self.is_in_call:
            logger.warning("リモートオーディオ再生: is_in_callがFalseのため中止")
            return

        try:
            import pyaudio
            self._audio_output = pyaudio.PyAudio()
            self._output_stream = self._audio_output.open(
                format=pyaudio.paInt16,
                channels=2,  # ステレオ（スマホからのオーディオはステレオ）
                rate=48000,
                output=True,
                output_device_index=Config.OUTPUT_DEVICE_INDEX,
                frames_per_buffer=960
            )
            logger.info("リモートオーディオ出力ストリーム開始（ステレオ）")

            frame_count = 0
            while self.is_in_call:
                try:
                    frame = await asyncio.wait_for(track.recv(), timeout=1.0)
                    frame_count += 1

                    # PyAVフレームからバイト列を取得
                    if hasattr(frame, 'to_ndarray'):
                        audio_array = frame.to_ndarray()
                        audio_data = audio_array.tobytes()
                    else:
                        audio_data = bytes(frame.planes[0])

                    if frame_count <= 3:
                        logger.info(f"リモートオーディオフレーム {frame_count}: "
                                    f"samples={getattr(frame, 'samples', '?')}, "
                                    f"rate={getattr(frame, 'sample_rate', '?')}, "
                                    f"format={getattr(frame, 'format', '?')}, "
                                    f"data_len={len(audio_data)}")
                    elif frame_count % 100 == 0:
                        logger.info(f"リモートオーディオフレーム受信: {frame_count}")

                    if audio_data and self._output_stream:
                        self._output_stream.write(audio_data)
                except asyncio.TimeoutError:
                    logger.debug(f"リモートオーディオ: タイムアウト (is_in_call={self.is_in_call})")
                    continue
                except Exception as e:
                    logger.warning(f"リモートオーディオ再生エラー: {e}")
                    break

            logger.info(f"リモートオーディオ再生終了: {frame_count}フレーム受信")

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

            # ICE収集完了を待つ（aiortcはicecandidateイベントを発火しないため）
            logger.info(f"ICE収集開始: {self.pc.iceGatheringState}")
            timeout = 5.0
            elapsed = 0.0
            while self.pc.iceGatheringState != "complete" and elapsed < timeout:
                await asyncio.sleep(0.1)
                elapsed += 0.1

            logger.info(f"ICE収集完了/タイムアウト: {self.pc.iceGatheringState}, 経過={elapsed:.1f}秒")

            # 最新のローカルSDP（ICE候補含む）を取得
            local_desc = self.pc.localDescription

            # ICE候補の数をカウント
            ice_count = local_desc.sdp.count('a=candidate:')
            logger.info(f"Offer SDPに含まれるICE候補数: {ice_count}")

            # SDPからICE候補を抽出してFirebaseに送信
            if self.on_ice_candidate:
                await self._send_ice_candidates_from_sdp(local_desc.sdp)

            return {
                "type": local_desc.type,
                "sdp": local_desc.sdp,
            }
        except Exception as e:
            logger.error(f"Offer作成エラー: {e}")
            return None

    async def handle_offer(self, offer: Dict) -> Optional[Dict]:
        """Offer SDPを処理してAnswerを作成"""
        if not self.pc:
            return None

        try:
            # Offer SDPをログに出力（最初の10行）
            offer_lines = offer["sdp"].split('\n')[:10]
            logger.info(f"受信Offer SDP（先頭）:\n" + '\n'.join(offer_lines))

            rtc_offer = RTCSessionDescription(
                sdp=offer["sdp"],
                type=offer["type"]
            )
            await self.pc.setRemoteDescription(rtc_offer)
            logger.info("setRemoteDescription完了")

            # 保留中のICE候補を処理
            await self._process_pending_ice_candidates()

            answer = await self.pc.createAnswer()
            await self.pc.setLocalDescription(answer)

            # ICE収集完了を待つ（候補はicecandidateイベントでも送信される）
            logger.info(f"ICE収集開始: {self.pc.iceGatheringState}")
            timeout = 5.0  # 最大5秒待機
            elapsed = 0.0
            while self.pc.iceGatheringState != "complete" and elapsed < timeout:
                await asyncio.sleep(0.1)
                elapsed += 0.1

            logger.info(f"ICE収集完了/タイムアウト: {self.pc.iceGatheringState}, 経過={elapsed:.1f}秒")

            # 最新のローカルSDP（ICE候補含む）を取得
            local_desc = self.pc.localDescription

            # Answer SDPをログに出力（最初の15行）
            answer_lines = local_desc.sdp.split('\n')[:15]
            logger.info(f"生成Answer SDP（先頭）:\n" + '\n'.join(answer_lines))

            # ICE候補の数をカウント
            ice_count = local_desc.sdp.count('a=candidate:')
            logger.info(f"Answer SDPに含まれるICE候補数: {ice_count}")

            # SDPからICE候補を抽出してFirebaseに送信（aiortcがicecandidateイベントを発火しないため）
            if self.on_ice_candidate:
                await self._send_ice_candidates_from_sdp(local_desc.sdp)

            return {
                "type": local_desc.type,
                "sdp": local_desc.sdp,
            }
        except Exception as e:
            logger.error(f"Offer処理エラー: {e}")
            return None

    async def _send_ice_candidates_from_sdp(self, sdp: str) -> None:
        """SDPからICE候補を抽出してFirebaseに送信"""
        lines = sdp.split('\n')
        current_mid = None
        current_mline_index = -1

        for line in lines:
            line = line.strip()
            # メディアライン（m=audio, m=video）を追跡
            if line.startswith('m='):
                current_mline_index += 1
                current_mid = str(current_mline_index)

            # mid属性を取得
            if line.startswith('a=mid:'):
                current_mid = line[6:]

            # ICE候補を抽出
            if line.startswith('a=candidate:'):
                candidate_str = line[2:]  # "a=" を除去
                logger.info(f"SDP ICE候補送信: mid={current_mid}, index={current_mline_index}, candidate={candidate_str[:60]}...")
                if self.on_ice_candidate:
                    self.on_ice_candidate({
                        "candidate": candidate_str,
                        "sdpMid": current_mid,
                        "sdpMLineIndex": current_mline_index,
                    })

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
        candidate_str = candidate.get("candidate", "")
        logger.info(f"ICE候補追加試行: {candidate_str[:60]}...")

        if not self.pc:
            # PeerConnectionがまだない場合はキューに追加
            self._pending_ice_candidates.append(candidate)
            logger.info(f"ICE候補をキューに追加（PC未作成）: {len(self._pending_ice_candidates)}件")
            return True

        # remoteDescriptionが設定されているか確認
        if not self.pc.remoteDescription:
            self._pending_ice_candidates.append(candidate)
            logger.info(f"ICE候補をキューに追加（remoteDescription未設定）: {len(self._pending_ice_candidates)}件")
            return True

        try:
            # ICE候補文字列をパース
            parsed = parse_ice_candidate(candidate_str)
            if not parsed:
                logger.warning(f"ICE候補パース失敗: {candidate_str}")
                return False

            rtc_candidate = RTCIceCandidate(
                foundation=parsed["foundation"],
                component=parsed["component"],
                protocol=parsed["protocol"],
                priority=parsed["priority"],
                ip=parsed["ip"],
                port=parsed["port"],
                type=parsed["type"],
                relatedAddress=parsed.get("relatedAddress"),
                relatedPort=parsed.get("relatedPort"),
                tcpType=parsed.get("tcpType"),
                sdpMid=candidate.get("sdpMid"),
                sdpMLineIndex=candidate.get("sdpMLineIndex"),
            )
            await self.pc.addIceCandidate(rtc_candidate)
            logger.info(f"ICE候補追加成功: {parsed['ip']}:{parsed['port']} ({parsed['type']})")
            return True
        except Exception as e:
            logger.warning(f"ICE候補追加エラー: {e}")
            return False

    async def _process_pending_ice_candidates(self) -> None:
        """保留中のICE候補を処理"""
        if not self._pending_ice_candidates:
            return

        logger.info(f"保留中のICE候補を処理: {len(self._pending_ice_candidates)}件")
        success_count = 0
        fail_count = 0
        for candidate in self._pending_ice_candidates:
            try:
                candidate_str = candidate.get("candidate", "")
                parsed = parse_ice_candidate(candidate_str)
                if not parsed:
                    fail_count += 1
                    logger.warning(f"ICE候補パース失敗: {candidate_str[:50]}")
                    continue

                rtc_candidate = RTCIceCandidate(
                    foundation=parsed["foundation"],
                    component=parsed["component"],
                    protocol=parsed["protocol"],
                    priority=parsed["priority"],
                    ip=parsed["ip"],
                    port=parsed["port"],
                    type=parsed["type"],
                    relatedAddress=parsed.get("relatedAddress"),
                    relatedPort=parsed.get("relatedPort"),
                    tcpType=parsed.get("tcpType"),
                    sdpMid=candidate.get("sdpMid"),
                    sdpMLineIndex=candidate.get("sdpMLineIndex"),
                )
                await self.pc.addIceCandidate(rtc_candidate)
                success_count += 1
            except Exception as e:
                fail_count += 1
                logger.warning(f"保留ICE候補追加エラー: {e}")
        logger.info(f"保留ICE候補処理完了: 成功={success_count}, 失敗={fail_count}")

        self._pending_ice_candidates.clear()

    async def end_call(self) -> None:
        """通話終了"""
        self.is_in_call = False

        # 保留中のICE候補をクリア
        self._pending_ice_candidates.clear()

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
