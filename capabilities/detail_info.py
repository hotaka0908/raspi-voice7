"""
詳細情報送信Capability

直前に見たものの詳細情報をスマホに送る
"""

from typing import Any, Dict

from .base import Capability, CapabilityCategory, CapabilityResult
from .vision import get_last_capture, clear_last_capture, get_openai_client
from .communication import get_firebase_messenger


class SendDetailInfo(Capability):
    """直前に見たものの詳細情報をスマホに送る"""

    @property
    def name(self) -> str:
        return "send_detail_info"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.COMMUNICATION

    @property
    def description(self) -> str:
        return """直前に見たものの詳細情報をスマホに送る。以下の場面で使う：

■ 使う場面:
- 「もっと詳しく」「詳細教えて」「詳しい情報が欲しい」
- 「もっと知りたい」「詳しく説明して」
- 直前に camera_capture で何かを見た後に、より詳しい情報を求められたとき

■ 条件:
- 直前（5分以内）に camera_capture で何かを見ている必要がある
- 詳細情報はスマホのWebアプリに表示される"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {}
        }

    def execute(self, **kwargs) -> CapabilityResult:
        """詳細情報をスマホに送信"""
        # 1. 直前の撮影コンテキストを取得
        context = get_last_capture()
        if context is None:
            return CapabilityResult.fail(
                "最近見たものがありません。まず「これ何？」と聞いてください"
            )

        # 2. Firebaseメッセンジャーを取得
        firebase = get_firebase_messenger()
        if firebase is None:
            return CapabilityResult.fail("今はスマホに送れません")

        # 3. GPT-4o Vision で詳細分析
        try:
            client = get_openai_client()
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"""この画像について詳細な情報を提供してください。

元の質問: {context.prompt}
簡潔な回答: {context.brief_analysis}

以下の形式でMarkdown形式で詳細情報を作成してください：

## 概要
（1文で分かりやすく説明）

## 詳細情報
（用途、機能、歴史、関連情報など）

## 補足
（1-2文で短く分かりやすく）

日本語で回答してください。"""
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{context.image_base64}",
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=1500
            )

            detail_analysis = response.choices[0].message.content

        except Exception:
            return CapabilityResult.fail("詳細情報を取得できませんでした")

        # 4. Firebaseに送信
        try:
            success = firebase.send_detail_info(
                image_data=context.image_data,
                brief_analysis=context.brief_analysis,
                detail_analysis=detail_analysis,
                original_prompt=context.prompt
            )

            if success:
                # コンテキストをクリア（同じ画像で何度も送らないように）
                clear_last_capture()
                return CapabilityResult.ok("詳しい情報をスマホに送りました")
            else:
                return CapabilityResult.fail("今はスマホに送れません")

        except Exception:
            return CapabilityResult.fail("今はスマホに送れません")


# エクスポート
DETAIL_INFO_CAPABILITIES = [
    SendDetailInfo(),
]
