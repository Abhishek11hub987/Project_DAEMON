"""
Agent Registry — Dynamic Discovery, Intent Routing, Voice Swapping
====================================================================

The registry is the central nervous system for D.A.E.M.O.N.'s persona
agents.  At startup it scans the ``agents/`` package, discovers every
concrete :class:`BaseAgent` subclass, and builds a lookup table for
real-time intent routing and TTS voice swapping.

Responsibilities
----------------
1. **Dynamic discovery** — ``importlib`` + ``inspect`` scan of
   ``agents/*.py`` for ``BaseAgent`` subclasses.
2. **Name → Agent mapping** — ``registry.get("cipher")`` returns the
   ``CipherAgent`` instance.
3. **Intent routing** — ``registry.route_to_agent(utterance)`` parses
   the user's text, matches against each agent's keywords, and returns
   ``(agent, cleaned_query)`` or ``(None, original)`` if no agent
   matched.
4. **Voice swapping** — ``registry.speak_as(agent, text, tts_engine)``
   temporarily loads the agent's Piper ``.onnx`` voice, synthesises the
   text, then restores the previous voice.  Follows the same HuggingFace
   auto-download pattern as ``tts_engine.py``.
5. **Background dispatch** — ``registry.run_all_background_tasks()``
   iterates all agents and collects their AWAY-mode results for the
   briefing queue.

Thread Safety
-------------
The registry itself is read-only after ``__init__`` completes.  The
only mutable operation — voice swapping — acquires a lock to prevent
two agents from speaking simultaneously.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import io
import logging
import os
import pkgutil
import re
import threading
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core_logic.base_agent import BaseAgent
from core_logic.config import Config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Package path for agent discovery
# ---------------------------------------------------------------------------
_AGENTS_PACKAGE = "agents"
_AGENTS_DIR = Config.PROJECT_ROOT / "agents"


class AgentRegistry:
    """Plug-and-play agent loader, router, and voice manager.

    Parameters
    ----------
    agents_package
        Dotted package name to scan (default ``"agents"``).
    agents_dir
        Filesystem path to the agents package directory.

    Usage::

        registry = AgentRegistry()
        print(registry.list_agents())

        # Intent routing
        agent, query = registry.route_to_agent(
            "ask cipher for the memory report"
        )
        if agent:
            response = await agent.generate_response(query)
            registry.speak_as(agent, response, tts_engine)

        # Background sweep (AWAY mode)
        results = await registry.run_all_background_tasks()
    """

    def __init__(
        self,
        agents_package: str = _AGENTS_PACKAGE,
        agents_dir: Optional[Path] = None,
    ) -> None:
        self._package = agents_package
        self._dir = agents_dir or _AGENTS_DIR
        self._agents: Dict[str, BaseAgent] = {}

        # Lock for TTS voice swapping — only one agent speaks at a time
        self._voice_lock = threading.Lock()

        # Cache of loaded Piper voices (voice_model → PiperVoice)
        self._voice_cache: Dict[str, Any] = {}

        # Discover and register agents
        self._discover_agents()

        logger.info(
            f"🤖 AgentRegistry ready — {len(self._agents)} agent(s): "
            f"{', '.join(self._agents.keys()) or '(none)'}"
        )

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover_agents(self) -> None:
        """Scan the agents package for BaseAgent subclasses.

        Uses ``pkgutil.iter_modules`` + ``importlib.import_module`` to
        find and instantiate every concrete subclass of ``BaseAgent``
        in the agents directory.  Agents with duplicate names are logged
        and skipped.
        """
        if not self._dir.exists():
            logger.warning(
                f"Agents directory does not exist: {self._dir}  "
                f"— no agents loaded."
            )
            return

        for finder, module_name, is_pkg in pkgutil.iter_modules(
            [str(self._dir)]
        ):
            if is_pkg or module_name.startswith("_"):
                continue

            fqn = f"{self._package}.{module_name}"
            try:
                module = importlib.import_module(fqn)
            except Exception as exc:
                logger.warning(
                    f"Failed to import agent module '{fqn}': {exc}"
                )
                continue

            # Find all concrete BaseAgent subclasses in this module
            for attr_name, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, BaseAgent)
                    and obj is not BaseAgent
                    and not inspect.isabstract(obj)
                ):
                    try:
                        instance = obj()
                        name = instance.name.lower()

                        if name in self._agents:
                            logger.warning(
                                f"Duplicate agent name '{name}' in "
                                f"{fqn}.{attr_name} — skipped."
                            )
                            continue

                        self._agents[name] = instance
                        logger.info(
                            f"  ✅ Registered agent: {instance.display_name} "
                            f"({name}) — voice={instance.voice_model}"
                        )
                    except Exception as exc:
                        logger.warning(
                            f"Failed to instantiate {fqn}.{attr_name}: {exc}"
                        )

    def reload(self) -> None:
        """Re-scan the agents directory (hot-reload support)."""
        self._agents.clear()
        self._discover_agents()

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[BaseAgent]:
        """Return the agent with the given name, or None."""
        return self._agents.get(name.lower())

    def list_agents(self) -> List[Dict[str, Any]]:
        """Return a list of registered agent summaries."""
        return [agent.to_dict() for agent in self._agents.values()]

    @property
    def agent_names(self) -> List[str]:
        """All registered agent names."""
        return list(self._agents.keys())

    def __contains__(self, name: str) -> bool:
        return name.lower() in self._agents

    def __len__(self) -> int:
        return len(self._agents)

    # ------------------------------------------------------------------
    # Intent Routing
    # ------------------------------------------------------------------

    # Patterns that indicate the user is addressing a specific agent:
    #   "ask cipher for the memory report"
    #   "hey nova, summarise this PDF"
    #   "daemon, ask forge about the print job"
    #   "tell cipher to check valgrind"
    _ROUTING_PATTERNS = [
        # "ask <agent> (for|about|to) <query>"
        re.compile(
            r"(?:ask|tell|have)\s+(\w+)\s+(?:for|about|to|if)\s+(.+)",
            re.IGNORECASE,
        ),
        # "hey <agent>, <query>"
        re.compile(
            r"(?:hey|hi|yo)\s+(\w+)[,:]?\s+(.+)",
            re.IGNORECASE,
        ),
        # "<agent>, <query>"  (direct address)
        re.compile(
            r"^(\w+)[,:]?\s+(.+)",
            re.IGNORECASE,
        ),
    ]

    def route_to_agent(
        self, utterance: str
    ) -> Tuple[Optional[BaseAgent], str]:
        """Parse user utterance and route to the appropriate agent.

        Tries two strategies:

        1. **Explicit address** — patterns like "ask cipher for …" or
           "hey nova, …" where the agent name appears literally.
        2. **Keyword match** — if no explicit address, check each
           agent's ``can_handle()`` and pick the one with the highest
           ``keyword_score()``.

        Parameters
        ----------
        utterance
            The raw user input (may include routing prefixes).

        Returns
        -------
        tuple[BaseAgent | None, str]
            ``(agent, cleaned_query)`` if a match is found, otherwise
            ``(None, original_utterance)``.
        """
        # Strategy 1: explicit agent name in routing prefix
        for pattern in self._ROUTING_PATTERNS:
            match = pattern.match(utterance.strip())
            if match:
                candidate_name = match.group(1).lower()
                query = match.group(2).strip()
                agent = self._agents.get(candidate_name)
                if agent:
                    logger.info(
                        f"🎯 Routed to {agent.display_name} (explicit): "
                        f"'{query[:60]}'"
                    )
                    return agent, query

        # Strategy 2: keyword scoring across all agents
        best_agent: Optional[BaseAgent] = None
        best_score = 0

        for agent in self._agents.values():
            if agent.can_handle(utterance):
                score = agent.keyword_score(utterance)
                if score > best_score:
                    best_score = score
                    best_agent = agent

        if best_agent:
            logger.info(
                f"🎯 Routed to {best_agent.display_name} "
                f"(keywords, score={best_score}): '{utterance[:60]}'"
            )
            return best_agent, utterance

        return None, utterance

    # ------------------------------------------------------------------
    # Voice Swapping (Piper TTS)
    # ------------------------------------------------------------------

    def speak_as(
        self,
        agent: BaseAgent,
        text: str,
        tts_engine: Any,
    ) -> None:
        """Synthesise ``text`` using the agent's Piper voice.

        Thread-safe — acquires ``_voice_lock`` so only one agent
        speaks at a time.  Falls back to the default TTS voice if
        the agent's voice model cannot be loaded.

        Parameters
        ----------
        agent
            The agent whose voice to use.
        text
            The text to speak.
        tts_engine
            The ``TextToSpeechEngine`` instance from ``audio/tts_engine.py``.
        """
        if not text or not text.strip():
            return

        with self._voice_lock:
            # Snapshot the current voice so we can restore it after
            original_voice = getattr(tts_engine, "voice", None)
            original_piper_voice = getattr(tts_engine, "_piper_voice", None)

            try:
                agent_voice_model = agent.voice_model

                # Load the agent's Piper voice (cached)
                piper_voice = self._load_piper_voice(
                    agent_voice_model, tts_engine
                )

                if piper_voice:
                    # Swap to the agent's voice
                    tts_engine._piper_voice = piper_voice
                    tts_engine.voice = agent_voice_model

                    logger.info(
                        f"🔊 Speaking as {agent.display_name} "
                        f"(voice={agent_voice_model})"
                    )

                    # Force Piper backend for this utterance
                    saved_backend = tts_engine.backend
                    tts_engine.backend = "piper"
                    try:
                        tts_engine.speak(text)
                    finally:
                        tts_engine.backend = saved_backend
                else:
                    # Fallback: use whatever TTS is configured
                    logger.warning(
                        f"Could not load Piper voice '{agent_voice_model}' "
                        f"for {agent.display_name} — using default voice."
                    )
                    tts_engine.speak(text)

            except Exception as exc:
                logger.error(
                    f"Voice swap failed for {agent.display_name}: {exc}"
                )
                # Last resort: speak with default voice
                try:
                    tts_engine.speak(text)
                except Exception:
                    pass

            finally:
                # Restore original voice
                tts_engine._piper_voice = original_piper_voice
                if original_voice:
                    tts_engine.voice = original_voice

    def _load_piper_voice(
        self, voice_model: str, tts_engine: Any
    ) -> Any:
        """Load a Piper voice model, using cache and auto-download.

        Follows the same HuggingFace download pattern as
        ``TextToSpeechEngine._ensure_piper_voice()``.

        Returns the ``PiperVoice`` object, or None on failure.
        """
        # Check cache first
        if voice_model in self._voice_cache:
            return self._voice_cache[voice_model]

        try:
            from piper import PiperVoice
        except ImportError:
            logger.warning(
                "piper-tts not installed — cannot load agent voices. "
                "Install with: pip install piper-tts"
            )
            return None

        model_dir = Config.PROJECT_ROOT / "models" / "piper"
        model_dir.mkdir(parents=True, exist_ok=True)
        onnx_path = model_dir / f"{voice_model}.onnx"
        json_path = model_dir / f"{voice_model}.onnx.json"

        # Auto-download if not cached on disk
        if not onnx_path.exists() or not json_path.exists():
            try:
                import requests

                onnx_url, json_url = tts_engine._piper_hf_paths(voice_model)
                logger.info(f"⬇️  Downloading Piper voice '{voice_model}'...")

                for url, dest in [(json_url, json_path), (onnx_url, onnx_path)]:
                    with requests.get(url, stream=True, timeout=120) as r:
                        r.raise_for_status()
                        tmp = dest.with_suffix(dest.suffix + ".part")
                        with open(tmp, "wb") as f:
                            for chunk in r.iter_content(chunk_size=1 << 16):
                                f.write(chunk)
                        tmp.replace(dest)

                logger.info(f"✅ Piper voice '{voice_model}' cached.")

            except Exception as exc:
                logger.error(
                    f"Failed to download Piper voice '{voice_model}': {exc}"
                )
                return None

        # Load the voice
        try:
            voice = PiperVoice.load(str(onnx_path))
            self._voice_cache[voice_model] = voice
            return voice
        except Exception as exc:
            logger.error(f"Failed to load Piper voice '{voice_model}': {exc}")
            return None

    # ------------------------------------------------------------------
    # Background Dispatch (AWAY mode)
    # ------------------------------------------------------------------

    async def run_all_background_tasks(self) -> List[Dict[str, Any]]:
        """Execute every agent's background task and collect results.

        Called by the state manager when entering AWAY mode.  Runs
        all agents concurrently via ``asyncio.gather``.

        Returns
        -------
        list[dict]
            List of non-empty result dicts from each agent, ready to
            be appended to the briefing queue.
        """
        if not self._agents:
            return []

        logger.info(
            f"🔄 Running background tasks for {len(self._agents)} agent(s)..."
        )

        async def _safe_run(agent: BaseAgent) -> Optional[Dict[str, Any]]:
            try:
                result = await agent.process_background_task()
                if result:
                    # Ensure the agent name is stamped on the result
                    result.setdefault("agent", agent.name)
                    logger.info(
                        f"  📋 {agent.display_name}: "
                        f"{result.get('summary', '(no summary)')[:80]}"
                    )
                    return result
            except Exception as exc:
                logger.error(
                    f"Background task failed for {agent.display_name}: {exc}"
                )
                return {
                    "agent": agent.name,
                    "category": "error",
                    "summary": f"Background task failed: {exc}",
                    "data": {"error": str(exc)},
                }
            return None

        raw_results = await asyncio.gather(
            *[_safe_run(agent) for agent in self._agents.values()]
        )

        # Filter out None / empty results
        results = [r for r in raw_results if r]
        logger.info(
            f"🔄 Background sweep complete — {len(results)} report(s) generated."
        )
        return results

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def register_all(self) -> None:
        """Call ``on_register()`` on every discovered agent."""
        for agent in self._agents.values():
            try:
                await agent.on_register()
            except Exception as exc:
                logger.warning(
                    f"on_register failed for {agent.display_name}: {exc}"
                )

    async def shutdown_all(self) -> None:
        """Call ``on_shutdown()`` on every agent."""
        for agent in self._agents.values():
            try:
                await agent.on_shutdown()
            except Exception as exc:
                logger.warning(
                    f"on_shutdown failed for {agent.display_name}: {exc}"
                )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Return serialisable registry status for the HUD / API."""
        return {
            "agent_count": len(self._agents),
            "agents": self.list_agents(),
            "cached_voices": list(self._voice_cache.keys()),
        }

    def __repr__(self) -> str:
        names = ", ".join(self._agents.keys()) or "(empty)"
        return f"<AgentRegistry [{names}]>"
