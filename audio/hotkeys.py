"""
Global hotkeys for D.A.E.M.O.N.

Provides:
  - Push-to-talk (default: Ctrl+Alt+Space) — skip the wake word and start
    listening for a command immediately. Works while D.A.E.M.O.N. is sitting
    in the wake-word loop OR while it's speaking (acts like barge-in).
  - Mute / silence (default: Ctrl+Alt+M) — stop the current TTS utterance
    immediately.

Hotkeys are configurable via .env:
  HOTKEY_PUSH_TO_TALK=ctrl+alt+space
  HOTKEY_MUTE=ctrl+alt+m
  ENABLE_HOTKEYS=true

This module degrades gracefully — if the `keyboard` package isn't installed
or can't grab the global hooks (e.g. running headless), it logs a warning
and returns without raising.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# A single shared event the audio pipeline can poll to know whether the user
# pressed push-to-talk and we should skip wake-word detection.
PTT_REQUESTED = threading.Event()


def _hk(name: str, default: str) -> str:
    return (os.getenv(name, default) or default).strip()


def start_hotkey_listener(daemon) -> Optional[object]:
    """Register the global hotkeys.

    Args:
        daemon: A DAEMON instance — used to access ``daemon.audio.tts`` for
                the mute hotkey and to surface user feedback.

    Returns:
        The ``keyboard`` module if hooks were installed, else ``None``.
    """
    if (os.getenv("ENABLE_HOTKEYS", "true").lower() != "true"):
        logger.info("Hotkeys disabled via ENABLE_HOTKEYS=false")
        return None

    try:
        import keyboard  # type: ignore
    except ImportError:
        logger.warning(
            "Hotkeys unavailable — `keyboard` package not installed. "
            "Run: pip install keyboard"
        )
        return None

    ptt_combo = _hk("HOTKEY_PUSH_TO_TALK", "ctrl+alt+space")
    mute_combo = _hk("HOTKEY_MUTE", "ctrl+alt+m")

    def _on_ptt() -> None:
        logger.info(f"⌨️  Push-to-talk pressed ({ptt_combo})")
        # If D.A.E.M.O.N. is mid-sentence, treat PTT as an immediate barge-in.
        try:
            tts = getattr(getattr(daemon, "audio", None), "tts", None)
            if tts and getattr(tts, "is_speaking", lambda: False)():
                tts.stop()
        except Exception as e:
            logger.debug(f"PTT could not stop TTS: {e}")
        # Tell the wake-word loop to skip listening and record directly.
        PTT_REQUESTED.set()

    def _on_mute() -> None:
        logger.info(f"⌨️  Mute pressed ({mute_combo})")
        try:
            tts = getattr(getattr(daemon, "audio", None), "tts", None)
            if tts:
                tts.stop()
        except Exception as e:
            logger.debug(f"Mute hotkey failed: {e}")

    try:
        keyboard.add_hotkey(ptt_combo, _on_ptt, suppress=False)
        keyboard.add_hotkey(mute_combo, _on_mute, suppress=False)
        logger.info(
            f"⌨️  Global hotkeys ready — PTT: {ptt_combo}  |  Mute: {mute_combo}"
        )
        print(f"   ⌨️  Hotkeys: {ptt_combo} = push-to-talk | {mute_combo} = mute")
        return keyboard
    except Exception as e:
        # On Windows, keyboard hooks normally don't need admin, but if the
        # user's environment blocks them we don't want to crash the daemon.
        logger.warning(f"Could not install hotkeys: {e}")
        return None


def consume_ptt() -> bool:
    """Return True (and clear the flag) if a PTT press is pending."""
    if PTT_REQUESTED.is_set():
        PTT_REQUESTED.clear()
        return True
    return False
