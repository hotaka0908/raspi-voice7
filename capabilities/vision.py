"""
視覚系Capability

「見る」に関する能力:
- カメラで撮影して分析
- 画像からテキストを読み取る
- 目の前のものを理解する
"""

import subprocess
import threading
from typing import Any, Dict, Optional
from google import genai
from google.genai import types

from .base import Capability, CapabilityCategory, CapabilityResult
from config import Config


# カメラ排他制御用ロック
camera_lock = threading.Lock()

# Geminiクライアント（Vision API用）
_gemini_client = None


def get_gemini_client():
    """Geminiクライアントを取得（遅延初期化）"""
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=Config.get_api_key())
    return _gemini_client


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
            client = get_gemini_client()
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=[
                    types.Part.from_text(
                        text=prompt + "\n\n日本語で回答してください。音声で読み上げるため、1-2文程度の簡潔な説明をお願いします。"
                    ),
                    types.Part.from_bytes(data=image_data, mime_type="image/jpeg")
                ]
            )
            return CapabilityResult.ok(response.text)

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
