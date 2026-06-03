"""Voice provider layer. base.py defines the interface (step 1);
retell_provider.py / vapi_provider.py are implemented in step 4.
"""
from voiceagent.voice.base import (  # noqa: F401
    LeadContext,
    NormalizedCallEvent,
    OutboundCallResult,
    VoiceEventType,
    VoiceProvider,
)
