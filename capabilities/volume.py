"""
音量調整Capability

AI応答の音声音量を調整する
"""

from typing import Any, Dict

from .base import Capability, CapabilityCategory, CapabilityResult
from core.audio import get_voice_volume, adjust_voice_volume


class VolumeUp(Capability):
    """音量を上げる"""

    @property
    def name(self) -> str:
        return "volume_up"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.SYSTEM

    @property
    def description(self) -> str:
        return """音量を上げる。以下の場面で使う：
- 「音量上げて」「声大きくして」「もっと大きく」「聞こえにくい」"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self) -> CapabilityResult:
        current = get_voice_volume()
        if current >= 2.0:
            return CapabilityResult.ok("もう最大音量です")

        adjust_voice_volume(0.25)
        return CapabilityResult.ok("音量を上げました")


class VolumeDown(Capability):
    """音量を下げる"""

    @property
    def name(self) -> str:
        return "volume_down"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.SYSTEM

    @property
    def description(self) -> str:
        return """音量を下げる。以下の場面で使う：
- 「音量下げて」「声小さくして」「もっと小さく」「うるさい」"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self) -> CapabilityResult:
        current = get_voice_volume()
        if current <= 0.5:
            return CapabilityResult.ok("もう最小音量です")

        adjust_voice_volume(-0.25)
        return CapabilityResult.ok("音量を下げました")


# エクスポート
VOLUME_CAPABILITIES = [
    VolumeUp(),
    VolumeDown(),
]
