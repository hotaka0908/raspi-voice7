"""
raspi-voice7 設定 (OpenAI Realtime API版)

環境変数から読み込み、デフォルト値を提供
"""

import os
from typing import Optional
from dotenv import load_dotenv

# 環境変数の読み込み
env_path = os.path.expanduser("~/.ai-necklace/.env")
load_dotenv(env_path)


class Config:
    """アプリケーション設定"""

    # OpenAI Realtime API設定
    MODEL = "gpt-4o-realtime-preview"
    VOICE = "alloy"  # OpenAI voices: alloy, echo, fable, onyx, nova, shimmer

    # オーディオ設定 (OpenAI Realtime API仕様)
    SEND_SAMPLE_RATE = 24000      # OpenAI入力: 24kHz
    RECEIVE_SAMPLE_RATE = 24000   # OpenAI出力: 24kHz
    INPUT_SAMPLE_RATE = 48000     # マイク入力: 48kHz (PyAudioはUSBマイクで48kHzのみ対応)
    OUTPUT_SAMPLE_RATE = 48000    # スピーカー出力: 48kHz (デバイスが48kHzのみ対応)
    CHANNELS = 1                  # モノラル
    CHUNK_SIZE = 512              # 512が最も効率的（読み取り遅延が少ない）

    # デバイス設定（None = 自動検出）
    INPUT_DEVICE_INDEX = None
    OUTPUT_DEVICE_INDEX = None

    # GPIO設定
    BUTTON_PIN = 5
    USE_BUTTON = True

    # パス設定
    BASE_DIR = os.path.expanduser("~/.ai-necklace")
    GMAIL_CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")
    GMAIL_TOKEN_PATH = os.path.join(BASE_DIR, "token.json")
    ALARM_FILE_PATH = os.path.join(BASE_DIR, "alarms.json")
    LOG_DIR = os.path.join(BASE_DIR, "logs")
    LIFELOG_DIR = os.path.expanduser("~/lifelog")

    # ライフログ設定
    LIFELOG_INTERVAL = 60  # 1分（秒）

    # プロアクティブリマインダー設定
    REMINDER_CHECK_INTERVAL = 60        # チェック間隔（秒）
    REMINDER_ADVANCE_MINUTES = 10       # 出発何分前にリマインド
    REMINDER_MOVEMENT_CHECK_DELAY = 30  # 移動検知の再撮影待ち（秒）
    REMINDER_LOOKAHEAD_HOURS = 3        # 監視する予定の範囲（時間）
    REMINDER_MAX_RETRIES = 3            # 再リマインド最大回数

    # セッション設定
    SESSION_RESET_TIMEOUT = 10  # 秒（応答後このくらい経過でリセット）
    VOICE_MESSAGE_TIMEOUT = 60  # 秒（音声メッセージモードのタイムアウト）

    # 再接続設定
    MAX_RECONNECT_ATTEMPTS = 5
    RECONNECT_DELAY_BASE = 2  # 秒（指数バックオフの基底）

    # WebRTC設定
    ICE_SERVERS = [
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"},
        # TURN (外出先から使用する場合に必要)
        # {"urls": "turn:your-turn-server:3478", "username": "user", "credential": "pass"}
    ]
    VIDEO_WIDTH = 640
    VIDEO_HEIGHT = 480
    VIDEO_FPS = 15
    VIDEOCALL_RING_TIMEOUT = 30  # 着信タイムアウト（秒）

    # APIキー
    @classmethod
    def get_api_key(cls) -> str:
        """OpenAI APIキーを取得"""
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise ValueError("OPENAI_API_KEY が設定されていません")
        return key

    @classmethod
    def get_google_api_key(cls) -> str:
        """Google APIキーを取得（Vision/Search用）"""
        key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not key:
            raise ValueError("GOOGLE_API_KEY または GEMINI_API_KEY が設定されていません")
        return key

    @classmethod
    def get_tavily_api_key(cls) -> str:
        """Tavily APIキーを取得（Web検索用）"""
        key = os.getenv("TAVILY_API_KEY")
        if not key:
            raise ValueError("TAVILY_API_KEY が設定されていません")
        return key

    @classmethod
    def get_google_maps_api_key(cls) -> str:
        """Google Maps APIキーを取得（Directions API用）"""
        key = os.getenv("GOOGLE_MAPS_API_KEY")
        if not key:
            raise ValueError("GOOGLE_MAPS_API_KEY が設定されていません")
        return key

    @classmethod
    def get_openclaw_url(cls) -> str:
        """OpenClaw Gateway URLを取得"""
        url = os.getenv("OPENCLAW_GATEWAY_URL", "ws://127.0.0.1:18789")
        return url

    @classmethod
    def get_openclaw_token(cls) -> Optional[str]:
        """OpenClaw Gatewayトークンを取得（任意）"""
        return os.getenv("OPENCLAW_GATEWAY_TOKEN")

    # Gmail APIスコープ
    GMAIL_SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.send',
        'https://www.googleapis.com/auth/gmail.modify'
    ]


# 設定ディレクトリの作成
os.makedirs(Config.BASE_DIR, exist_ok=True)
os.makedirs(Config.LOG_DIR, exist_ok=True)
