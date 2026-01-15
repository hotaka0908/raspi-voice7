"""
raspi-voice6 設定

環境変数から読み込み、デフォルト値を提供
"""

import os
from dotenv import load_dotenv

# 環境変数の読み込み
env_path = os.path.expanduser("~/.ai-necklace/.env")
load_dotenv(env_path)


class Config:
    """アプリケーション設定"""

    # Gemini Live API設定
    MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
    VOICE = "Kore"  # Gemini voice: Puck, Charon, Kore, Fenrir, Aoede

    # オーディオ設定 (Gemini Live API仕様)
    SEND_SAMPLE_RATE = 16000      # Gemini入力: 16kHz
    RECEIVE_SAMPLE_RATE = 24000   # Gemini出力: 24kHz
    INPUT_SAMPLE_RATE = 48000     # マイク入力: 48kHz
    OUTPUT_SAMPLE_RATE = 48000    # スピーカー出力: 48kHz
    CHANNELS = 1                  # モノラル
    CHUNK_SIZE = 1024

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

    # セッション設定
    SESSION_RESET_TIMEOUT = 30  # 秒（応答後このくらい経過でリセット）
    VOICE_MESSAGE_TIMEOUT = 60  # 秒（音声メッセージモードのタイムアウト）

    # 再接続設定
    MAX_RECONNECT_ATTEMPTS = 5
    RECONNECT_DELAY_BASE = 2  # 秒（指数バックオフの基底）

    # APIキー
    @classmethod
    def get_api_key(cls) -> str:
        """APIキーを取得"""
        key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not key:
            raise ValueError("GOOGLE_API_KEY または GEMINI_API_KEY が設定されていません")
        return key

    # Gmail APIスコープ
    GMAIL_SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.send',
        'https://www.googleapis.com/auth/gmail.modify'
    ]


# 設定ディレクトリの作成
os.makedirs(Config.BASE_DIR, exist_ok=True)
os.makedirs(Config.LOG_DIR, exist_ok=True)
