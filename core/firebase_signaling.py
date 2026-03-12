"""
Firebase Signaling Module for WebRTC

ラズパイとスマホ間のWebRTCシグナリングを行うモジュール
Firebase Realtime Database を使用してSDP/ICE候補を交換
"""

import os
import time
import threading
import logging
from typing import Optional, Callable, Dict, Any
from dotenv import load_dotenv
import requests

# 環境変数の読み込み
env_path = os.path.expanduser("~/.ai-necklace/.env")
load_dotenv(env_path)

logger = logging.getLogger("conversation")

# Firebase設定
FIREBASE_CONFIG = {
    "databaseURL": os.getenv("FIREBASE_DATABASE_URL", ""),
}


class FirebaseSignaling:
    """Firebase WebRTC Signaling"""

    def __init__(self, device_id: str = "raspi"):
        self.device_id = device_id
        self.db_url = FIREBASE_CONFIG["databaseURL"]
        self.running = False
        self.listener_thread: Optional[threading.Thread] = None
        self.current_session_id: Optional[str] = None

        # コールバック
        self.on_incoming_call: Optional[Callable[[str, Dict], None]] = None
        self.on_offer_received: Optional[Callable[[str, Dict], None]] = None
        self.on_answer_received: Optional[Callable[[str, Dict], None]] = None
        self.on_ice_candidate: Optional[Callable[[str, Dict], None]] = None
        self.on_call_ended: Optional[Callable[[str], None]] = None

    def create_call(self, callee: str = "phone") -> Optional[str]:
        """発信セッション作成（ラズパイから発信）"""
        session_id = f"{self.device_id}_{int(time.time() * 1000)}"
        call_data = {
            "caller": self.device_id,
            "callee": callee,
            "status": "calling",
            "created_at": int(time.time() * 1000),
        }

        url = f"{self.db_url}/videocall/{session_id}.json"
        response = requests.put(url, json=call_data, timeout=10)

        if response.status_code == 200:
            self.current_session_id = session_id
            logger.info(f"ビデオ通話発信: {session_id}")
            return session_id
        return None

    def send_offer(self, session_id: str, offer: Dict) -> bool:
        """Offer SDPを送信"""
        url = f"{self.db_url}/videocall/{session_id}/offer.json"
        response = requests.put(url, json=offer, timeout=10)
        return response.status_code == 200

    def send_answer(self, session_id: str, answer: Dict) -> bool:
        """Answer SDPを送信"""
        url = f"{self.db_url}/videocall/{session_id}/answer.json"
        response = requests.put(url, json=answer, timeout=10)

        if response.status_code == 200:
            # ステータスを接続中に更新
            self._update_status(session_id, "connected")
            return True
        return False

    def send_ice_candidate(self, session_id: str, candidate: Dict, is_caller: bool = False) -> bool:
        """ICE候補を送信"""
        path = "caller_candidates" if is_caller else "callee_candidates"
        url = f"{self.db_url}/videocall/{session_id}/{path}.json"
        response = requests.post(url, json=candidate, timeout=10)
        return response.status_code == 200

    def get_session(self, session_id: str) -> Optional[Dict]:
        """セッション情報を取得"""
        url = f"{self.db_url}/videocall/{session_id}.json"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json()
        return None

    def end_call(self, session_id: str = None) -> bool:
        """通話終了"""
        target_id = session_id or self.current_session_id
        if not target_id:
            return False

        success = self._update_status(target_id, "ended")
        if success:
            logger.info(f"ビデオ通話終了: {target_id}")
            self.current_session_id = None
        return success

    def accept_call(self, session_id: str) -> bool:
        """着信応答"""
        self.current_session_id = session_id
        return self._update_status(session_id, "answering")

    def reject_call(self, session_id: str) -> bool:
        """着信拒否"""
        return self._update_status(session_id, "rejected")

    def _update_status(self, session_id: str, status: str) -> bool:
        """ステータス更新"""
        url = f"{self.db_url}/videocall/{session_id}/status.json"
        response = requests.put(url, json=status, timeout=10)
        return response.status_code == 200

    def cleanup_old_sessions(self, max_age_ms: int = 3600000) -> int:
        """古いビデオ通話セッションをクリーンアップ

        自分のデバイスに関連する古いセッション、または終了済みセッションのみ削除。
        他のデバイス間のアクティブなセッションは保持。

        Args:
            max_age_ms: セッションの最大有効期間（ミリ秒）。デフォルト1時間。
        """
        try:
            url = f"{self.db_url}/videocall.json"
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                return 0

            data = response.json()
            if not data:
                return 0

            current_time = int(time.time() * 1000)
            deleted_count = 0

            for session_id, session in data.items():
                if not isinstance(session, dict):
                    continue

                caller = session.get("caller", "")
                callee = session.get("callee", "")
                status = session.get("status", "")
                created_at = session.get("created_at", 0)

                # 削除条件:
                # 1. 終了済みセッション
                # 2. 自分のデバイスが関与 かつ 古いセッション（1時間以上前）
                should_delete = False

                if status in ("ended", "rejected"):
                    should_delete = True
                elif caller == self.device_id or callee == self.device_id:
                    if current_time - created_at > max_age_ms:
                        should_delete = True

                if should_delete:
                    delete_url = f"{self.db_url}/videocall/{session_id}.json"
                    del_response = requests.delete(delete_url, timeout=10)
                    if del_response.status_code == 200:
                        deleted_count += 1
                        logger.debug(f"セッション削除: {session_id}")

            if deleted_count > 0:
                logger.info(f"古いビデオ通話セッションを {deleted_count} 件削除しました")
            return deleted_count

        except Exception as e:
            logger.warning(f"セッションクリーンアップエラー: {e}")
            return 0

    def start_listening(self) -> None:
        """シグナリングイベント監視開始"""
        # 起動時に古いセッションをクリーンアップ
        self.cleanup_old_sessions()

        self.running = True
        self._last_seen_sessions = set()
        self._last_caller_candidates = {}
        self._last_callee_candidates = {}
        self._last_answer = {}
        self._last_offer = {}

        # 動的ポーリング間隔
        self._idle_interval = 2.0      # 待機中: 2秒
        self._active_interval = 0.1    # 通話中/接続確立中: 0.1秒（ICE候補交換を高速化）

        def poll_loop():
            while self.running:
                try:
                    self._poll_signals()
                except Exception as e:
                    logger.debug(f"シグナリングポーリングエラー: {e}")

                # 通話中は高速ポーリング、待機中は低速ポーリング
                if self.current_session_id:
                    time.sleep(self._active_interval)
                else:
                    time.sleep(self._idle_interval)

        self.listener_thread = threading.Thread(target=poll_loop, daemon=True)
        self.listener_thread.start()
        logger.info(f"シグナリング監視開始（待機: {self._idle_interval}s, 通話中: {self._active_interval}s）")

    def stop_listening(self) -> None:
        """監視停止"""
        self.running = False
        if self.listener_thread:
            self.listener_thread.join(timeout=5)
        logger.info("シグナリング監視停止")

    def _poll_signals(self) -> None:
        """シグナリングをポーリング"""
        url = f"{self.db_url}/videocall.json"
        response = requests.get(url, timeout=10)

        if response.status_code != 200:
            return

        data = response.json()
        if not data:
            return

        for session_id, session in data.items():
            if not isinstance(session, dict):
                continue

            status = session.get("status", "")
            caller = session.get("caller", "")
            callee = session.get("callee", "")

            # 着信検出（自分がcalleeで、calling状態）
            if callee == self.device_id and status == "calling":
                if session_id not in self._last_seen_sessions:
                    self._last_seen_sessions.add(session_id)
                    offer = session.get("offer")
                    if offer and self.on_incoming_call:
                        self.on_incoming_call(session_id, session)

            # Answer受信（自分がcallerで、answerがある）
            if caller == self.device_id:
                answer = session.get("answer")
                if answer and session_id not in self._last_answer:
                    self._last_answer[session_id] = True
                    if self.on_answer_received:
                        self.on_answer_received(session_id, answer)

            # Offer受信（自分がcalleeで、offerがある、answeringかconnected）
            if callee == self.device_id and status in ("answering", "connected"):
                offer = session.get("offer")
                if offer and session_id not in self._last_offer:
                    self._last_offer[session_id] = True
                    if self.on_offer_received:
                        self.on_offer_received(session_id, offer)

            # ICE候補受信
            self._check_ice_candidates(session_id, session, caller, callee)

            # 通話終了検出
            if status == "ended" and session_id == self.current_session_id:
                if self.on_call_ended:
                    self.on_call_ended(session_id)
                self.current_session_id = None

    def _check_ice_candidates(self, session_id: str, session: Dict,
                               caller: str, callee: str) -> None:
        """ICE候補をチェック"""
        # 相手のICE候補を取得
        if caller == self.device_id:
            # 自分がcallerなら、callee_candidatesを監視
            candidates = session.get("callee_candidates", {})
            last_seen = self._last_callee_candidates.get(session_id, set())
        else:
            # 自分がcalleeなら、caller_candidatesを監視
            candidates = session.get("caller_candidates", {})
            last_seen = self._last_caller_candidates.get(session_id, set())

        if not isinstance(candidates, dict):
            return

        for cand_id, candidate in candidates.items():
            if cand_id not in last_seen:
                last_seen.add(cand_id)
                if self.on_ice_candidate:
                    self.on_ice_candidate(session_id, candidate)

        if caller == self.device_id:
            self._last_callee_candidates[session_id] = last_seen
        else:
            self._last_caller_candidates[session_id] = last_seen
