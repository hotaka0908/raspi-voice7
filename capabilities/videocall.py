"""
ビデオ通話 Capability

「通話する」に関する能力:
- ビデオ通話発信
- ビデオ通話終了
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Callable

from .base import Capability, CapabilityCategory, CapabilityResult

logger = logging.getLogger("conversation")

# ビデオ通話マネージャー（main.pyから設定）
_video_call_start_callback: Optional[Callable[[], bool]] = None
_video_call_end_callback: Optional[Callable[[], bool]] = None
_is_in_call_callback: Optional[Callable[[], bool]] = None


def set_videocall_callbacks(
    start_callback: Callable[[], bool],
    end_callback: Callable[[], bool],
    is_in_call_callback: Callable[[], bool]
) -> None:
    """ビデオ通話コールバックを設定"""
    global _video_call_start_callback, _video_call_end_callback, _is_in_call_callback
    _video_call_start_callback = start_callback
    _video_call_end_callback = end_callback
    _is_in_call_callback = is_in_call_callback


class VideoCallStart(Capability):
    """ビデオ通話発信"""

    @property
    def name(self) -> str:
        return "videocall_start"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.CALL

    @property
    def description(self) -> str:
        return """ビデオ通話を開始する。以下の場面で使う：
- 「ビデオ通話して」「電話して」「顔見せて」「テレビ電話」"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self) -> CapabilityResult:
        global _video_call_start_callback, _is_in_call_callback

        if _is_in_call_callback and _is_in_call_callback():
            return CapabilityResult.ok("もう通話中です")

        if not _video_call_start_callback:
            return CapabilityResult.fail("今はビデオ通話できません")

        try:
            success = _video_call_start_callback()
            if success:
                return CapabilityResult.ok("スマホに発信します")
            else:
                return CapabilityResult.fail("発信できませんでした")
        except Exception as e:
            logger.error(f"ビデオ通話発信エラー: {e}")
            return CapabilityResult.fail("発信できませんでした")


class VideoCallEnd(Capability):
    """ビデオ通話終了"""

    @property
    def name(self) -> str:
        return "videocall_end"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.CALL

    @property
    def description(self) -> str:
        return """ビデオ通話を終了する。以下の場面で使う：
- 「電話切って」「通話終了」「切断して」"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self) -> CapabilityResult:
        global _video_call_end_callback, _is_in_call_callback

        if _is_in_call_callback and not _is_in_call_callback():
            return CapabilityResult.ok("通話していません")

        if not _video_call_end_callback:
            return CapabilityResult.fail("今はできません")

        try:
            success = _video_call_end_callback()
            if success:
                return CapabilityResult.ok("通話を終了しました")
            else:
                return CapabilityResult.fail("終了できませんでした")
        except Exception as e:
            logger.error(f"ビデオ通話終了エラー: {e}")
            return CapabilityResult.fail("終了できませんでした")


# エクスポート
VIDEOCALL_CAPABILITIES = [
    VideoCallStart(),
    VideoCallEnd(),
]
