"""
Narrative Briefing Engine — J.A.R.V.I.S. Style
================================================

Connects to the existing ``LLMEngine`` and ``UnifiedStateStore`` to
generate flowing, speakable status briefings.

The ``compile_vocal_briefing()`` method:

1. Fetches current context from ``UnifiedStateStore.get_integrated_context()``
2. Optionally triggers a fresh poll via ``ProactiveMonitor.poll_now_sync()``
3. Formats a dense system prompt forcing the LLM to act like a dry,
   professional British AI (J.A.R.V.I.S.)
4. Passes the data to the LLM and returns a short, flowing narrative
   suitable for Piper TTS

The output groups metrics naturally — transitioning from email volume
into GitHub PRs — and ends by asking what to execute first.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from core_logic.state_store import UnifiedStateStore

logger = logging.getLogger(__name__)


_BRIEFING_SYSTEM_PROMPT = """\
You are D.A.E.M.O.N., a voice AI assistant modelled after J.A.R.V.I.S. — dry, \
professional, British-inflected, quietly competent.

You are about to deliver a STATUS BRIEFING to the user (spoken aloud via TTS). \
Follow these rules STRICTLY:

VOICE STYLE
- Speak like a seasoned British butler-engineer: calm, precise, a touch sardonic.
- Use contractions naturally ("you've", "there's", "I'd").
- Brief, flowing sentences. No bullet points, no markdown, no numbered lists.
- Transition between topics smoothly (e.g., "On the comms front...", \
"Meanwhile, over on GitHub...", "As for the numbers...").

LENGTH
- Aim for 60-120 words total. This is spoken aloud — keep it tight.
- Do NOT pad with filler. Every sentence must carry information.

CONTENT
- Group related metrics together naturally.
- If a section has no data or an error, mention it casually: "Email's offline \
at the moment" or "GitHub credentials aren't wired up yet."
- End with a short prompt like "What would you like me to tackle first?" or \
"Shall I dig into any of those?"

CRITICAL
- Do NOT invent data. Use ONLY what's provided in the context payload below.
- Do NOT add markdown, headings, or formatting — this is pure spoken text.
- Do NOT say "as an AI" or break character.
"""


class BriefingEngine:
    """Generates J.A.R.V.I.S.-style vocal briefings from live state data.

    Parameters
    ----------
    state_store
        The ``UnifiedStateStore`` to read context from.
    llm
        The ``LLMEngine`` instance for generating narrative text.
    background_worker
        Optional ``ProactiveMonitor`` for on-demand fresh polls.
    """

    def __init__(
        self,
        state_store: Optional[UnifiedStateStore] = None,
        llm: Optional[Any] = None,
        background_worker: Optional[Any] = None,
    ) -> None:
        self._store = state_store or UnifiedStateStore()
        self._llm = llm
        self._worker = background_worker

        logger.info("🎤 BriefingEngine initialised.")

    def _get_llm(self):
        """Lazy-load LLM to avoid circular imports."""
        if self._llm is None:
            from core_logic.llm_engine import LLMEngine
            self._llm = LLMEngine()
        return self._llm

    def compile_vocal_briefing(self, fresh_poll: bool = False) -> str:
        """Generate a J.A.R.V.I.S.-style spoken status briefing.

        Parameters
        ----------
        fresh_poll
            If True and a background worker is available, trigger an
            immediate poll before compiling the briefing.

        Returns
        -------
        str
            A flowing, speakable narrative ready for TTS.
        """
        # Optionally poll fresh data
        if fresh_poll and self._worker:
            try:
                self._worker.poll_now_sync()
                logger.info("🎤 Fresh poll completed before briefing.")
            except Exception as e:
                logger.warning(f"Fresh poll failed: {e}")

        # Get integrated context
        ctx = self._store.get_integrated_context()

        # Build the data block for the LLM
        data_block = self._format_context_for_llm(ctx)

        # Build the full prompt
        prompt = (
            f"Here is the current system state data. Use ONLY this data to "
            f"generate the briefing:\n\n{data_block}\n\n"
            f"Current time: {datetime.now().strftime('%I:%M %p, %A %B %d')}\n\n"
            f"Now deliver the spoken briefing."
        )

        try:
            llm = self._get_llm()
            response = llm.generate(
                prompt=prompt,
                system_prompt=_BRIEFING_SYSTEM_PROMPT,
                temperature=0.6,
                max_tokens=300,
            )
            briefing = (response or "").strip()
            if not briefing:
                briefing = self._fallback_briefing(ctx)
            logger.info(f"🎤 Briefing generated ({len(briefing)} chars)")
            return briefing

        except Exception as e:
            logger.error(f"Briefing LLM generation failed: {e}")
            return self._fallback_briefing(ctx)

    def _format_context_for_llm(self, ctx: Dict[str, Any]) -> str:
        """Format the state context into a readable data block."""
        lines = []

        # Support / Email
        support = ctx.get("support", {})
        unread = support.get("unread_count", 0)
        total = support.get("total_inbox", 0)
        recent = support.get("recent_subjects", [])
        email_error = support.get("error")

        if email_error and "not configured" in str(email_error).lower():
            lines.append("EMAIL: Not configured (credentials missing)")
        elif email_error:
            lines.append(f"EMAIL: Error — {email_error}")
        else:
            lines.append(f"EMAIL: {unread} unread out of {total} total")
            if recent:
                lines.append("  Recent emails:")
                for e in recent[:3]:
                    sender = e.get("sender", "Unknown")
                    subject = e.get("subject", "(no subject)")
                    lines.append(f"    - From {sender}: \"{subject}\"")

        # Pipeline / GitHub
        pipeline = ctx.get("pipeline", {})
        pr_count = pipeline.get("open_pr_count", 0)
        prs = pipeline.get("pr_details", [])
        repo = pipeline.get("repo", "")
        gh_error = pipeline.get("error")

        if gh_error and "not configured" in str(gh_error).lower():
            lines.append("GITHUB: Not configured (credentials missing)")
        elif gh_error:
            lines.append(f"GITHUB: Error — {gh_error}")
        else:
            lines.append(f"GITHUB [{repo}]: {pr_count} open pull requests")
            if prs:
                for pr in prs[:5]:
                    title = pr.get("title", "")
                    author = pr.get("author", "unknown")
                    draft = " (draft)" if pr.get("draft") else ""
                    lines.append(f"    - \"{title}\" by {author}{draft}")

        # Marketing (placeholder)
        marketing = ctx.get("marketing", {})
        if marketing.get("active_users", 0) > 0:
            lines.append(
                f"MARKETING: {marketing['active_users']} active users, "
                f"{marketing.get('signups_today', 0)} signups, "
                f"${marketing.get('revenue_today', 0):.2f} revenue"
            )

        return "\n".join(lines)

    @staticmethod
    def _fallback_briefing(ctx: Dict[str, Any]) -> str:
        """Generate a basic briefing without the LLM (fallback)."""
        parts = []
        parts.append("Right then, here's where things stand.")

        support = ctx.get("support", {})
        unread = support.get("unread_count", 0)
        if unread > 0:
            parts.append(f"You've got {unread} unread emails waiting.")
        else:
            parts.append("Inbox is clear — no unread emails.")

        pipeline = ctx.get("pipeline", {})
        pr_count = pipeline.get("open_pr_count", 0)
        repo = pipeline.get("repo", "")
        if pr_count > 0:
            parts.append(
                f"Over on GitHub, there are {pr_count} open pull requests "
                f"on {repo}."
            )
        elif repo:
            parts.append(f"No open pull requests on {repo}.")

        parts.append("What would you like me to tackle first?")
        return " ".join(parts)

    def get_raw_context(self) -> Dict[str, Any]:
        """Return the raw integrated context (for debugging/API)."""
        return self._store.get_integrated_context()
