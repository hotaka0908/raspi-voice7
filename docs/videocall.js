/**
 * WebRTC Video Call Module
 *
 * スマホ側のWebRTCビデオ通話ロジック
 * Firebase経由でシグナリング、ラズパイと双方向通話
 */

// ICEサーバー設定
const ICE_SERVERS = [
    { urls: 'stun:stun.l.google.com:19302' },
    { urls: 'stun:stun1.l.google.com:19302' },
    // TURN（必要に応じて追加）
    // { urls: 'turn:your-turn-server:3478', username: 'user', credential: 'pass' }
];

class VideoCallManager {
    constructor(db) {
        this.db = db;
        this.pc = null;
        this.localStream = null;
        this.remoteStream = null;
        this.currentSessionId = null;
        this.deviceId = 'phone';
        this.calleeId = 'raspi';

        // コールバック
        this.onIncomingCall = null;
        this.onCallConnected = null;
        this.onCallEnded = null;
        this.onRemoteStream = null;
        this.onLocalStream = null;
        this.onConnectionStateChange = null;

        // リスナー解除関数
        this._unsubscribeSignaling = null;
    }

    /**
     * シグナリング監視開始
     */
    startListening() {
        const { ref, onValue } = window.firebaseFunctions;
        const videocallRef = ref(this.db, 'videocall');

        // 処理済みセッションを追跡
        this._notifiedIncomingCalls = new Set();

        this._unsubscribeSignaling = onValue(videocallRef, (snapshot) => {
            const data = snapshot.val();
            if (!data) return;

            for (const [sessionId, session] of Object.entries(data)) {
                if (!session || typeof session !== 'object') continue;

                const { caller, callee, status, offer } = session;

                // 着信検出（自分がcalleeで、calling状態、offerあり、未通知）
                if (callee === this.deviceId && status === 'calling' && offer) {
                    if (!this._notifiedIncomingCalls.has(sessionId)) {
                        this._notifiedIncomingCalls.add(sessionId);
                        if (this.onIncomingCall) {
                            this.onIncomingCall(sessionId, session);
                        }
                    }
                }

                // 相手がAnswerを送った場合（自分がcaller）
                if (caller === this.deviceId && session.answer && !this._answerProcessed) {
                    this._answerProcessed = true;
                    this._handleAnswer(sessionId, session.answer);
                }

                // ICE候補受信
                this._processIceCandidates(sessionId, session, caller, callee);

                // 通話終了検出
                if (status === 'ended' && sessionId === this.currentSessionId) {
                    this._handleCallEnded(sessionId);
                }
            }
        });
    }

    /**
     * シグナリング監視停止
     */
    stopListening() {
        if (this._unsubscribeSignaling) {
            this._unsubscribeSignaling();
            this._unsubscribeSignaling = null;
        }
    }

    /**
     * 発信（スマホ→ラズパイ）
     */
    async startCall() {
        try {
            // ローカルメディア取得
            this.localStream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: 'user', width: 640, height: 480 },
                audio: true
            });

            if (this.onLocalStream) {
                this.onLocalStream(this.localStream);
            }

            // PeerConnection作成
            await this._createPeerConnection();

            // トラック追加
            this.localStream.getTracks().forEach(track => {
                this.pc.addTrack(track, this.localStream);
            });

            // セッション作成
            const sessionId = `${this.deviceId}_${Date.now()}`;
            this.currentSessionId = sessionId;
            this._answerProcessed = false;

            const { ref, set } = window.firebaseFunctions;
            await set(ref(this.db, `videocall/${sessionId}`), {
                caller: this.deviceId,
                callee: this.calleeId,
                status: 'calling',
                created_at: Date.now()
            });

            // Offer作成・送信
            const offer = await this.pc.createOffer();
            await this.pc.setLocalDescription(offer);

            await set(ref(this.db, `videocall/${sessionId}/offer`), {
                type: offer.type,
                sdp: offer.sdp
            });

            console.log('発信開始:', sessionId);
            return sessionId;

        } catch (error) {
            console.error('発信エラー:', error);
            await this.endCall();
            throw error;
        }
    }

    /**
     * 着信応答
     */
    async acceptCall(sessionId, session) {
        try {
            this.currentSessionId = sessionId;

            // ローカルメディア取得
            this.localStream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: 'user', width: 640, height: 480 },
                audio: true
            });

            if (this.onLocalStream) {
                this.onLocalStream(this.localStream);
            }

            // PeerConnection作成
            await this._createPeerConnection();

            // トラック追加
            this.localStream.getTracks().forEach(track => {
                this.pc.addTrack(track, this.localStream);
            });

            // Offer設定
            const offer = session.offer;
            await this.pc.setRemoteDescription(new RTCSessionDescription({
                type: offer.type,
                sdp: offer.sdp
            }));

            // Answer作成・送信
            const answer = await this.pc.createAnswer();
            await this.pc.setLocalDescription(answer);

            const { ref, set, update } = window.firebaseFunctions;
            await set(ref(this.db, `videocall/${sessionId}/answer`), {
                type: answer.type,
                sdp: answer.sdp
            });

            await update(ref(this.db, `videocall/${sessionId}`), {
                status: 'connected'
            });

            console.log('着信応答:', sessionId);

        } catch (error) {
            console.error('着信応答エラー:', error);
            await this.endCall();
            throw error;
        }
    }

    /**
     * 着信拒否
     */
    async rejectCall(sessionId) {
        const { ref, update } = window.firebaseFunctions;
        await update(ref(this.db, `videocall/${sessionId}`), {
            status: 'rejected'
        });
    }

    /**
     * 通話終了
     */
    async endCall() {
        // ローカルストリーム停止
        if (this.localStream) {
            this.localStream.getTracks().forEach(track => track.stop());
            this.localStream = null;
        }

        // PeerConnection終了
        if (this.pc) {
            this.pc.close();
            this.pc = null;
        }

        // Firebaseのステータス更新
        if (this.currentSessionId) {
            try {
                const { ref, update } = window.firebaseFunctions;
                await update(ref(this.db, `videocall/${this.currentSessionId}`), {
                    status: 'ended'
                });
            } catch (e) {
                console.log('終了ステータス更新エラー:', e);
            }
            this.currentSessionId = null;
        }

        this.remoteStream = null;

        if (this.onCallEnded) {
            this.onCallEnded();
        }
    }

    /**
     * ビデオON/OFF
     */
    toggleVideo() {
        if (!this.localStream) return false;
        const videoTrack = this.localStream.getVideoTracks()[0];
        if (videoTrack) {
            videoTrack.enabled = !videoTrack.enabled;
            return videoTrack.enabled;
        }
        return false;
    }

    /**
     * オーディオON/OFF
     */
    toggleAudio() {
        if (!this.localStream) return false;
        const audioTrack = this.localStream.getAudioTracks()[0];
        if (audioTrack) {
            audioTrack.enabled = !audioTrack.enabled;
            return audioTrack.enabled;
        }
        return false;
    }

    /**
     * PeerConnection作成
     */
    async _createPeerConnection() {
        this.pc = new RTCPeerConnection({
            iceServers: ICE_SERVERS
        });

        // 接続状態変更
        this.pc.onconnectionstatechange = () => {
            const state = this.pc.connectionState;
            console.log('接続状態:', state);

            if (this.onConnectionStateChange) {
                this.onConnectionStateChange(state);
            }

            if (state === 'connected' && this.onCallConnected) {
                this.onCallConnected();
            }

            if (state === 'failed' || state === 'disconnected') {
                this.endCall();
            }
        };

        // ICE候補
        this.pc.onicecandidate = async (event) => {
            if (event.candidate && this.currentSessionId) {
                const { ref, push } = window.firebaseFunctions;
                const path = this.deviceId === 'phone' ? 'caller_candidates' : 'callee_candidates';
                await push(ref(this.db, `videocall/${this.currentSessionId}/${path}`), {
                    candidate: event.candidate.candidate,
                    sdpMid: event.candidate.sdpMid,
                    sdpMLineIndex: event.candidate.sdpMLineIndex
                });
            }
        };

        // リモートトラック受信
        this.pc.ontrack = (event) => {
            console.log('リモートトラック受信:', event.track.kind);
            if (!this.remoteStream) {
                this.remoteStream = new MediaStream();
            }
            this.remoteStream.addTrack(event.track);

            if (this.onRemoteStream) {
                this.onRemoteStream(this.remoteStream);
            }
        };
    }

    /**
     * Answer処理
     */
    async _handleAnswer(sessionId, answer) {
        if (!this.pc) return;

        try {
            await this.pc.setRemoteDescription(new RTCSessionDescription({
                type: answer.type,
                sdp: answer.sdp
            }));
            console.log('Answer受信・設定完了');
        } catch (error) {
            console.error('Answer処理エラー:', error);
        }
    }

    /**
     * ICE候補処理
     */
    async _processIceCandidates(sessionId, session, caller, callee) {
        if (!this.pc || sessionId !== this.currentSessionId) return;

        // 相手のICE候補を取得
        const candidatesPath = caller === this.deviceId ? 'callee_candidates' : 'caller_candidates';
        const candidates = session[candidatesPath];

        if (!candidates || typeof candidates !== 'object') return;

        if (!this._processedCandidates) {
            this._processedCandidates = new Set();
        }

        for (const [id, candidate] of Object.entries(candidates)) {
            if (this._processedCandidates.has(id)) continue;
            this._processedCandidates.add(id);

            try {
                await this.pc.addIceCandidate(new RTCIceCandidate({
                    candidate: candidate.candidate,
                    sdpMid: candidate.sdpMid,
                    sdpMLineIndex: candidate.sdpMLineIndex
                }));
                console.log('ICE候補追加');
            } catch (error) {
                console.log('ICE候補追加エラー:', error);
            }
        }
    }

    /**
     * 通話終了処理
     */
    _handleCallEnded(sessionId) {
        console.log('通話終了検出:', sessionId);
        this.endCall();
    }
}

// エクスポート
window.VideoCallManager = VideoCallManager;
