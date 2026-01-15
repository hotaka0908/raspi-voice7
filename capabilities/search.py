"""
検索系Capability

「調べる」に関する能力:
- Web検索でリアルタイム情報を取得
- 天気、ニュース、為替など
"""

from typing import Any, Dict
from google import genai
from google.genai import types

from .base import Capability, CapabilityCategory, CapabilityResult
from config import Config


# Geminiクライアント（検索用）
_gemini_client = None


def get_gemini_client():
    """Geminiクライアントを取得"""
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=Config.get_api_key())
    return _gemini_client


class WebSearch(Capability):
    """Web検索で情報を取得"""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.VISION  # 「見る」の拡張として

    @property
    def description(self) -> str:
        return """リアルタイム情報を調べる。以下の場面で使う：

■ 最新情報が必要な質問：
- 「今日の天気は？」「明日の天気」
- 「今のニュースは？」「最新ニュース」
- 「今日の日経平均は？」「ドル円いくら？」
- 「○○の試合結果は？」
- 「○○って何？」（最新の話題・人物）

■ ローカル情報：
- 「近くのラーメン屋は？」
- 「○○駅から○○駅への行き方」
- 「今やってる映画は？」

queryで検索キーワードを渡す"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "検索クエリ（例: '東京 天気', '最新ニュース', 'ドル円 為替'）"
                }
            },
            "required": ["query"]
        }

    def execute(self, query: str) -> CapabilityResult:
        """Web検索を実行"""
        try:
            client = get_gemini_client()

            # Google Search Groundingを使用
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=f"""以下の質問に対して、最新の情報を調べて簡潔に答えてください。
音声で読み上げるため、1-3文程度で要点だけお願いします。

質問: {query}""",
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                )
            )

            if response and response.text:
                return CapabilityResult.ok(response.text.strip())
            else:
                return CapabilityResult.fail("情報が見つかりませんでした")

        except Exception as e:
            # エラーの種類に応じて対応
            error_str = str(e)
            if "google_search" in error_str.lower() or "grounding" in error_str.lower():
                # Grounding未対応の場合、通常のGeminiで回答
                return self._fallback_search(query)
            return CapabilityResult.fail("今は調べられません")

    def _fallback_search(self, query: str) -> CapabilityResult:
        """Grounding未対応時のフォールバック"""
        try:
            client = get_gemini_client()
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=f"""以下の質問に答えてください。
最新情報が必要な場合は「最新情報は確認できませんが」と前置きしてください。
音声で読み上げるため、1-3文程度で簡潔に。

質問: {query}"""
            )

            if response and response.text:
                return CapabilityResult.ok(response.text.strip())
            return CapabilityResult.fail("情報が見つかりませんでした")

        except Exception:
            return CapabilityResult.fail("今は調べられません")


# エクスポート
SEARCH_CAPABILITIES = [
    WebSearch(),
]
