"""
State Manager — Proactive Mode Controller
===========================================

Finite state machine governing D.A.E.M.O.N.'s three operational modes:

    ACTIVE   — Microphone live, user is present, agents idle.
    AWAY     — Microphone asleep, background agents run tasks and
               append results to ``briefing_queue.json``.
    BRIEFING — Triggered on return. A synthesis agent reads the queue,
               drafts a narrative via Ollama, and speaks it aloud.

Valid transitions::

    ACTIVE  ──▶  AWAY      (user locks screen / manual trigger)
    AWAY    ──▶  BRIEFING  (user unlocks / hotkey)
    AWAY    ──▶  ACTIVE    (skip briefing, go straight to active)
    BRIEFING ──▶ ACTIVE    (briefing finished speaking)

Thread Safety
-------------
All public methods acquire ``_lock`` so the state can be changed from
FastAPI endpoints, hotkey callbacks, the voice pipeline, or background
agent threads without races.

Briefing Queue
--------------
Located at ``logs/briefing_queue.json`` (configurable via env var
``BRIEFING_QUEUE_PATH``).  Each entry is a timestamped dict written by
an agent's ``process_background_task()`` method::

    {
        "agent":     "cipher",
        "timestamp": "2026-06-26T23:00:00+00:00",
        "category":  "system_profiler",
        "summary":   "GCC compilation succeeded for matrix.c ...",
        "data":      { ... }
    }
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core_logic.config import Config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Queue file path — configurable via env, defaults to logs/briefing_queue.json
# ---------------------------------------------------------------------------
_QUEUE_PATH = Path(
    os.getenv(
        "BRIEFING_QUEUE_PATH",
        str(Config.LOGS_DIR / "briefing_queue.json"),
    )
)


class DaemonState(str, Enum):
    """Operational modes of the D.A.E.M.O.N. system."""

    ACTIVE = "active"
    AWAY = "away"
    BRIEFING = "briefing"


# ---------------------------------------------------------------------------
# Allowed transitions (source → set of valid targets)
# ---------------------------------------------------------------------------
_VALID_TRANSITIONS: Dict[DaemonState, set] = {
    DaemonState.ACTIVE: {DaemonState.AWAY},
    DaemonState.AWAY: {DaemonState.BRIEFING, DaemonState.ACTIVE},
    DaemonState.BRIEFING: {DaemonState.ACTIVE},
}


class StateManager:
    """Thread-safe state machine for D.A.E.M.O.N.'s operational modes.

    Parameters
    ----------
    initial_state
        Starting state (default ``ACTIVE``).
    queue_path
        Filesystem path for the briefing queue JSON file.
    on_enter_active
        Callback fired when transitioning **into** ACTIVE.
    on_enter_away
        Callback fired when transitioning **into** AWAY.
    on_enter_briefing
        Callback fired when transitioning **into** BRIEFING.
    on_state_change
        Generic callback fired on *every* transition, receiving
        ``(old_state, new_state)`` as arguments.

    Usage::

        sm = StateManager()
        sm.on_enter_away = lambda: mic.sleep()
        sm.on_enter_active = lambda: mic.wake()
        sm.go_away()           # ACTIVE → AWAY
        sm.start_briefing()    # AWAY → BRIEFING
        sm.go_active()         # BRIEFING → ACTIVE
    """

    def __init__(
        self,
        initial_state: DaemonState = DaemonState.ACTIVE,
        queue_path: Optional[Path] = None,
        on_enter_active: Optional[Callable[[], None]] = None,
        on_enter_away: Optional[Callable[[], None]] = None,
        on_enter_briefing: Optional[Callable[[], None]] = None,
        on_state_change: Optional[Callable[[DaemonState, DaemonState], None]] = None,
    ) -> None:
        self._state = initial_state
        self._lock = threading.Lock()
        self._queue_path = queue_path or _QUEUE_PATH

        # Ensure the queue parent directory exists
        self._queue_path.parent.mkdir(parents=True, exist_ok=True)

        # Lifecycle callbacks — callers wire these up after construction
        self.on_enter_active = on_enter_active
        self.on_enter_away = on_enter_away
        self.on_enter_briefing = on_enter_briefing
        self.on_state_change = on_state_change

        logger.info(
            f"🔄 StateManager initialised — state={self._state.value}, "
            f"queue={self._queue_path}"
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> DaemonState:
        """Current operational state (read-only outside the lock)."""
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state == DaemonState.ACTIVE

    @property
    def is_away(self) -> bool:
        return self._state == DaemonState.AWAY

    @property
    def is_briefing(self) -> bool:
        return self._state == DaemonState.BRIEFING

    # ------------------------------------------------------------------
    # Transition helpers (convenience wrappers over _transition)
    # ------------------------------------------------------------------

    def go_active(self) -> bool:
        """Transition to ACTIVE mode (from AWAY or BRIEFING).

        Returns True if the transition succeeded.
        """
        return self._transition(DaemonState.ACTIVE)

    def go_away(self) -> bool:
        """Transition to AWAY mode (from ACTIVE).

        Returns True if the transition succeeded.
        """
        return self._transition(DaemonState.AWAY)

    def start_briefing(self) -> bool:
        """Transition to BRIEFING mode (from AWAY).

        Returns True if the transition succeeded.
        """
        return self._transition(DaemonState.BRIEFING)

    # ------------------------------------------------------------------
    # Core transition engine
    # ------------------------------------------------------------------

    def _transition(self, target: DaemonState) -> bool:
        """Attempt a state transition.  Thread-safe.

        Returns True if the transition was valid and executed, False if
        the transition was rejected (invalid source→target pair or
        already in the target state).
        """
        with self._lock:
            old = self._state

            if old == target:
                logger.debug(f"StateManager: already in {target.value}, no-op")
                return True

            if target not in _VALID_TRANSITIONS.get(old, set()):
                logger.warning(
                    f"StateManager: invalid transition {old.value} → {target.value}"
                )
                return False

            self._state = target
            logger.info(f"🔄 State: {old.value} → {target.value}")

        # Fire callbacks OUTSIDE the lock to avoid deadlocks if the
        # callback itself queries state or triggers another transition.
        self._fire_callbacks(old, target)
        return True

    def _fire_callbacks(self, old: DaemonState, new: DaemonState) -> None:
        """Invoke registered lifecycle callbacks."""
        # Generic change callback
        if self.on_state_change:
            try:
                self.on_state_change(old, new)
            except Exception as exc:
                logger.error(f"on_state_change callback error: {exc}")

        # Per-state enter callbacks
        cb_map = {
            DaemonState.ACTIVE: self.on_enter_active,
            DaemonState.AWAY: self.on_enter_away,
            DaemonState.BRIEFING: self.on_enter_briefing,
        }
        cb = cb_map.get(new)
        if cb:
            try:
                cb()
            except Exception as exc:
                logger.error(f"on_enter_{new.value} callback error: {exc}")

    # ------------------------------------------------------------------
    # Briefing queue — file-backed JSON array
    # ------------------------------------------------------------------

    def append_to_briefing_queue(self, entry: Dict[str, Any]) -> None:
        """Append a single entry to the briefing queue.

        Called by agents during AWAY mode and by the ``/api/agent_logs``
        FastAPI endpoint when the C-daemon sends data.

        Each entry should contain at minimum:
            - ``agent``:    agent name (str)
            - ``summary``:  human-readable summary (str)
            - ``data``:     raw payload (dict)

        A ``timestamp`` field is added automatically if missing.
        """
        if "timestamp" not in entry:
            entry["timestamp"] = datetime.now(timezone.utc).isoformat()

        with self._lock:
            queue = self._read_queue_unsafe()
            queue.append(entry)
            self._write_queue_unsafe(queue)

        logger.debug(
            f"📥 Briefing queue: +1 entry from '{entry.get('agent', '?')}' "
            f"(total {len(queue)})"
        )

    def read_briefing_queue(self) -> List[Dict[str, Any]]:
        """Read the full briefing queue without clearing it.

        Returns a list of entry dicts (possibly empty).
        """
        with self._lock:
            return self._read_queue_unsafe()

    def consume_briefing_queue(self) -> List[Dict[str, Any]]:
        """Read and clear the briefing queue atomically.

        Intended to be called at the start of BRIEFING mode so the
        synthesis agent can process all accumulated entries and then
        the queue starts fresh for the next AWAY cycle.
        """
        with self._lock:
            queue = self._read_queue_unsafe()
            self._write_queue_unsafe([])
        logger.info(f"📤 Briefing queue consumed — {len(queue)} entries")
        return queue

    def get_queue_size(self) -> int:
        """Return the number of pending entries in the queue."""
        with self._lock:
            return len(self._read_queue_unsafe())

    # -- internal queue I/O (must be called while holding _lock) -----------

    def _read_queue_unsafe(self) -> List[Dict[str, Any]]:
        """Read the queue file. Returns [] if missing or corrupt."""
        try:
            if self._queue_path.exists():
                text = self._queue_path.read_text(encoding="utf-8").strip()
                if text:
                    data = json.loads(text)
                    if isinstance(data, list):
                        return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"Briefing queue read error: {exc}")
        return []

    def _write_queue_unsafe(self, queue: List[Dict[str, Any]]) -> None:
        """Write the queue file atomically (write-then-rename)."""
        tmp = self._queue_path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(queue, indent=2, default=str),
                encoding="utf-8",
            )
            tmp.replace(self._queue_path)
        except OSError as exc:
            logger.error(f"Briefing queue write error: {exc}")

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Return serialisable status dict for the HUD / API."""
        return {
            "state": self._state.value,
            "queue_size": self.get_queue_size(),
            "queue_path": str(self._queue_path),
        }

    def __repr__(self) -> str:
        return f"<StateManager state={self._state.value}>"
