"""
Unified State Store — DAEMON's Executive Memory
=================================================

Centralised SQLite database (``logs/daemon_state.db``) that holds the
latest snapshot from every live-world sensor:

- **marketing_vitals** — placeholder for future marketing KPIs
- **support_metrics**  — email inbox status (unread count, recent subjects)
- **agent_pipeline**   — GitHub PR status, sub-agent activity

The store exposes ``get_integrated_context()`` which compiles the most
recent row from every table into a single dictionary — ready to be
injected into the LLM briefing prompt.

Design decisions
----------------
- WAL journal mode for concurrent read/write from the polling loop and
  the briefing engine simultaneously.
- Each table uses an ``updated_at`` column so we always know data freshness.
- Upsert pattern: we keep only the *latest* snapshot per source (no
  unbounded history — that's what ``analytics.py`` is for).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core_logic.config import Config

logger = logging.getLogger(__name__)

_DB_PATH = Config.LOGS_DIR / "daemon_state.db"

_SCHEMA_SQL = """
-- Marketing vitals (placeholder — wire to real analytics later)
CREATE TABLE IF NOT EXISTS marketing_vitals (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    active_users   INTEGER DEFAULT 0,
    signups_today  INTEGER DEFAULT 0,
    revenue_today  REAL    DEFAULT 0.0,
    conversion_pct REAL    DEFAULT 0.0,
    updated_at     TEXT    NOT NULL
);

-- Support / email metrics
CREATE TABLE IF NOT EXISTS support_metrics (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    unread_count    INTEGER DEFAULT 0,
    total_inbox     INTEGER DEFAULT 0,
    recent_subjects TEXT    DEFAULT '[]',   -- JSON array of {sender, subject}
    source          TEXT    DEFAULT 'gmail',
    updated_at      TEXT    NOT NULL
);

-- Agent pipeline / GitHub PRs
CREATE TABLE IF NOT EXISTS agent_pipeline (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    open_pr_count   INTEGER DEFAULT 0,
    pr_details      TEXT    DEFAULT '[]',   -- JSON array of {title, author, url, created}
    agent_status    TEXT    DEFAULT 'idle',  -- idle / polling / error
    repo            TEXT    DEFAULT '',
    updated_at      TEXT    NOT NULL
);
"""


class UnifiedStateStore:
    """DAEMON's short-term executive memory backed by SQLite.

    Usage::

        store = UnifiedStateStore()
        store.update_support_metrics(unread=5, subjects=[...])
        ctx = store.get_integrated_context()
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._path = db_path or _DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

        # Seed empty rows so UPSERTs always have a target
        now = _now_iso()
        for table in ("marketing_vitals", "support_metrics", "agent_pipeline"):
            try:
                self._conn.execute(
                    f"INSERT OR IGNORE INTO {table} (id, updated_at) VALUES (1, ?)",
                    (now,),
                )
            except Exception:
                pass
        self._conn.commit()
        logger.info(f"📦 UnifiedStateStore ready — {self._path}")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Writers
    # ------------------------------------------------------------------

    def update_marketing_vitals(
        self,
        active_users: int = 0,
        signups_today: int = 0,
        revenue_today: float = 0.0,
        conversion_pct: float = 0.0,
    ) -> None:
        """Update or insert the marketing vitals snapshot."""
        try:
            self._conn.execute(
                """INSERT INTO marketing_vitals
                       (id, active_users, signups_today, revenue_today,
                        conversion_pct, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       active_users   = excluded.active_users,
                       signups_today  = excluded.signups_today,
                       revenue_today  = excluded.revenue_today,
                       conversion_pct = excluded.conversion_pct,
                       updated_at     = excluded.updated_at
                """,
                (active_users, signups_today, revenue_today,
                 conversion_pct, _now_iso()),
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"StateStore: marketing_vitals write failed: {e}")

    def update_support_metrics(
        self,
        unread_count: int = 0,
        total_inbox: int = 0,
        recent_subjects: Optional[List[Dict[str, str]]] = None,
        source: str = "gmail",
    ) -> None:
        """Update or insert the support / email metrics snapshot."""
        subjects_json = json.dumps(recent_subjects or [])
        try:
            self._conn.execute(
                """INSERT INTO support_metrics
                       (id, unread_count, total_inbox, recent_subjects,
                        source, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       unread_count    = excluded.unread_count,
                       total_inbox     = excluded.total_inbox,
                       recent_subjects = excluded.recent_subjects,
                       source          = excluded.source,
                       updated_at      = excluded.updated_at
                """,
                (unread_count, total_inbox, subjects_json, source, _now_iso()),
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"StateStore: support_metrics write failed: {e}")

    def update_agent_pipeline(
        self,
        open_pr_count: int = 0,
        pr_details: Optional[List[Dict[str, str]]] = None,
        agent_status: str = "idle",
        repo: str = "",
    ) -> None:
        """Update or insert the agent pipeline / GitHub PR snapshot."""
        details_json = json.dumps(pr_details or [])
        try:
            self._conn.execute(
                """INSERT INTO agent_pipeline
                       (id, open_pr_count, pr_details, agent_status,
                        repo, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       open_pr_count = excluded.open_pr_count,
                       pr_details    = excluded.pr_details,
                       agent_status  = excluded.agent_status,
                       repo          = excluded.repo,
                       updated_at    = excluded.updated_at
                """,
                (open_pr_count, details_json, agent_status, repo, _now_iso()),
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"StateStore: agent_pipeline write failed: {e}")

    # ------------------------------------------------------------------
    # Readers
    # ------------------------------------------------------------------

    def _fetch_row(self, table: str) -> Dict[str, Any]:
        """Fetch the single snapshot row from a table as a dict."""
        try:
            cur = self._conn.execute(f"SELECT * FROM {table} WHERE id = 1")
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
            if row is None:
                return {}
            data = dict(zip(cols, row))
            # Parse any JSON text columns back into Python objects
            for key in ("recent_subjects", "pr_details"):
                if key in data and isinstance(data[key], str):
                    try:
                        data[key] = json.loads(data[key])
                    except (json.JSONDecodeError, TypeError):
                        pass
            return data
        except Exception as e:
            logger.error(f"StateStore: read {table} failed: {e}")
            return {}

    def get_marketing_vitals(self) -> Dict[str, Any]:
        return self._fetch_row("marketing_vitals")

    def get_support_metrics(self) -> Dict[str, Any]:
        return self._fetch_row("support_metrics")

    def get_agent_pipeline(self) -> Dict[str, Any]:
        return self._fetch_row("agent_pipeline")

    def get_integrated_context(self) -> Dict[str, Any]:
        """Compile the latest snapshot from every table into one payload.

        This is what gets injected into the LLM briefing prompt.
        Returns a dict like::

            {
                "timestamp": "...",
                "marketing": { ... },
                "support":   { ... },
                "pipeline":  { ... },
            }
        """
        return {
            "timestamp": _now_iso(),
            "marketing": self.get_marketing_vitals(),
            "support": self.get_support_metrics(),
            "pipeline": self.get_agent_pipeline(),
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
