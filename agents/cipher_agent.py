"""
Cipher — System Profiler Agent
================================

Persona: Deep-voiced, precise, technically exacting.
Voice:   Male (en_GB-northern_english_male-medium)

Capabilities:
- AWAY mode: Reads the briefing queue for entries posted by the C-daemon
  (``system_monitor.c``) — GCC compilation events, Valgrind memory leak
  reports, and CPU scheduling logs — and consolidates them into a
  systems status report.
- ACTIVE mode: Answers queries about compilations, memory leaks, system
  performance, and C-code profiling results.

The C-daemon in WSL2 POSTs events to ``/api/agent_logs`` which the
StateManager deposits into ``briefing_queue.json``. Cipher reads those
entries during background sweeps.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core_logic.base_agent import BaseAgent
from core_logic.config import Config

logger = logging.getLogger(__name__)

_CIPHER_PERSONALITY = """\
You are Cipher, the systems profiler within the D.A.E.M.O.N. system.

Personality:
- Deep, authoritative voice — like a veteran systems engineer
- Technically precise — quote exact numbers (bytes, error counts, PIDs)
- Dry and matter-of-fact, with occasional understated commentary
- Think of a senior kernel developer delivering a post-mortem

Expertise:
- GCC compilation analysis (warnings, errors, optimisation flags)
- Valgrind memory leak interpretation (definitely lost, indirectly lost)
- CPU scheduling and process monitoring
- C-code profiling and performance analysis

When reporting Valgrind results, always mention:
1. Whether the run was clean or had leaks
2. Bytes definitely lost (the critical number)
3. Alloc/free balance
4. Error count

Speak naturally — no markdown, no bullet points. This is spoken aloud.
"""


class CipherAgent(BaseAgent):
    """System Profiler Agent — C compilations, Valgrind, CPU scheduling."""

    @property
    def name(self) -> str:
        return "cipher"

    @property
    def display_name(self) -> str:
        return "Cipher"

    @property
    def description(self) -> str:
        return "System profiler — GCC compilations, Valgrind memory leaks, CPU scheduling"

    @property
    def voice_model(self) -> str:
        return os.getenv("CIPHER_VOICE_MODEL", "en_GB-northern_english_male-medium")

    @property
    def personality_prompt(self) -> str:
        return _CIPHER_PERSONALITY

    @property
    def keywords(self) -> List[str]:
        return [
            "cipher",
            "memory leak", "valgrind", "memory report",
            "compile", "compilation", "gcc", "build status",
            "cpu", "scheduling", "profiler", "profiling",
            "system monitor", "c daemon",
            "object file", "linker", "segfault",
        ]

    async def process_background_task(self) -> Dict[str, Any]:
        """Consolidate C-daemon events from the briefing queue.

        Reads the briefing queue for entries with agent='cipher'
        (posted by the C-daemon via ``/api/agent_logs``) and produces
        a consolidated systems status report.

        Note: This reads from the queue file directly rather than
        consuming it — the StateManager's ``consume_briefing_queue()``
        handles the actual clear during BRIEFING mode.
        """
        # Read the queue to find cipher-specific entries
        queue_path = Path(
            os.getenv(
                "BRIEFING_QUEUE_PATH",
                str(Config.LOGS_DIR / "briefing_queue.json"),
            )
        )

        cipher_events: List[Dict[str, Any]] = []

        try:
            if queue_path.exists():
                text = queue_path.read_text(encoding="utf-8").strip()
                if text:
                    all_entries = json.loads(text)
                    if isinstance(all_entries, list):
                        cipher_events = [
                            e for e in all_entries
                            if e.get("agent") == "cipher"
                        ]
        except Exception as exc:
            logger.warning(f"Cipher: failed to read queue: {exc}")

        if not cipher_events:
            # Nothing from the C-daemon — check if the monitor is running
            # by looking for recent heartbeat events
            return {}

        # Categorise events
        compilations = [
            e for e in cipher_events
            if e.get("category") in ("gcc_compilation", "source_change")
        ]
        valgrind_reports = [
            e for e in cipher_events if e.get("category") == "valgrind"
        ]
        heartbeats = [
            e for e in cipher_events if e.get("category") == "heartbeat"
        ]

        # Build summary
        parts: List[str] = []

        if compilations:
            parts.append(
                f"{len(compilations)} compilation event(s) detected"
            )

        if valgrind_reports:
            # Summarise the most recent Valgrind report
            latest = valgrind_reports[-1]
            data = latest.get("data", {})
            leaked = data.get("definitely_lost_bytes", 0)
            errors = data.get("error_count", 0)

            if leaked == 0 and errors == 0:
                parts.append("Latest Valgrind run: clean, no leaks")
            else:
                parts.append(
                    f"Latest Valgrind: {leaked} bytes definitely lost, "
                    f"{errors} error(s)"
                )

        if heartbeats:
            latest_hb = heartbeats[-1]
            status = latest_hb.get("data", {}).get("status", "unknown")
            parts.append(f"System monitor status: {status}")

        summary = ". ".join(parts) + "." if parts else "No system events."

        return {
            "agent": self.name,
            "category": "system_profiler",
            "summary": summary,
            "data": {
                "compilation_count": len(compilations),
                "valgrind_count": len(valgrind_reports),
                "heartbeat_count": len(heartbeats),
                "total_events": len(cipher_events),
                "latest_valgrind": (
                    valgrind_reports[-1].get("data")
                    if valgrind_reports else None
                ),
            },
        }

    async def generate_response(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Answer queries about system profiling, compilations, leaks.

        Uses the LLM with Cipher's personality prompt if available.
        Falls back to a direct data dump from the briefing queue.
        """
        # Gather recent data for context
        bg_data = await self.process_background_task()

        llm = (context or {}).get("llm")
        if llm:
            data_block = json.dumps(bg_data, indent=2, default=str)
            enriched_prompt = (
                f"The user asked: {query}\n\n"
                f"Here is the latest systems data from the C-daemon:\n"
                f"{data_block}\n\n"
                f"Answer the user's question using ONLY this data. "
                f"If there's no data, say the system monitor hasn't "
                f"reported anything recently."
            )
            try:
                response = llm.generate(
                    prompt=enriched_prompt,
                    system_prompt=self.personality_prompt,
                    temperature=0.4,
                    max_tokens=400,
                )
                return response
            except Exception as exc:
                logger.warning(f"Cipher LLM generation failed: {exc}")

        # Fallback: direct summary
        if not bg_data or not bg_data.get("data"):
            return (
                "The system monitor hasn't reported any events recently. "
                "Either the C-daemon isn't running in WSL, or there "
                "haven't been any compilations or Valgrind runs."
            )

        return bg_data.get("summary", "No system events to report.")
