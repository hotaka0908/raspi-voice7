"""
詳細情報送信Capability

直前に見たものの詳細情報をスマホに送る
"""

import threading
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
                                "text": f"""この画像に写っているものについて詳細な情報を提供してください。

元の質問: {context.prompt}
簡潔な回答: {context.brief_analysis}

重要な指示:
- 可能な限り具体的に特定する（名前、種類、ブランドなど分かれば明記）
- 簡潔な回答と同じ内容を繰り返さず、新しい情報を提供する

以下の形式でMarkdown形式で詳細情報を作成してください：

## 概要
（これが何か具体的に1文で）

## 詳細情報
（この対象について知っておくと役立つ情報を詳しく）

## 補足
（1-2文で追加の豆知識や注意点など）

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

        # 4. Firebaseに非同期で送信（送信完了を待たずに返答）
        def send_async():
            firebase.send_detail_info(
                image_data=context.image_data,
                brief_analysis=context.brief_analysis,
                detail_analysis=detail_analysis,
                original_prompt=context.prompt
            )

        threading.Thread(target=send_async, daemon=True).start()

        # コンテキストをクリア（同じ画像で何度も送らないように）
        clear_last_capture()
        return CapabilityResult.ok("詳しい情報をスマホに送りました")


# エクスポート
DETAIL_INFO_CAPABILITIES = [
    SendDetailInfo(),
]
