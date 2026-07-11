"""
FastAPI web server for the D.A.E.M.O.N. UI.

Responsibilities:
    - Serves the single-page UI (web/static/index.html).
    - Exposes JSON endpoints for the session sidebar (list, get, delete).
    - Streams live pipeline events (state, user/assistant messages, session
      start/end) to all connected browser tabs over a WebSocket.
    - Accepts text-mode commands from the UI input box and routes them
      through the same ``DAEMON.process_command`` used by the voice path.

The server runs on the main thread; the voice pipeline runs on a worker
thread that we start here too. Quitting the UI (or Ctrl+C) stops both.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Lazy imports for Pillar C+D — avoids circular import issues and
# keeps the server functional even if analytics/orchestrator aren't
# installed yet.
try:
    from core_logic.analytics import AnalyticsEngine
except ImportError:
    AnalyticsEngine = None  # type: ignore[misc,assignment]

try:
    from core_logic.orchestrator import Orchestrator
    from skills.workspace import WorkspaceSandbox
except ImportError:
    Orchestrator = None  # type: ignore[misc,assignment]
    WorkspaceSandbox = None  # type: ignore[misc,assignment]

STATIC_DIR = Path(__file__).parent / "static"


# --------------------------------------------------------------------- models
class TextCommand(BaseModel):
    text: str
    # If true (default), starts a new session if there isn't one active.
    auto_start_session: bool = True


class OrchestratorRequest(BaseModel):
    """Request body for /api/orchestrator/run."""
    task: str


# ----------------------------------------------------------- WebSocket bus
class EventBus:
    """Dead-simple pub/sub for browser WebSockets.

    The pipeline runs on a worker thread; we fan out events into a single
    asyncio loop using ``loop.call_soon_threadsafe`` so the FastAPI side can
    push them to all connected sockets.
    """

    def __init__(self) -> None:
        self.clients: Set[WebSocket] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        with self._lock:
            self.clients.add(ws)
        logger.info(f"WS client connected ({len(self.clients)} total)")

    def disconnect(self, ws: WebSocket) -> None:
        with self._lock:
            self.clients.discard(ws)
        logger.info(f"WS client disconnected ({len(self.clients)} total)")

    async def _broadcast(self, payload: Dict[str, Any]) -> None:
        """Send `payload` to all connected clients (silently drops dead ones)."""
        text = json.dumps(payload, default=str)
        dead: List[WebSocket] = []
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def publish_threadsafe(self, payload: Dict[str, Any]) -> None:
        """Thread-safe publish callable from any worker thread."""
        loop = self.loop
        if loop is None or not loop.is_running():
            return
        # Schedule the coroutine on the loop without blocking the worker.
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(payload), loop)
        except Exception as e:
            logger.debug(f"publish_threadsafe failed: {e}")


# --------------------------------------------------------------------- app
def create_app(daemon, *, start_voice: bool = True, enable_hotkeys: bool = True) -> FastAPI:
    """Build the FastAPI app bound to a given DAEMON instance."""
    bus = EventBus()
    voice_thread: Dict[str, Any] = {"thread": None}
    analytics_ref: Dict[str, Any] = {"engine": None}
    orchestrator_ref: Dict[str, Any] = {"instance": None}

    # Helper to speak text asynchronously from worker threads
    def _speak_async(daemon_instance, text: str) -> None:
        """Speak text on a background thread so HTTP responses aren't blocked."""
        if daemon_instance.audio is None:
            return
        def _run():
            try:
                daemon_instance.audio.speak(text)
            except Exception as e:
                logger.debug(f"TTS failed: {e}")
        threading.Thread(target=_run, daemon=True).start()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Capture the running event loop so worker-thread callbacks can publish.
        bus.attach_loop(asyncio.get_running_loop())

        # Wire pipeline + session-manager observers to the bus. We *chain*
        # the existing DAEMON handlers rather than overwriting them so the
        # SessionManager bookkeeping (set up in DAEMON.__init__) keeps working.
        if daemon.audio is not None:
            prev_status = daemon.audio.status_callback
            prev_user = daemon.audio.on_user_message
            prev_asst = daemon.audio.on_assistant_message
            prev_start = daemon.audio.on_session_start
            prev_end = daemon.audio.on_session_end

            def _wrap_status(state: str, _p=prev_status):
                if _p:
                    try: _p(state)
                    except Exception: pass
                bus.publish_threadsafe({"type": "state", "state": state})

            def _wrap_user(text: str, _p=prev_user):
                if _p:
                    try: _p(text)
                    except Exception: pass
                bus.publish_threadsafe({"type": "message", "role": "user", "content": text})

            def _wrap_asst(text: str, _p=prev_asst):
                if _p:
                    try: _p(text)
                    except Exception: pass
                bus.publish_threadsafe({"type": "message", "role": "assistant", "content": text})

            def _wrap_start(_p=prev_start):
                if _p:
                    try: _p()
                    except Exception: pass
                bus.publish_threadsafe({"type": "session_start"})

            def _wrap_end(reason: str, _p=prev_end):
                if _p:
                    try: _p(reason)
                    except Exception: pass
                bus.publish_threadsafe({"type": "session_end", "reason": reason})

            daemon.audio.status_callback = _wrap_status
            daemon.audio.on_user_message = _wrap_user
            daemon.audio.on_assistant_message = _wrap_asst
            daemon.audio.on_session_start = _wrap_start
            daemon.audio.on_session_end = _wrap_end

        if daemon.sessions is not None:
            prev_change = daemon.sessions.on_change

            def _wrap_change(session, _p=prev_change):
                if _p:
                    try: _p(session)
                    except Exception: pass
                bus.publish_threadsafe(
                    {"type": "session_update", "session_id": session.get("id")}
                )

            daemon.sessions.on_change = _wrap_change

        # Optional global hotkeys (push-to-talk, mute).
        if enable_hotkeys:
            try:
                from audio.hotkeys import start_hotkey_listener
                start_hotkey_listener(daemon)
            except Exception as e:
                logger.warning(f"Hotkeys disabled: {e}")

        # Voice pipeline on a worker thread (so the web loop owns the main).
        if start_voice and daemon.audio is not None:
            def _run_voice() -> None:
                try:
                    daemon.start()
                except Exception as e:
                    logger.error(f"Voice worker stopped: {e}", exc_info=True)
            t = threading.Thread(target=_run_voice, name="DaemonVoice", daemon=True)
            t.start()
            voice_thread["thread"] = t
            logger.info("Voice pipeline running in background.")
            # Push a greeting bubble to the UI after the server is ready.
            async def _send_voice_intro() -> None:
                import asyncio as _aio, random as _r
                from datetime import datetime as _dt
                await _aio.sleep(1.5)
                _hour = _dt.now().hour
                _tod = "Good morning." if _hour < 12 else ("Good afternoon." if _hour < 17 else "Good evening.")
                _intros = [
                    "{t}! I'm Daemon. I'm all set up and ready to go whenever you are.",
                    "{t}! Daemon here. Let me know if you need any help today.",
                    "{t}! I'm online and ready. What are we working on today?",
                    "{t}! Daemon's up and running. Just say my name if you need me.",
                    "{t}! I'm here. Let's get some work done, shall we?",
                ]
                intro = _r.choice(_intros).format(t=_tod)
                bus.publish_threadsafe({"type": "message", "role": "assistant", "content": intro})
                if daemon.sessions is not None:
                    daemon.sessions.add_turn("assistant", intro)
            asyncio.ensure_future(_send_voice_intro())
        else:
            # No voice pipeline — still speak + show the intro greeting so the
            # user knows Daemon is alive and TTS is working.
            intro = "Hi, I'm Daemon! Type below and I'll talk back. If you want to use voice, just say my name."
            _speak_async(daemon, intro)
            # Small delay so the WS snapshot goes first, then the intro bubble
            # appears as a proper assistant message.
            async def _send_intro() -> None:
                import asyncio as _aio
                await _aio.sleep(0.8)
                bus.publish_threadsafe({"type": "message", "role": "assistant", "content": intro})
                if daemon.sessions is not None:
                    daemon.sessions.add_turn("assistant", intro)
            asyncio.ensure_future(_send_intro())

        # ---- Pillar C: Background Analytics Engine ----
        if AnalyticsEngine is not None:
            try:
                analytics = AnalyticsEngine(
                    interval=2.0,
                    event_callback=bus.publish_threadsafe,
                )
                analytics.start(asyncio.get_running_loop())
                analytics_ref["engine"] = analytics
                logger.info("📡 Analytics engine started.")
            except Exception as e:
                logger.warning(f"Analytics engine failed to start: {e}")

        # ---- Pillar A: Orchestrator (lazy — created on first use) ----
        if Orchestrator is not None and WorkspaceSandbox is not None:
            try:
                from core_logic.llm_engine import LLMEngine
                sandbox = WorkspaceSandbox()
                llm = daemon.llm if daemon.llm else LLMEngine()
                orch = Orchestrator(
                    llm=llm,
                    sandbox=sandbox,
                    event_callback=bus.publish_threadsafe,
                )
                orchestrator_ref["instance"] = orch
                logger.info("🧠 Orchestrator ready.")
            except Exception as e:
                logger.warning(f"Orchestrator init failed: {e}")

        # ---- Pillar C (new): Background Worker (email + GitHub polling) ----
        if daemon.background_worker is not None:
            try:
                daemon.background_worker._emit = bus.publish_threadsafe
                daemon.background_worker.start(loop=asyncio.get_running_loop())
                logger.info("🔄 Background worker started (polling every 5 min).")
            except Exception as e:
                logger.warning(f"Background worker start failed: {e}")

        yield

        # Shutdown
        if analytics_ref["engine"]:
            try:
                analytics_ref["engine"].stop()
            except Exception:
                pass
        try:
            daemon.stop()
        except Exception:
            pass

    app = FastAPI(title="D.A.E.M.O.N. UI", lifespan=lifespan)

    # ----- static + index --------------------------------------------------
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        index_file = STATIC_DIR / "index.html"
        if not index_file.exists():
            return HTMLResponse("<h1>UI missing</h1>", status_code=500)
        return HTMLResponse(index_file.read_text(encoding="utf-8"))

    # ----- session API -----------------------------------------------------
    @app.get("/api/sessions")
    async def list_sessions() -> List[Dict[str, Any]]:
        if daemon.sessions is None:
            return []
        return daemon.sessions.list_sessions()

    @app.get("/api/sessions/active")
    async def get_active() -> Dict[str, Any]:
        active = daemon.sessions.active if daemon.sessions else None
        return {"active": active}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> Dict[str, Any]:
        if daemon.sessions is None:
            raise HTTPException(404, "Session manager unavailable")
        s = daemon.sessions.get(session_id)
        if s is None:
            raise HTTPException(404, "Session not found")
        return s

    @app.post("/api/sessions/{session_id}/end")
    async def end_session(session_id: str) -> Dict[str, Any]:
        if daemon.sessions is None:
            return {"ok": False, "reason": "no_session_manager"}
        active = daemon.sessions.active
        if active is not None and active.get("id") == session_id:
            # End the live session properly.
            if daemon.audio is not None:
                daemon.audio._end_conversation("user_ended")
            else:
                daemon.sessions.end("user_ended")
        # Always push a session_ended event so the UI refreshes.
        bus.publish_threadsafe({"type": "session_end", "session_id": session_id})
        return {"ok": True}

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str) -> Dict[str, Any]:
        if daemon.sessions is None:
            raise HTTPException(404, "Session manager unavailable")
        ok = daemon.sessions.delete(session_id)
        return {"ok": ok}

    # ----- text command (no mic needed) ------------------------------------
    @app.post("/api/text")
    async def text_command(cmd: TextCommand) -> Dict[str, Any]:
        text = (cmd.text or "").strip()
        if not text:
            raise HTTPException(400, "Empty text")
        # Treat the typed command like a voice utterance, but DON'T re-broadcast
        # the user's own message — the browser already rendered it optimistically.
        if daemon.sessions is not None and cmd.auto_start_session and daemon.sessions.active is None:
            daemon.sessions.begin()
            if daemon.memory:
                daemon.memory.clear()
        if daemon.sessions is not None:
            daemon.sessions.add_turn("user", text)

        # End-phrase shortcut, mirroring voice behaviour.
        try:
            from audio.audio_pipeline import AudioPipeline as _AP
            is_end = _AP._is_end_phrase(text)
        except Exception:
            is_end = False
        if is_end:
            farewell = "Anytime. I'll be here when you need me."
            if daemon.sessions:
                daemon.sessions.add_turn("assistant", farewell)
                daemon.sessions.end("user_ended")
            bus.publish_threadsafe({"type": "message", "role": "assistant", "content": farewell})
            bus.publish_threadsafe({"type": "session_end", "reason": "user_ended"})
            _speak_async(daemon, farewell)
            return {"response": farewell, "ended": True}

        try:
            response = await asyncio.to_thread(daemon.process_command, text)
        except Exception as e:
            logger.error(f"text_command failed: {e}")
            raise HTTPException(500, str(e))
        if daemon.sessions:
            daemon.sessions.add_turn("assistant", response)
        bus.publish_threadsafe({"type": "message", "role": "assistant", "content": response})
        # Speak the reply too — the user wanted Daemon to actually talk
        # back even when typing. Runs on a worker thread so the HTTP response
        # returns immediately.
        _speak_async(daemon, response)
        return {"response": response, "ended": False}

    # ----- mute / barge-in (UI button) ------------------------------------
    _mute_state: Dict[str, bool] = {"muted": False}

    @app.post("/api/mute")
    async def mute() -> Dict[str, Any]:
        _mute_state["muted"] = not _mute_state["muted"]
        is_muted = _mute_state["muted"]
        try:
            if daemon.audio:
                mic = getattr(daemon.audio, "microphone", None)
                if is_muted:
                    # Stop mic stream so OS indicator turns off
                    if mic and mic.is_recording:
                        mic.stop_recording()
                    # Also stop any TTS so conversation halts
                    tts = getattr(daemon.audio, "tts", None)
                    if tts:
                        tts.stop()
                    # Flag the pipeline so it won't re-open mic while muted
                    daemon.audio.is_listening = False
                    logger.info("🔇 Microphone MUTED")
                else:
                    # Unmute: pipeline will re-open mic on next cycle naturally
                    daemon.audio.is_listening = True
                    logger.info("🎙️  Microphone UNMUTED")
        except Exception as e:
            logger.debug(f"mute toggle failed: {e}")
        bus.publish_threadsafe({"type": "mute_state", "muted": is_muted})
        return {"ok": True, "muted": is_muted}

    @app.get("/api/mute")
    async def get_mute_state() -> Dict[str, Any]:
        return {"muted": _mute_state["muted"]}

    @app.get("/api/status")
    async def status() -> Dict[str, Any]:
        st = daemon.get_status() if hasattr(daemon, "get_status") else {}
        st["voice_running"] = bool(voice_thread["thread"] and voice_thread["thread"].is_alive())
        return st

    # ----- Pillar C: Telemetry API -----------------------------------------
    @app.get("/api/telemetry")
    async def get_telemetry() -> Dict[str, Any]:
        """Return current system vitals snapshot."""
        engine = analytics_ref.get("engine")
        if engine:
            return engine.get_snapshot()
        return {"error": "Analytics engine not running"}

    @app.get("/api/telemetry/history")
    async def get_telemetry_history(minutes: int = 60) -> List[Dict[str, Any]]:
        """Query historical metrics from SQLite."""
        engine = analytics_ref.get("engine")
        if engine:
            return engine.get_history(minutes)
        return []

    @app.get("/api/telemetry/summary")
    async def get_telemetry_summary() -> Dict[str, Any]:
        """Aggregate stats for the last hour."""
        engine = analytics_ref.get("engine")
        if engine:
            return engine.get_summary()
        return {}

    # ----- Pillar A: Orchestrator API --------------------------------------
    @app.post("/api/orchestrator/run")
    async def run_orchestrator(req: OrchestratorRequest) -> Dict[str, Any]:
        """Start a multi-agent task."""
        orch = orchestrator_ref.get("instance")
        if not orch:
            raise HTTPException(503, "Orchestrator not available")
        try:
            ctx = await orch.run_task(req.task)
            return ctx.to_summary()
        except Exception as e:
            logger.error(f"Orchestrator run failed: {e}", exc_info=True)
            raise HTTPException(500, str(e))

    @app.post("/api/orchestrator/cancel")
    async def cancel_orchestrator() -> Dict[str, Any]:
        """Cancel the currently running orchestrator task."""
        orch = orchestrator_ref.get("instance")
        if not orch:
            return {"ok": False, "reason": "no_orchestrator"}
        orch.cancel()
        return {"ok": True}

    @app.get("/api/orchestrator/status")
    async def orchestrator_status() -> Dict[str, Any]:
        """Get orchestrator metadata."""
        orch = orchestrator_ref.get("instance")
        if orch:
            return orch.get_status()
        return {"available": False}

    # ----- WebSocket -------------------------------------------------------
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await bus.connect(ws)
        # Send initial snapshot so the UI can render without waiting for an event.
        try:
            sessions = daemon.sessions.list_sessions() if daemon.sessions else []
            active = daemon.sessions.active if daemon.sessions else None
            snapshot: Dict[str, Any] = {
                "type": "snapshot",
                "state": daemon.audio._current_state if daemon.audio else "idle",
                "sessions": sessions,
                "active": active,
            }
            # Include latest telemetry in the snapshot
            engine = analytics_ref.get("engine")
            if engine:
                snapshot["telemetry"] = engine.get_snapshot()
            await ws.send_text(json.dumps(snapshot, default=str))
        except Exception as e:
            logger.debug(f"snapshot send failed: {e}")
        try:
            while True:
                # We don't expect inbound messages currently — just keepalive.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug(f"WS error: {e}")
        finally:
            bus.disconnect(ws)

    # ----- Pillar C: Briefing + Poll API ------------------------------------
    @app.get("/api/briefing")
    async def get_briefing():
        """Generate a J.A.R.V.I.S.-style status briefing."""
        if daemon.briefing_engine is None:
            return {"briefing": "Briefing engine not available.", "error": True}
        try:
            briefing = await asyncio.to_thread(
                daemon.briefing_engine.compile_vocal_briefing, True
            )
            # Also speak it aloud
            _speak_async(daemon, briefing)
            bus.publish_threadsafe({
                "type": "message", "role": "assistant", "content": briefing
            })
            return {"briefing": briefing, "error": False}
        except Exception as e:
            logger.error(f"Briefing API error: {e}")
            return {"briefing": f"Briefing failed: {e}", "error": True}

    @app.post("/api/poll")
    async def force_poll():
        """Force an immediate background data refresh."""
        if daemon.background_worker is None:
            return {"status": "unavailable"}
        try:
            result = await daemon.background_worker.poll_now()
            return {"status": "ok", "result": result}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @app.get("/api/state")
    async def get_state():
        """Get the current integrated state context."""
        if daemon.state_store is None:
            return {"error": "State store not available"}
        return daemon.state_store.get_integrated_context()

    # ----- Phase 6: Agent Logs Ingestion (C-daemon → briefing queue) -------
    class AgentLogPayload(BaseModel):
        """Payload sent by the C-daemon (system_monitor) via HTTP POST."""
        agent: str
        timestamp: str = ""
        category: str = ""
        summary: str = ""
        data: dict = {}

    @app.post("/api/agent_logs")
    async def receive_agent_log(payload: AgentLogPayload):
        """Receive a JSON log from the C-daemon and add to briefing queue.

        The C system_monitor in WSL2 POSTs events here. The state
        manager appends them to briefing_queue.json for later synthesis.
        """
        sm = getattr(daemon, "state_manager", None)
        if sm is None:
            raise HTTPException(503, "State manager not available")

        entry = payload.model_dump()
        sm.append_to_briefing_queue(entry)

        bus.publish_threadsafe({
            "type": "agent_log",
            "agent": payload.agent,
            "category": payload.category,
            "summary": payload.summary,
        })
        logger.info(
            f"📥 Agent log received: [{payload.agent}] {payload.summary[:80]}"
        )
        return {"ok": True, "queue_size": sm.get_queue_size()}

    # ----- Phase 6: State Management API -----------------------------------
    @app.post("/api/state/away")
    async def enter_away_mode():
        """Transition to AWAY mode — mic sleeps, agents run background tasks."""
        sm = getattr(daemon, "state_manager", None)
        if sm is None:
            return {"ok": False, "error": "State manager not available"}
        ok = sm.go_away()
        bus.publish_threadsafe({"type": "state_change", "state": sm.state.value})
        return {"ok": ok, "state": sm.state.value}

    @app.post("/api/state/active")
    async def enter_active_mode():
        """Transition to ACTIVE mode — mic wakes, agents idle."""
        sm = getattr(daemon, "state_manager", None)
        if sm is None:
            return {"ok": False, "error": "State manager not available"}
        ok = sm.go_active()
        bus.publish_threadsafe({"type": "state_change", "state": sm.state.value})
        return {"ok": ok, "state": sm.state.value}

    @app.post("/api/state/briefing")
    async def enter_briefing_mode():
        """Transition to BRIEFING mode — consumes queue, synthesises narrative."""
        sm = getattr(daemon, "state_manager", None)
        if sm is None:
            return {"ok": False, "error": "State manager not available"}
        ok = sm.start_briefing()
        bus.publish_threadsafe({"type": "state_change", "state": sm.state.value})
        return {"ok": ok, "state": sm.state.value}

    @app.get("/api/state/current")
    async def get_current_state():
        """Get the current DAEMON operational state."""
        sm = getattr(daemon, "state_manager", None)
        if sm is None:
            return {"error": "State manager not available"}
        return sm.get_status()

    # ----- Phase 6: Agent Registry API -------------------------------------
    @app.get("/api/agents")
    async def list_agents():
        """List all registered persona agents."""
        registry = getattr(daemon, "agent_registry", None)
        if registry is None:
            return {"agents": [], "error": "Agent registry not available"}
        return {"agents": registry.list_agents()}

    @app.get("/api/agents/status")
    async def agents_status():
        """Get full agent registry status."""
        registry = getattr(daemon, "agent_registry", None)
        if registry is None:
            return {"error": "Agent registry not available"}
        return registry.get_status()

    return app


def serve(daemon, host: str = "127.0.0.1", port: int = 7860, *,
          start_voice: bool = True, enable_hotkeys: bool = True) -> None:
    """Run the web UI (blocking)."""
    import uvicorn

    app = create_app(daemon, start_voice=start_voice, enable_hotkeys=enable_hotkeys)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    server.run()
