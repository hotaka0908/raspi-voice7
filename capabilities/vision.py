"""
視覚系Capability

「見る」に関する能力:
- カメラで撮影して分析
- 画像からテキストを読み取る
- 目の前のものを理解する
"""

import base64
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from openai import OpenAI

from .base import Capability, CapabilityCategory, CapabilityResult
from config import Config


# カメラ排他制御用ロック
camera_lock = threading.Lock()

# OpenAIクライアント（Vision API用）
_openai_client = None


# 直前の撮影コンテキスト
@dataclass
class LastCaptureContext:
    """直前の撮影コンテキスト"""
    image_data: bytes        # 画像バイナリ
    image_base64: str        # Base64エンコード済み
    brief_analysis: str      # 簡潔な分析結果
    prompt: str              # 元の質問
    timestamp: float         # 撮影時刻（5分でタイムアウト）


_last_capture: Optional[LastCaptureContext] = None
CAPTURE_TIMEOUT_SEC = 300  # 5分


def get_last_capture() -> Optional[LastCaptureContext]:
    """5分以内の撮影コンテキストを取得"""
    global _last_capture
    if _last_capture is None:
        return None
    if time.time() - _last_capture.timestamp > CAPTURE_TIMEOUT_SEC:
        _last_capture = None
        return None
    return _last_capture


def clear_last_capture() -> None:
    """撮影コンテキストをクリア"""
    global _last_capture
    _last_capture = None


def _save_capture_context(image_data: bytes, image_base64: str,
                          brief_analysis: str, prompt: str) -> None:
    """撮影コンテキストを保存"""
    global _last_capture
    _last_capture = LastCaptureContext(
        image_data=image_data,
        image_base64=image_base64,
        brief_analysis=brief_analysis,
        prompt=prompt,
        timestamp=time.time()
    )


def get_openai_client():
    """OpenAIクライアントを取得（遅延初期化）"""
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=Config.get_api_key())
    return _openai_client


class CameraCapture(Capability):
    """カメラで撮影して分析"""

    @property
    def name(self) -> str:
        return "camera_capture"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.VISION

    @property
    def description(self) -> str:
        return """目の前を見て理解する。以下の場面で使う：

■ 視覚が必要な質問すべて：
- 「この答えは？」→ 問題を見て答えを計算
- 「これ何？」「何が見える？」→ 目の前を見て説明
- 「読んで」→ 文字を見て読み上げ
- 「どう思う？」「どっちがいい？」→ 見て意見を述べる
- 「色は？」「サイズは？」「いくつある？」→ 見て確認
- 「おいしそう？」「かわいい？」→ 見て感想
- 「翻訳して」→ 外国語を見て翻訳

■ 指示語がある場合：
- 「これ」「あれ」「それ」「この」→ 見る必要がある

promptで質問を渡すと、見たものについてその質問に答える"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "見たものに対する質問（例: 'この問題の答えを教えて', '何が見えますか'）"
                }
            }
        }

    def execute(self, prompt: str = "何が見えますか？") -> CapabilityResult:
        """カメラで撮影して分析"""
        with camera_lock:
            try:
                image_path = "/tmp/ai_necklace_capture.jpg"
                result = subprocess.run(
                    ["rpicam-still", "-o", image_path, "-t", "500",
                     "--width", "1280", "--height", "960"],
                    capture_output=True, text=True, timeout=10
                )

                if result.returncode != 0:
                    return CapabilityResult.fail("今は見えません")

                with open(image_path, "rb") as f:
                    image_data = f.read()

            except subprocess.TimeoutExpired:
                return CapabilityResult.fail("今は見えません")
            except FileNotFoundError:
                return CapabilityResult.fail("今は見えません")
            except Exception:
                return CapabilityResult.fail("今は見えません")

        # 画像分析（ロック外で実行）
        try:
            client = get_openai_client()

            # 画像をBase64エンコード
            image_base64 = base64.b64encode(image_data).decode('utf-8')

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt + "\n\n日本語で回答してください。音声で読み上げるため、1-2文程度の簡潔な説明をお願いします。"
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_base64}",
                                    "detail": "auto"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=300
            )

            brief_analysis = response.choices[0].message.content

            # 撮影コンテキストを保存（後で「詳しく」と聞かれた時用）
            _save_capture_context(image_data, image_base64, brief_analysis, prompt)

            return CapabilityResult.ok(brief_analysis)

        except Exception:
            return CapabilityResult.fail("今は見えません")


def capture_image_raw() -> Optional[bytes]:
    """画像データを取得（他のCapabilityから使用）"""
    with camera_lock:
        try:
            image_path = "/tmp/ai_necklace_capture.jpg"
            result = subprocess.run(
                ["rpicam-still", "-o", image_path, "-t", "500",
                 "--width", "1280", "--height", "960"],
                capture_output=True, timeout=10
            )

            if result.returncode != 0:
                return None

            with open(image_path, "rb") as f:
                return f.read()

        except Exception:
            return None


# エクスポート
VISION_CAPABILITIES = [
    CameraCapture(),
]

# コンテキスト関連のエクスポート
__all__ = [
    'VISION_CAPABILITIES',
    'capture_image_raw',
    'get_last_capture',
    'clear_last_capture',
    'LastCaptureContext',
    'get_openai_client',
]
