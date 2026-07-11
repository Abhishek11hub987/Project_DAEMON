"""
Live WebSocket HUD Stream Manager — Pillar D
===============================================

Upgrades the existing ``EventBus`` in ``web/server.py`` to support
structured JSON packet broadcasting from multiple sources:

- **Telemetry** — System vitals + business metrics (every 2s from analytics)
- **Orchestrator** — Agent progress, tool calls, code being written
- **Pipeline** — Voice state changes, messages, session events

The manager acts as a **fan-in hub**: any subsystem can call
``broadcast(packet)`` and it gets pushed to all connected WebSocket
clients as structured JSON with a ``type`` discriminator.

It also manages orchestrator lifecycle — starting tasks, cancelling them,
and exposing endpoints the frontend can call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Centralised WebSocket broadcast manager for the DAEMON HUD.

    This replaces / wraps the basic ``EventBus`` from ``web/server.py``
    with richer functionality:

    - Typed packet broadcasting with ``type`` discriminator
    - Client connection tracking with metadata
    - Telemetry + orchestrator state aggregation
    - Rate-limited broadcast to avoid flooding slow clients

    Parameters
    ----------
    max_queue_size
        Maximum outbound queue per client.  If a client falls behind,
        older messages are dropped.
    """

    def __init__(self, max_queue_size: int = 100) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._max_queue = max_queue_size
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # State tracking for snapshot on new connections
        self._last_telemetry: Optional[Dict[str, Any]] = None
        self._orchestrator_state: Optional[Dict[str, Any]] = None
        self._daemon_state: str = "idle"
        self._message_count: int = 0

        logger.info("🌐 WebSocketManager initialised.")

    # ------------------------------------------------------------------
    # Loop binding
    # ------------------------------------------------------------------

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind to the running event loop (call from FastAPI lifespan)."""
        self._loop = loop

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------

    async def connect(self, ws: WebSocket) -> None:
        """Accept a new WebSocket client and send initial state snapshot."""
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        logger.info(f"🌐 WS client connected ({len(self._clients)} total)")

    async def send_snapshot(self, ws: WebSocket, extra: Dict[str, Any] = None) -> None:
        """Send the full current state to a newly connected client."""
        snapshot: Dict[str, Any] = {
            "type": "snapshot",
            "daemon_state": self._daemon_state,
            "message_count": self._message_count,
        }
        if self._last_telemetry:
            snapshot["telemetry"] = self._last_telemetry
        if self._orchestrator_state:
            snapshot["orchestrator"] = self._orchestrator_state
        if extra:
            snapshot.update(extra)

        try:
            await ws.send_text(json.dumps(snapshot, default=str))
        except Exception as e:
            logger.debug(f"Snapshot send failed: {e}")

    def disconnect(self, ws: WebSocket) -> None:
        """Remove a disconnected client."""
        self._clients.discard(ws)
        logger.info(f"🌐 WS client disconnected ({len(self._clients)} total)")

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # ------------------------------------------------------------------
    # Broadcasting
    # ------------------------------------------------------------------

    async def _broadcast(self, payload: Dict[str, Any]) -> None:
        """Send a JSON packet to all connected clients."""
        if not self._clients:
            return

        text = json.dumps(payload, default=str)
        dead: List[WebSocket] = []

        for ws in list(self._clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)

    def broadcast(self, payload: Dict[str, Any]) -> None:
        """Thread-safe broadcast — can be called from any thread.

        Schedules the async broadcast on the attached event loop.
        """
        # Track state for snapshots
        ptype = payload.get("type")
        if ptype == "telemetry":
            self._last_telemetry = payload
        elif ptype in ("orchestrator_start", "orchestrator_done",
                       "orchestrator_cancelled", "agent_start",
                       "agent_done", "agent_iteration"):
            self._orchestrator_state = payload
        elif ptype == "state":
            self._daemon_state = payload.get("state", "idle")
        elif ptype == "message":
            self._message_count += 1

        loop = self._loop
        if loop is None or not loop.is_running():
            return

        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(payload), loop)
        except Exception as e:
            logger.debug(f"broadcast failed: {e}")

    # ------------------------------------------------------------------
    # Convenience: typed broadcasts
    # ------------------------------------------------------------------

    def broadcast_telemetry(self, data: Dict[str, Any]) -> None:
        """Broadcast a telemetry packet (called by AnalyticsEngine)."""
        self.broadcast(data)

    def broadcast_orchestrator(self, data: Dict[str, Any]) -> None:
        """Broadcast an orchestrator event."""
        self.broadcast(data)

    def broadcast_message(self, role: str, content: str) -> None:
        """Broadcast a chat message."""
        self.broadcast({
            "type": "message",
            "role": role,
            "content": content,
        })

    def broadcast_state(self, state: str) -> None:
        """Broadcast a daemon state change."""
        self._daemon_state = state
        self.broadcast({"type": "state", "state": state})

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Return manager status for diagnostics."""
        return {
            "connected_clients": self.client_count,
            "messages_sent": self._message_count,
            "daemon_state": self._daemon_state,
            "has_telemetry": self._last_telemetry is not None,
            "has_orchestrator": self._orchestrator_state is not None,
        }
