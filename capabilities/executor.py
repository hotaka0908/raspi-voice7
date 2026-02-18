"""
Capability実行エンジン

ツール呼び出しを受けて適切なCapabilityを実行
"""

from typing import Any, Dict, List, Optional

from .base import Capability, CapabilityResult
from .vision import VISION_CAPABILITIES
from .communication import COMMUNICATION_CAPABILITIES
from .schedule import SCHEDULE_CAPABILITIES
from .memory import MEMORY_CAPABILITIES
from .search import SEARCH_CAPABILITIES
from .calendar import CALENDAR_CAPABILITIES
from .videocall import VIDEOCALL_CAPABILITIES
from .detail_info import DETAIL_INFO_CAPABILITIES
from .music import MUSIC_CAPABILITIES
from .openclaw import OPENCLAW_CAPABILITIES


class CapabilityExecutor:
    """Capability実行エンジン"""

    def __init__(self):
        self._capabilities: Dict[str, Capability] = {}
        self._register_all()

    def _register_all(self) -> None:
        """すべてのCapabilityを登録"""
        all_capabilities = (
            VISION_CAPABILITIES +
            COMMUNICATION_CAPABILITIES +
            SCHEDULE_CAPABILITIES +
            MEMORY_CAPABILITIES +
            SEARCH_CAPABILITIES +
            CALENDAR_CAPABILITIES +
            VIDEOCALL_CAPABILITIES +
            DETAIL_INFO_CAPABILITIES +
            MUSIC_CAPABILITIES +
            OPENCLAW_CAPABILITIES
        )
        for cap in all_capabilities:
            self._capabilities[cap.name] = cap

    def execute(self, name: str, arguments: Dict[str, Any]) -> CapabilityResult:
        """Capabilityを実行"""
        cap = self._capabilities.get(name)
        if not cap:
            return CapabilityResult.fail("できませんでした")

        try:
            return cap.execute(**arguments)
        except Exception as e:
            # 技術的なエラーは隠蔽
            return CapabilityResult.fail("今はできません")

    def get_capability(self, name: str) -> Optional[Capability]:
        """Capabilityを取得"""
        return self._capabilities.get(name)

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        """OpenAI Realtime API用のツール定義を取得"""
        tools = []

        for cap in self._capabilities.values():
            tool_def = cap.get_tool_definition()
            params = tool_def.get("parameters", {})
            properties = params.get("properties", {})
            required = params.get("required", [])

            # OpenAI形式のパラメータスキーマ
            schema_props = {}
            for prop_name, prop in properties.items():
                schema_props[prop_name] = {
                    "type": prop.get("type", "string"),
                    "description": prop.get("description", "")
                }

            tool = {
                "type": "function",
                "name": tool_def["name"],
                "description": tool_def["description"],
                "parameters": {
                    "type": "object",
                    "properties": schema_props,
                    "required": required if required else []
                }
            }
            tools.append(tool)

        return tools


# シングルトンインスタンス
_executor: Optional[CapabilityExecutor] = None


def get_executor() -> CapabilityExecutor:
    """実行エンジンを取得（シングルトン）"""
    global _executor
    if _executor is None:
        _executor = CapabilityExecutor()
    return _executor
