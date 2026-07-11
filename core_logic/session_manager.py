"""
Conversation session manager.

A *session* is a single end-to-end conversation: it starts when the wake word
fires (or the user clicks "New" in the UI), accumulates user/assistant turns,
and ends when the user says an end-phrase, goes silent for a while, or hits
"End" in the UI.

Each session is persisted as one JSON file in ``logs/sessions/`` so the user
can see and switch back to past conversations from the web UI.

Session schema (on disk):

    {
        "id": "2026-05-26_2331_a1b2c3",
        "title": "What time is it",          # auto-derived from first user turn
        "created_at": "2026-05-26T23:31:04",
        "ended_at":   "2026-05-26T23:32:11",   # null while active
        "ended_reason": "user_ended",          # null while active
        "turns": [
            {"role": "user", "content": "...", "timestamp": "..."},
            {"role": "assistant", "content": "...", "timestamp": "..."},
            ...
        ]
    }
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core_logic.config import Config

logger = logging.getLogger(__name__)


def _slugify(text: str, max_len: int = 60) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text or "Untitled"


class SessionManager:
    """Manages the lifecycle of conversation sessions on disk."""

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = Path(storage_dir or (Path(Config.LOGS_DIR) / "sessions"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.active: Optional[Dict[str, Any]] = None  # full session dict in memory
        # Hooks for the web layer to push live updates without polling.
        self.on_change: Optional[Callable[[Dict[str, Any]], None]] = None

        logger.info(f"SessionManager ready at {self.storage_dir}")

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _new_id() -> str:
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        return f"{ts}_{secrets.token_hex(3)}"

    def _path(self, session_id: str) -> Path:
        return self.storage_dir / f"{session_id}.json"

    def _save(self, session: Dict[str, Any]) -> None:
        path = self._path(session["id"])
        try:
            path.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not save session {session['id']}: {e}")

    def _notify(self, session: Dict[str, Any]) -> None:
        cb = self.on_change
        if cb is None:
            return
        try:
            cb(session)
        except Exception as e:
            logger.debug(f"on_change callback failed: {e}")

    # ---------------------------------------------------------------- lifecycle
    def begin(self) -> Dict[str, Any]:
        """Start a new active session, returning its dict."""
        with self._lock:
            session = {
                "id": self._new_id(),
                "title": "New conversation",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "ended_at": None,
                "ended_reason": None,
                "turns": [],
            }
            self.active = session
            self._save(session)
        logger.info(f"🆕 Session begin: {session['id']}")
        self._notify(session)
        return session

    def end(self, reason: str = "user_ended") -> Optional[Dict[str, Any]]:
        """Close the currently-active session."""
        with self._lock:
            session = self.active
            if session is None:
                return None
            session["ended_at"] = datetime.now().isoformat(timespec="seconds")
            session["ended_reason"] = reason
            self._save(session)
            self.active = None
        logger.info(f"🛑 Session end: {session['id']} ({reason})")
        self._notify(session)
        return session

    def add_turn(self, role: str, content: str) -> Optional[Dict[str, Any]]:
        """Append a turn to the active session (auto-starts one if needed)."""
        if role not in ("user", "assistant"):
            return None
        content = (content or "").strip()
        if not content:
            return None
        with self._lock:
            if self.active is None:
                # Auto-create a session if a turn arrives outside of one (e.g.
                # a text-mode message before the wake word).
                self.active = {
                    "id": self._new_id(),
                    "title": "New conversation",
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "ended_at": None,
                    "ended_reason": None,
                    "turns": [],
                }
                logger.info(f"🆕 Session auto-begin: {self.active['id']}")
            session = self.active
            turn = {
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
            session["turns"].append(turn)
            # First user turn becomes the title.
            if role == "user" and session["title"] == "New conversation":
                session["title"] = _slugify(content)
            self._save(session)
        self._notify(session)
        return session

    # ---------------------------------------------------------------- query
    def list_sessions(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return a summary of recent sessions, newest first."""
        rows: List[Dict[str, Any]] = []
        for path in sorted(self.storage_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                rows.append(
                    {
                        "id": data.get("id", path.stem),
                        "title": data.get("title", "Untitled"),
                        "created_at": data.get("created_at"),
                        "ended_at": data.get("ended_at"),
                        "turn_count": len(data.get("turns", [])),
                        "active": (
                            self.active is not None
                            and self.active.get("id") == data.get("id")
                        ),
                    }
                )
            except Exception as e:
                logger.debug(f"Bad session file {path.name}: {e}")
            if len(rows) >= limit:
                break
        return rows

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        if self.active is not None and self.active.get("id") == session_id:
            return self.active
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Could not read session {session_id}: {e}")
            return None

    def delete(self, session_id: str) -> bool:
        with self._lock:
            if self.active is not None and self.active.get("id") == session_id:
                self.active = None
        path = self._path(session_id)
        if path.exists():
            try:
                path.unlink()
                return True
            except Exception as e:
                logger.warning(f"Could not delete session {session_id}: {e}")
        return False
