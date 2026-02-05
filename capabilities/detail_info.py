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
                                "text": f"""この画像から読み取れる情報（文字、ロゴ、署名、特徴など）を手がかりに、写っているものを特定し、詳しい情報を提供してください。

元の質問: {context.prompt}
簡潔な回答: {context.brief_analysis}

■ 対象別に提供すべき情報:
- 本 → タイトル、作者、あらすじ、ジャンル、出版年、評価など
- アート作品 → 作品名、作者、制作年、作品の意味や背景、美術史的な位置づけなど
- 場所・店舗 → 正式名称、どんな場所か、歴史、名物、特徴など
- 商品 → 商品名、ブランド、用途、特徴、価格帯など
- 人物 → 名前、職業、経歴、有名な業績など
- 食べ物 → 料理名、由来、材料、発祥地など

■ 重要:
- 画像内の文字やロゴを読み取って特定の手がかりにする
- 見た目の説明ではなく「調べたら分かる情報」を提供する
- 特定できない場合は一般的な情報でも可

以下の形式でMarkdown形式で回答してください：

## 概要
（これが何か1文で特定）

## 詳細情報
（上記の対象別情報を参考に、知りたくなるような情報を詳しく）

## 補足
（1-2文で豆知識）

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
