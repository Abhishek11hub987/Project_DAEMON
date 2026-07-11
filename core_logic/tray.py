"""
System tray icon for D.A.E.M.O.N. (Windows / macOS / Linux).

Shows a coloured dot reflecting the current pipeline state:

    grey   = idle           (waiting for wake word)
    green  = listening      (capturing your voice)
    yellow = thinking       (LLM / skill processing)
    blue   = speaking       (TTS playing)
    red    = error

Right-click the tray icon for a menu:
    - Mute (stop current TTS)
    - Clear conversation memory
    - Quit

Implementation notes:
    - Uses ``pystray`` + ``Pillow`` (both pure-python, cross-platform).
    - The pipeline notifies us via ``pipeline.status_callback``; we redraw the
      icon on every state change.
    - The DAEMON main loop runs on a worker thread so the tray's event loop
      can own the main thread (required by macOS / pystray.run()).
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_STATE_COLORS = {
    "idle":      (120, 120, 120),  # grey
    "listening": ( 60, 200,  90),  # green
    "thinking":  (240, 190,  40),  # yellow
    "speaking":  ( 70, 130, 230),  # blue
    "error":     (220,  60,  60),  # red
}

_STATE_LABELS = {
    "idle":      "Idle — say 'Daemon'",
    "listening": "Listening…",
    "thinking":  "Thinking…",
    "speaking":  "Speaking…",
    "error":     "Error",
}


def _make_icon_image(state: str):
    """Render a simple coloured-circle icon for the given state."""
    from PIL import Image, ImageDraw

    color = _STATE_COLORS.get(state, _STATE_COLORS["idle"])
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=color + (255,), outline=(30, 30, 30, 255), width=2)
    # Tiny inner highlight for a less-flat look.
    draw.ellipse((14, 12, 30, 26), fill=(255, 255, 255, 80))
    return img


def run_with_tray(daemon, enable_hotkeys: bool = True) -> None:
    """Start the daemon's main loop on a worker thread and run a tray icon
    on the calling (main) thread until the user picks Quit.

    Args:
        daemon: A ``DAEMON`` instance from ``core_logic.main``.
        enable_hotkeys: Install global push-to-talk / mute hotkeys too.
    """
    try:
        import pystray  # noqa: F401  (importability check)
        from pystray import Icon, Menu, MenuItem
    except ImportError as e:
        raise RuntimeError(
            "Tray mode requires `pystray` and `pillow`. "
            "Install with: pip install pystray pillow"
        ) from e

    # Install hotkeys (push-to-talk, mute) if requested.
    if enable_hotkeys:
        try:
            from audio.hotkeys import start_hotkey_listener
            start_hotkey_listener(daemon)
        except Exception as e:
            logger.warning(f"Hotkeys disabled: {e}")

    # Holds a reference to the live tray icon so the status callback can
    # update its image / tooltip in-place.
    icon_ref: dict = {"icon": None}

    def _on_status(state: str) -> None:
        icon = icon_ref.get("icon")
        if icon is None:
            return
        try:
            icon.icon = _make_icon_image(state)
            icon.title = f"D.A.E.M.O.N. — {_STATE_LABELS.get(state, state)}"
        except Exception as e:
            logger.debug(f"Tray update failed: {e}")

    if daemon.audio is not None:
        daemon.audio.status_callback = _on_status

    # Worker thread: runs the voice pipeline.
    def _run_daemon() -> None:
        try:
            daemon.start()
        except Exception as e:
            logger.error(f"Daemon worker stopped: {e}", exc_info=True)
            _on_status("error")

    worker = threading.Thread(target=_run_daemon, name="DaemonWorker", daemon=True)
    worker.start()

    # ----- menu actions ----------------------------------------------------
    def _act_mute(_icon, _item) -> None:
        try:
            tts = getattr(daemon.audio, "tts", None) if daemon.audio else None
            if tts:
                tts.stop()
        except Exception as e:
            logger.debug(f"Mute failed: {e}")

    def _act_clear_memory(_icon, _item) -> None:
        try:
            if daemon.memory:
                daemon.memory.clear()
                logger.info("🧹 Conversation memory cleared from tray.")
        except Exception as e:
            logger.debug(f"Clear memory failed: {e}")

    def _act_quit(icon, _item) -> None:
        logger.info("Tray quit requested.")
        try:
            daemon.stop()
        except Exception:
            pass
        icon.stop()

    menu = Menu(
        MenuItem("Mute", _act_mute),
        MenuItem("Clear memory", _act_clear_memory),
        Menu.SEPARATOR,
        MenuItem("Quit", _act_quit),
    )

    icon = Icon(
        "daemon",
        icon=_make_icon_image("idle"),
        title="D.A.E.M.O.N. — starting…",
        menu=menu,
    )
    icon_ref["icon"] = icon

    # Run the tray (blocking) on the main thread.
    icon.run()
