"""
raspi-voice7 設定 (OpenAI Realtime API版)

環境変数から読み込み、デフォルト値を提供
"""

import os
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
    INPUT_SAMPLE_RATE = 44100     # マイク入力: 44.1kHz (raspi-voice3と同じ)
    OUTPUT_SAMPLE_RATE = 44100    # スピーカー出力: 44.1kHz
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

    # Gmail APIスコープ
    GMAIL_SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.send',
        'https://www.googleapis.com/auth/gmail.modify'
    ]


# 設定ディレクトリの作成
os.makedirs(Config.BASE_DIR, exist_ok=True)
os.makedirs(Config.LOG_DIR, exist_ok=True)
