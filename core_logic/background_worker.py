"""
Background Polling Worker — Proactive Monitor
===============================================

Runs an ``asyncio`` loop (``execution_heartbeat``) that polls every
5 minutes (configurable):

1. Calls ``EmailMonitor.fetch_inbox_status()``
2. Calls ``GitHubMonitor.fetch_pull_requests()``
3. Writes results into ``UnifiedStateStore``

The worker runs as an async task alongside the FastAPI event loop and
can be started/stopped cleanly.

Configuration via ``.env``::

    BACKGROUND_POLL_INTERVAL=300   # seconds between polls (default 5 min)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Dict, Optional

from core_logic.state_store import UnifiedStateStore
from skills.email_monitor import EmailMonitor
from skills.github_monitor import GitHubMonitor

logger = logging.getLogger(__name__)


class ProactiveMonitor:
    """Background polling loop that feeds live-world data into DAEMON's state.

    Parameters
    ----------
    state_store
        The ``UnifiedStateStore`` instance to write results into.
    poll_interval
        Seconds between polling cycles (default: 300 = 5 minutes).
    event_callback
        Optional callback for broadcasting polling events to WebSocket.
    """

    def __init__(
        self,
        state_store: Optional[UnifiedStateStore] = None,
        poll_interval: Optional[float] = None,
        event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self._store = state_store or UnifiedStateStore()
        self._interval = poll_interval or float(
            os.getenv("BACKGROUND_POLL_INTERVAL", "300")
        )
        self._emit = event_callback or (lambda e: None)

        # Sensors
        self._email = EmailMonitor()
        self._github = GitHubMonitor()

        # Task management
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._poll_count = 0

        logger.info(
            f"🔄 ProactiveMonitor created — poll every {self._interval}s, "
            f"email={'✅' if self._email.configured else '❌'}, "
            f"github={'✅' if self._github.configured else '❌'}"
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Start the background heartbeat as an asyncio Task."""
        if self._running:
            return

        self._running = True

        if loop:
            self._task = loop.create_task(self._execution_heartbeat())
        else:
            self._task = asyncio.ensure_future(self._execution_heartbeat())

        logger.info("🔄 ProactiveMonitor started.")

    def stop(self) -> None:
        """Stop the background polling."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("🔄 ProactiveMonitor stopped.")

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Core heartbeat
    # ------------------------------------------------------------------

    async def _execution_heartbeat(self) -> None:
        """Core async loop — polls sensors and updates state store."""
        logger.info("🔄 Background polling loop running.")

        # Run first poll immediately on startup, then every interval
        while self._running:
            try:
                self._poll_count += 1
                logger.info(f"🔄 Poll cycle #{self._poll_count} starting...")

                # Emit polling event
                self._emit({
                    "type": "background_poll",
                    "status": "polling",
                    "cycle": self._poll_count,
                })

                # ── Email sensor ──────────────────────────────────
                email_result = await asyncio.to_thread(
                    self._email.fetch_inbox_status
                )
                self._store.update_support_metrics(
                    unread_count=email_result.get("unread_count", 0),
                    total_inbox=email_result.get("total_inbox", 0),
                    recent_subjects=email_result.get("recent", []),
                    source="gmail",
                )
                if email_result.get("error"):
                    logger.warning(
                        f"Email poll warning: {email_result['error']}"
                    )

                # ── GitHub sensor ─────────────────────────────────
                github_result = await asyncio.to_thread(
                    self._github.fetch_pull_requests
                )
                self._store.update_agent_pipeline(
                    open_pr_count=github_result.get("open_pr_count", 0),
                    pr_details=github_result.get("prs", []),
                    agent_status="idle" if not github_result.get("error") else "error",
                    repo=github_result.get("repo", ""),
                )
                if github_result.get("error"):
                    logger.warning(
                        f"GitHub poll warning: {github_result['error']}"
                    )

                # Emit completion event
                self._emit({
                    "type": "background_poll",
                    "status": "completed",
                    "cycle": self._poll_count,
                    "email_unread": email_result.get("unread_count", 0),
                    "github_prs": github_result.get("open_pr_count", 0),
                })

                logger.info(
                    f"🔄 Poll cycle #{self._poll_count} complete — "
                    f"Email: {email_result.get('unread_count', 0)} unread, "
                    f"GitHub: {github_result.get('open_pr_count', 0)} open PRs"
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Background poll error: {e}", exc_info=True)
                self._emit({
                    "type": "background_poll",
                    "status": "error",
                    "cycle": self._poll_count,
                    "error": str(e),
                })

            # Wait for next cycle
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

        logger.info("🔄 Background polling loop exited.")

    # ------------------------------------------------------------------
    # Manual trigger
    # ------------------------------------------------------------------

    async def poll_now(self) -> Dict[str, Any]:
        """Trigger an immediate poll cycle and return results.

        Useful for the briefing engine to get fresh data on demand.
        """
        logger.info("🔄 Manual poll triggered.")

        email_result = await asyncio.to_thread(
            self._email.fetch_inbox_status
        )
        github_result = await asyncio.to_thread(
            self._github.fetch_pull_requests
        )

        # Update state store
        self._store.update_support_metrics(
            unread_count=email_result.get("unread_count", 0),
            total_inbox=email_result.get("total_inbox", 0),
            recent_subjects=email_result.get("recent", []),
        )
        self._store.update_agent_pipeline(
            open_pr_count=github_result.get("open_pr_count", 0),
            pr_details=github_result.get("prs", []),
            agent_status="idle" if not github_result.get("error") else "error",
            repo=github_result.get("repo", ""),
        )

        return {
            "email": email_result,
            "github": github_result,
        }

    def poll_now_sync(self) -> Dict[str, Any]:
        """Synchronous version of poll_now for non-async callers."""
        email_result = self._email.fetch_inbox_status()
        github_result = self._github.fetch_pull_requests()

        self._store.update_support_metrics(
            unread_count=email_result.get("unread_count", 0),
            total_inbox=email_result.get("total_inbox", 0),
            recent_subjects=email_result.get("recent", []),
        )
        self._store.update_agent_pipeline(
            open_pr_count=github_result.get("open_pr_count", 0),
            pr_details=github_result.get("prs", []),
            agent_status="idle" if not github_result.get("error") else "error",
            repo=github_result.get("repo", ""),
        )

        return {"email": email_result, "github": github_result}

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "poll_count": self._poll_count,
            "interval_seconds": self._interval,
            "email_configured": self._email.configured,
            "github_configured": self._github.configured,
        }
