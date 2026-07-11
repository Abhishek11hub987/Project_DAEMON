"""
Base Agent — Abstract Foundation for Persona Agents
=====================================================

Every persona agent in the D.A.E.M.O.N. system (Nova, Cipher, Forge, …)
inherits from this class.  It defines the contract that the
:class:`AgentRegistry` relies on for:

- **Intent routing** — matching user utterances to the right agent via
  ``keywords`` and ``can_handle()``.
- **Background tasks** — running autonomous work during AWAY mode via
  ``process_background_task()``.
- **Direct responses** — answering user questions in ACTIVE mode via
  ``generate_response()``.
- **Voice identity** — each agent carries a distinct Piper TTS voice
  model so the user can *hear* who is speaking.

.. note::

    This is intentionally **separate** from ``skills/base_skill.py``.
    Skills are lightweight keyword→handler mappings.  Agents are
    autonomous personas with their own voice, personality prompt,
    and background capabilities.

Concrete implementations live in the ``agents/`` package::

    agents/
    ├── nova_agent.py      # Academic & Document Agent
    ├── cipher_agent.py    # System Profiler Agent
    └── forge_agent.py     # Hardware Agent
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract base class for all D.A.E.M.O.N. persona agents.

    Subclasses **must** implement every ``@abstractmethod`` below.
    The :class:`AgentRegistry` uses these properties and methods to
    discover, route, and invoke agents at runtime.

    Example
    -------
    ::

        class CipherAgent(BaseAgent):

            @property
            def name(self) -> str:
                return "cipher"

            @property
            def display_name(self) -> str:
                return "Cipher"

            @property
            def description(self) -> str:
                return "System profiler — C compilations, Valgrind, CPU scheduling"

            @property
            def voice_model(self) -> str:
                return "en_GB-northern_english_male-medium"

            @property
            def personality_prompt(self) -> str:
                return "You are Cipher, a deep-voiced systems engineer..."

            @property
            def keywords(self) -> list[str]:
                return ["memory", "valgrind", "cpu", "compile", "profiler"]

            async def process_background_task(self) -> dict:
                ...

            async def generate_response(self, query: str, context=None) -> str:
                ...
    """

    # ------------------------------------------------------------------
    # Identity (must be implemented by subclasses)
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique lowercase identifier used for routing (e.g. ``"cipher"``)."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-friendly name shown in the UI (e.g. ``"Cipher"``)."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description of this agent's domain."""
        ...

    @property
    @abstractmethod
    def voice_model(self) -> str:
        """Piper TTS voice short-name (e.g. ``"en_GB-northern_english_male-medium"``).

        The registry uses this to swap the active Piper ``.onnx`` model
        before synthesising this agent's speech.
        """
        ...

    @property
    @abstractmethod
    def personality_prompt(self) -> str:
        """System prompt injected into the LLM when this agent speaks.

        Should define the agent's tone, expertise area, and speaking
        style so the generated text feels distinct from the main DAEMON
        persona.
        """
        ...

    @property
    @abstractmethod
    def keywords(self) -> List[str]:
        """Intent-routing keywords.

        The registry matches these against user utterances to decide
        which agent should handle a request.  More specific keywords
        should come first.

        Example: ``["valgrind", "memory leak", "cpu", "compile", "gcc"]``
        """
        ...

    # ------------------------------------------------------------------
    # Core capabilities (must be implemented by subclasses)
    # ------------------------------------------------------------------

    @abstractmethod
    async def process_background_task(self) -> Dict[str, Any]:
        """Run autonomous background work during AWAY mode.

        Called by :meth:`AgentRegistry.run_all_background_tasks` when
        the system enters AWAY state.

        Returns
        -------
        dict
            A JSON-serialisable payload to be appended to the briefing
            queue.  Should contain at minimum::

                {
                    "agent":    self.name,
                    "category": "...",
                    "summary":  "Human-readable one-liner",
                    "data":     { ... }   # raw structured data
                }

            Return an empty dict ``{}`` if there is nothing to report.
        """
        ...

    @abstractmethod
    async def generate_response(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate a direct response to a user query in ACTIVE mode.

        Called when the intent router determines this agent should
        handle the request (e.g. "ask Cipher for the memory report").

        Parameters
        ----------
        query
            The user's natural-language question, with routing
            prefixes already stripped (e.g. ``"what's the memory
            report?"`` not ``"ask cipher for the memory report"``).
        context
            Optional dict of extra context (conversation history,
            recent logs, etc.) that the agent can use to enrich its
            response.

        Returns
        -------
        str
            The agent's spoken response text (will be synthesised
            with this agent's TTS voice).
        """
        ...

    # ------------------------------------------------------------------
    # Optional hooks (override if needed)
    # ------------------------------------------------------------------

    async def on_register(self) -> None:
        """Called once when the registry discovers and registers this agent.

        Override to perform one-time setup (loading models, opening
        database connections, etc.).
        """
        pass

    async def on_shutdown(self) -> None:
        """Called when the system is shutting down.

        Override to release resources, close connections, flush buffers.
        """
        pass

    # ------------------------------------------------------------------
    # Convenience: intent matching
    # ------------------------------------------------------------------

    def can_handle(self, utterance: str) -> bool:
        """Check whether this agent's keywords match the user utterance.

        The default implementation does a case-insensitive substring
        search for each keyword.  Subclasses can override for more
        sophisticated matching (regex, embeddings, etc.).

        Parameters
        ----------
        utterance
            The raw user input (may include routing prefixes like
            ``"ask cipher"``).

        Returns
        -------
        bool
        """
        lower = utterance.lower()
        return any(kw.lower() in lower for kw in self.keywords)

    def keyword_score(self, utterance: str) -> int:
        """Count how many keywords match the utterance.

        Used by the registry to break ties when multiple agents match —
        the agent with the highest score wins.
        """
        lower = utterance.lower()
        return sum(1 for kw in self.keywords if kw.lower() in lower)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe summary of this agent's identity."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "voice_model": self.voice_model,
            "keywords": self.keywords,
        }

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} name={self.name!r} "
            f"voice={self.voice_model!r}>"
        )
