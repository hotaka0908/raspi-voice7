# raspi-voice7

Raspberry Pi上で動作するCapability UXベースの音声AIアシスタント。**OpenAI Realtime API版**

ユーザーの意図を理解し、適切な能力を選択・組み合わせ、世界を代行して実行する「翻訳層」として機能します。

## raspi-voice6との違い

| 項目 | raspi-voice6 | raspi-voice7 |
|------|--------------|--------------|
| 音声AI | Gemini Live API | OpenAI Realtime API |
| LLM | Gemini 2.5 Flash | GPT-4o |
| STT | Gemini内蔵 | Whisper |
| TTS | Gemini内蔵 | OpenAI内蔵 |
| Vision | Gemini | GPT-4o Vision |
| Search | Gemini | Tavily API |

## 機能

### コア機能
- **リアルタイム音声対話**: OpenAI Realtime APIを使用した自然な音声会話
- **物理ボタン操作**: GPIOボタンで会話開始/終了を制御

### Capabilities（能力）
- **Gmail**: メールの確認・送信・返信
- **Googleカレンダー**: 予定の確認・追加・管理
- **アラーム/リマインダー**: 時間指定の通知
- **Web検索**: インターネット検索（Tavily API）
- **ビジョン**: カメラで見て理解（GPT-4o Vision）
- **ライフログ**: 日常の記録
- **音声メッセージ**: スマホとの音声メッセージ送受信

### Voice Messenger
Firebase経由でスマホと連携する音声メッセージ機能。Webアプリ（`docs/`）から音声メッセージの送受信が可能。

## 必要なもの

### ハードウェア
- Raspberry Pi（4以降推奨）
- USBマイク/スピーカー
- 物理ボタン（GPIO5に接続）

### ソフトウェア
- Python 3.11+
- ffmpeg
- PortAudio

## セットアップ

### 1. 依存関係のインストール

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv ffmpeg portaudio19-dev python3-lgpio

cd raspi-voice7
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 設定

`~/.ai-necklace/.env` に以下を設定:

```
# 必須: OpenAI API（音声対話、Vision用）
OPENAI_API_KEY=your_openai_api_key

# 任意: Tavily API（Web検索用）
TAVILY_API_KEY=your_tavily_api_key
```

### 3. Gmail/カレンダー連携（オプション）

Google Cloud Consoleでプロジェクトを作成し、OAuth認証情報を取得:

```
~/.ai-necklace/credentials.json
```

### 4. Firebase連携（オプション）

Voice Messenger機能を使用する場合:

1. Firebase Consoleでプロジェクトを作成
2. Realtime DatabaseとStorageを有効化
3. サービスアカウントキーを取得して配置:
   ```
   ~/.ai-necklace/firebase-service-account.json
   ```

4. Voice Messenger Webアプリの設定:
   ```bash
   cd docs
   cp firebase-config.example.js firebase-config.js
   # firebase-config.js を編集してFirebaseプロジェクトの設定を入力
   ```

## 実行

```bash
source venv/bin/activate
python main.py
```

### systemdサービスとして実行

```bash
sudo cp ai-necklace.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ai-necklace
sudo systemctl start ai-necklace
```

## ディレクトリ構成

```
raspi-voice7/
├── main.py                    # エントリーポイント
├── config.py                  # 設定
├── core/                      # コア機能
│   ├── audio.py               # 音声入出力
│   ├── openai_realtime_client.py  # OpenAI Realtime APIクライアント
│   └── firebase_voice.py      # Firebase音声メッセージ
├── capabilities/              # 能力モジュール
│   ├── communication.py       # Gmail連携
│   ├── calendar.py            # カレンダー連携
│   ├── schedule.py            # アラーム/リマインダー
│   ├── search.py              # Web検索（Tavily）
│   ├── memory.py              # 記憶/ライフログ
│   └── vision.py              # ビジョン機能（GPT-4o）
├── prompts/                   # システムプロンプト
└── docs/                      # Voice Messenger Webアプリ
```

## 使い方

1. ボタンを押しながら話しかける
2. ボタンを離すと応答が開始
3. 応答後60秒以内にボタンを押すと音声メッセージモード（スマホに送信）

## License

MIT
