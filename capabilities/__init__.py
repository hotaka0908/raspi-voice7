"""
Capability層

ユーザーの意図を実現するための能力群
"""

from .base import Capability, CapabilityCategory, CapabilityResult
from .executor import CapabilityExecutor, get_executor

from .vision import (
    VISION_CAPABILITIES, capture_image_raw,
    get_last_capture, clear_last_capture,
    set_vision_audio_callback
)
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
    set_firebase_messenger, set_play_audio_callback,
    pause_lifelog, resume_lifelog, is_lifelog_paused
)
from .search import SEARCH_CAPABILITIES
from .calendar import CALENDAR_CAPABILITIES, init_calendar, get_calendar_service
from .videocall import (
    VIDEOCALL_CAPABILITIES,
    set_videocall_callbacks
)
from .detail_info import DETAIL_INFO_CAPABILITIES, set_detail_audio_callback
from .music import (
    MUSIC_CAPABILITIES, is_music_playing, get_current_track, stop_music_player,
    set_music_audio_callbacks, is_music_active, pause_music_for_conversation,
    resume_music_after_conversation
)
from .proactive_reminder import (
    start_reminder_thread,
    stop_reminder_thread,
    set_reminder_notify_callback
)
from .profile import (
    PROFILE_CAPABILITIES,
    generate_user_profile,
    get_profile_from_firebase
)
from .volume import VOLUME_CAPABILITIES

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
    'VIDEOCALL_CAPABILITIES',
    'DETAIL_INFO_CAPABILITIES',
    'MUSIC_CAPABILITIES',
    'is_music_playing',
    'get_current_track',
    'stop_music_player',
    'set_music_audio_callbacks',
    'is_music_active',
    'pause_music_for_conversation',
    'resume_music_after_conversation',
    'capture_image_raw',
    'get_last_capture',
    'clear_last_capture',
    'set_vision_audio_callback',
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
    'pause_lifelog',
    'resume_lifelog',
    'is_lifelog_paused',
    'SEARCH_CAPABILITIES',
    'CALENDAR_CAPABILITIES',
    'init_calendar',
    'get_calendar_service',
    'set_videocall_callbacks',
    'set_detail_audio_callback',
    'start_reminder_thread',
    'stop_reminder_thread',
    'set_reminder_notify_callback',
    'PROFILE_CAPABILITIES',
    'generate_user_profile',
    'get_profile_from_firebase',
    'VOLUME_CAPABILITIES',
]
