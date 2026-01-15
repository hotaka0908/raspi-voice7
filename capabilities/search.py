"""
検索系Capability

「調べる」に関する能力:
- Web検索でリアルタイム情報を取得
- 天気、ニュース、為替など
"""

from typing import Any, Dict
from tavily import TavilyClient

from .base import Capability, CapabilityCategory, CapabilityResult
from config import Config


# Tavilyクライアント（検索用）
_tavily_client = None


def get_tavily_client():
    """Tavilyクライアントを取得"""
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=Config.get_tavily_api_key())
    return _tavily_client


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
            client = get_tavily_client()

            # Tavily検索を実行
            response = client.search(
                query=query,
                search_depth="basic",
                max_results=5,
                include_answer=True,  # AI生成の要約を含める
            )

            # AI生成の回答があればそれを使用
            if response.get("answer"):
                return CapabilityResult.ok(response["answer"])

            # なければ検索結果から要約を作成
            results = response.get("results", [])
            if results:
                # 上位3件の内容を要約
                summaries = []
                for r in results[:3]:
                    title = r.get("title", "")
                    content = r.get("content", "")
                    if content:
                        # 長すぎる場合は切り詰め
                        if len(content) > 200:
                            content = content[:200] + "..."
                        summaries.append(f"{title}: {content}")

                if summaries:
                    return CapabilityResult.ok("\n".join(summaries))

            return CapabilityResult.fail("情報が見つかりませんでした")

        except Exception as e:
            error_str = str(e)
            if "api_key" in error_str.lower():
                return CapabilityResult.fail("検索機能が設定されていません")
            return CapabilityResult.fail("今は調べられません")


# エクスポート
SEARCH_CAPABILITIES = [
    WebSearch(),
]
