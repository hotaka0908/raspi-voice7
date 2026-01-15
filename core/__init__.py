"""
Core層

音声処理、OpenAI Realtime API、Firebase連携
"""

from .audio import (
    AudioHandler,
    find_audio_device,
    resample_audio,
    generate_startup_sound,
    generate_notification_sound
)
from .openai_realtime_client import OpenAIRealtimeClient
from .firebase_voice import FirebaseVoiceMessenger

__all__ = [
    'AudioHandler',
    'find_audio_device',
    'resample_audio',
    'generate_startup_sound',
    'generate_notification_sound',
    'OpenAIRealtimeClient',
    'FirebaseVoiceMessenger',
]
