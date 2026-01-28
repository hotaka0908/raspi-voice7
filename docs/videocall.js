/**
 * WebRTC Video Call Module
 *
 * スマホ側のWebRTCビデオ通話ロジック
 * Firebase経由でシグナリング、ラズパイと通話
 *
 * 構成:
 * - ラズパイ: 映像送信のみ + 音声送受信
 * - スマホ: 映像受信のみ + 音声送受信
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

                // デバッグ: 自分のセッションの状態を出力
                if (sessionId === this.currentSessionId) {
                    console.log(`セッション状態: id=${sessionId}, caller=${caller}, status=${status}, answer=${!!session.answer}, _answerProcessed=${this._answerProcessed}`)
                }

                // 着信検出（自分がcalleeで、calling状態、offerあり、未通知）
                if (callee === this.deviceId && status === 'calling' && offer) {
                    if (!this._notifiedIncomingCalls.has(sessionId)) {
                        this._notifiedIncomingCalls.add(sessionId);
                        if (this.onIncomingCall) {
                            this.onIncomingCall(sessionId, session);
                        }
                    }
                }

                // 相手がAnswerを送った場合（自分がcaller、かつ現在のセッション）
                if (sessionId === this.currentSessionId && caller === this.deviceId && session.answer && !this._answerProcessed) {
                    this._answerProcessed = true;
                    console.log('Answer検出、処理開始:', sessionId);
                    // 非同期でanswerを処理し、完了後にICE候補も処理
                    this._handleAnswer(sessionId, session.answer, session, caller, callee);
                }

                // ICE候補受信（remoteDescriptionが設定された後のみ処理）
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
     * スマホは音声のみ送信、映像はラズパイから受信
     */
    async startCall() {
        try {
            // セッション作成（先にFirebaseに書き込み）
            const sessionId = `${this.deviceId}_${Date.now()}`;
            this.currentSessionId = sessionId;
            this._answerProcessed = false;

            const { ref, set } = window.firebaseFunctions;

            console.log('セッション作成開始:', sessionId);
            await set(ref(this.db, `videocall/${sessionId}`), {
                caller: this.deviceId,
                callee: this.calleeId,
                status: 'initializing',
                created_at: Date.now()
            });
            console.log('セッション作成完了');

            // ローカルメディア取得（音声のみ）
            console.log('マイク取得開始');
            this.localStream = await navigator.mediaDevices.getUserMedia({
                video: false,
                audio: true
            });
            console.log('マイク取得完了');

            // PeerConnection作成
            await this._createPeerConnection();

            // トラック追加
            this.localStream.getTracks().forEach(track => {
                this.pc.addTrack(track, this.localStream);
            });

            // ステータスを'calling'に更新
            const { update } = window.firebaseFunctions;
            await update(ref(this.db, `videocall/${sessionId}`), {
                status: 'calling'
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
     * スマホは音声のみ送信、映像はラズパイから受信
     */
    async acceptCall(sessionId, session) {
        try {
            this.currentSessionId = sessionId;

            // ローカルメディア取得（音声のみ）
            this.localStream = await navigator.mediaDevices.getUserMedia({
                video: false,
                audio: true
            });

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
     * ビデオON/OFF（スマホは映像送信しないため、ローカルビデオなし）
     */
    toggleVideo() {
        // スマホからは映像を送信しないため、この機能は無効
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

        // 映像受信のみのトランシーバーを追加（ラズパイからの映像を受信）
        this.pc.addTransceiver('video', { direction: 'recvonly' });

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

        // ICE接続状態変更（重要：checking→connected の遷移を監視）
        this.pc.oniceconnectionstatechange = () => {
            const state = this.pc.iceConnectionState;
            console.log('ICE接続状態:', state);

            if (state === 'failed') {
                console.error('ICE接続失敗！NATトラバーサルに問題がある可能性があります。');
            }
        };

        // ICE収集状態変更
        this.pc.onicegatheringstatechange = () => {
            console.log('ICE収集状態:', this.pc.iceGatheringState);
        };

        // ICE候補エラー
        this.pc.onicecandidateerror = (event) => {
            console.error('ICE候補エラー:', event.errorCode, event.errorText, event.url);
        };

        // ICE候補
        this.pc.onicecandidate = async (event) => {
            if (event.candidate && this.currentSessionId) {
                console.log('ローカルICE候補生成:', event.candidate.candidate?.substring(0, 60));
                const { ref, push } = window.firebaseFunctions;
                const path = this.deviceId === 'phone' ? 'caller_candidates' : 'callee_candidates';
                await push(ref(this.db, `videocall/${this.currentSessionId}/${path}`), {
                    candidate: event.candidate.candidate,
                    sdpMid: event.candidate.sdpMid,
                    sdpMLineIndex: event.candidate.sdpMLineIndex
                });
                console.log('ICE候補をFirebaseに送信:', path);
            } else if (!event.candidate) {
                console.log('ICE収集完了（end-of-candidates）');
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
    async _handleAnswer(sessionId, answer, session, caller, callee) {
        if (!this.pc) return;

        try {
            await this.pc.setRemoteDescription(new RTCSessionDescription({
                type: answer.type,
                sdp: answer.sdp
            }));
            console.log('Answer受信・設定完了');
            console.log(`ICE接続状態: ${this.pc.iceConnectionState}`);

            // setRemoteDescription完了後、ICE候補を処理
            if (session) {
                console.log('Answer処理完了後、ICE候補を処理');
                await this._processIceCandidates(sessionId, session, caller, callee);
            }
        } catch (error) {
            console.error('Answer処理エラー:', error);
        }
    }

    /**
     * ICE候補処理
     */
    async _processIceCandidates(sessionId, session, caller, callee) {
        if (!this.pc || sessionId !== this.currentSessionId) return;

        // remoteDescriptionが設定されていない場合はスキップ
        // （answerがsetRemoteDescriptionで処理された後に再度呼ばれる）
        if (!this.pc.remoteDescription) {
            console.log('remoteDescriptionが未設定、ICE候補処理をスキップ');
            return;
        }

        // 相手のICE候補を取得
        const candidatesPath = caller === this.deviceId ? 'callee_candidates' : 'caller_candidates';
        const candidates = session[candidatesPath];

        console.log(`ICE候補チェック: path=${candidatesPath}, candidates=`, candidates);
        console.log(`ICE接続状態: ${this.pc.iceConnectionState}, 接続状態: ${this.pc.connectionState}`);

        if (!candidates || typeof candidates !== 'object') return;

        if (!this._processedCandidates) {
            this._processedCandidates = new Set();
        }

        for (const [id, candidate] of Object.entries(candidates)) {
            if (this._processedCandidates.has(id)) continue;
            this._processedCandidates.add(id);

            console.log('ラズパイICE候補受信:', candidate);

            try {
                await this.pc.addIceCandidate(new RTCIceCandidate({
                    candidate: candidate.candidate,
                    sdpMid: candidate.sdpMid,
                    sdpMLineIndex: candidate.sdpMLineIndex
                }));
                console.log('ラズパイICE候補追加成功:', candidate.candidate?.substring(0, 50));
            } catch (error) {
                console.log('ICE候補追加エラー:', error, candidate);
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
