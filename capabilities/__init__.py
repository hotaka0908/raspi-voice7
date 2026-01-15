"""
Capability層

ユーザーの意図を実現するための能力群
"""

from .base import Capability, CapabilityCategory, CapabilityResult
from .executor import CapabilityExecutor, get_executor

from .vision import VISION_CAPABILITIES, capture_image_raw
from .communication import (
    COMMUNICATION_CAPABILITIES,
    init_gmail, init_firebase, get_firebase_messenger
)
from .schedule import (
    SCHEDULE_CAPABILITIES,
    load_alarms, start_alarm_thread, stop_alarm_thread,
    set_alarm_notify_callback
)
from .memory import (
    MEMORY_CAPABILITIES,
    start_lifelog_thread, stop_lifelog_thread,
    set_firebase_messenger, set_play_audio_callback
)
from .search import SEARCH_CAPABILITIES
from .calendar import CALENDAR_CAPABILITIES, init_calendar

__all__ = [
    'Capability',
    'CapabilityCategory',
    'CapabilityResult',
    'CapabilityExecutor',
    'get_executor',
    'VISION_CAPABILITIES',
    'COMMUNICATION_CAPABILITIES',
    'SCHEDULE_CAPABILITIES',
    'MEMORY_CAPABILITIES',
    'capture_image_raw',
    'init_gmail',
    'init_firebase',
    'get_firebase_messenger',
    'load_alarms',
    'start_alarm_thread',
    'stop_alarm_thread',
    'set_alarm_notify_callback',
    'start_lifelog_thread',
    'stop_lifelog_thread',
    'set_firebase_messenger',
    'set_play_audio_callback',
    'SEARCH_CAPABILITIES',
    'CALENDAR_CAPABILITIES',
    'init_calendar',
]
