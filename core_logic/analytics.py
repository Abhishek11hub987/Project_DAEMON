"""
Background Telemetry & Analytics Engine — Pillar C
=====================================================

Provides a continuous, non-blocking background heartbeat that:

1. **Polls system vitals** — CPU%, RAM%, disk%, GPU temp (via ``psutil``)
   every configurable interval (default: 2 seconds).
2. **Aggregates business metrics** — Simulated or real external data
   (user signups, revenue, API call counts) stored per-interval.
3. **Logs to SQLite** — All metrics are written to a local time-series
   database (``logs/daemon_metrics.db``) for historical queries and
   proactive voice readouts.
4. **Broadcasts to HUD** — Every heartbeat pushes a structured JSON
   packet via an event callback, designed to feed the WebSocket bus.

The engine is fully async and designed to run as an ``asyncio.Task``
alongside the FastAPI event loop. It exposes a simple ``start()`` /
``stop()`` interface and a ``get_snapshot()`` for instant readouts.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import random
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import psutil

from core_logic.config import Config

logger = logging.getLogger(__name__)

# =========================================================================
# Data models
# =========================================================================

@dataclass
class SystemVitals:
    """Snapshot of current system health."""
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    disk_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    cpu_temp_c: Optional[float] = None
    gpu_temp_c: Optional[float] = None
    cpu_freq_mhz: Optional[float] = None
    process_count: int = 0
    net_sent_mb: float = 0.0
    net_recv_mb: float = 0.0
    boot_time: Optional[str] = None
    uptime_hours: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu_percent": round(self.cpu_percent, 1),
            "ram_percent": round(self.ram_percent, 1),
            "ram_used_gb": round(self.ram_used_gb, 2),
            "ram_total_gb": round(self.ram_total_gb, 2),
            "disk_percent": round(self.disk_percent, 1),
            "disk_used_gb": round(self.disk_used_gb, 1),
            "disk_total_gb": round(self.disk_total_gb, 1),
            "cpu_temp_c": round(self.cpu_temp_c, 1) if self.cpu_temp_c else None,
            "gpu_temp_c": round(self.gpu_temp_c, 1) if self.gpu_temp_c else None,
            "cpu_freq_mhz": round(self.cpu_freq_mhz) if self.cpu_freq_mhz else None,
            "process_count": self.process_count,
            "net_sent_mb": round(self.net_sent_mb, 1),
            "net_recv_mb": round(self.net_recv_mb, 1),
            "boot_time": self.boot_time,
            "uptime_hours": round(self.uptime_hours, 1),
        }


@dataclass
class BusinessMetrics:
    """Simulated or real business KPIs for the HUD dashboard."""
    active_users: int = 0
    signups_today: int = 0
    api_calls_today: int = 0
    revenue_today: float = 0.0
    error_rate_pct: float = 0.0
    uptime_pct: float = 99.9

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_users": self.active_users,
            "signups_today": self.signups_today,
            "api_calls_today": self.api_calls_today,
            "revenue_today": round(self.revenue_today, 2),
            "error_rate_pct": round(self.error_rate_pct, 2),
            "uptime_pct": round(self.uptime_pct, 2),
        }


@dataclass
class TelemetryPacket:
    """Full telemetry snapshot broadcast to the HUD every heartbeat."""
    timestamp: str
    vitals: SystemVitals
    business: BusinessMetrics
    daemon_state: str = "idle"
    active_agent: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "telemetry",
            "timestamp": self.timestamp,
            "vitals": self.vitals.to_dict(),
            "business": self.business.to_dict(),
            "daemon_state": self.daemon_state,
            "active_agent": self.active_agent,
        }


# =========================================================================
# SQLite time-series store
# =========================================================================

_DB_PATH = Config.LOGS_DIR / "daemon_metrics.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    cpu_pct     REAL,
    ram_pct     REAL,
    ram_used_gb REAL,
    disk_pct    REAL,
    cpu_temp_c  REAL,
    net_sent_mb REAL,
    net_recv_mb REAL,
    process_cnt INTEGER,
    -- business
    active_users    INTEGER,
    signups_today   INTEGER,
    api_calls_today INTEGER,
    revenue_today   REAL,
    error_rate_pct  REAL
);

CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(timestamp);
"""


class MetricsStore:
    """Thin SQLite wrapper for persisting telemetry snapshots.

    Uses WAL mode for concurrent read/write without blocking.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._path = db_path or _DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_CREATE_SQL)
        self._conn.commit()
        logger.info(f"📊 MetricsStore ready — {self._path}")

    def insert(self, packet: TelemetryPacket) -> None:
        """Write one telemetry snapshot to the database."""
        v = packet.vitals
        b = packet.business
        try:
            self._conn.execute(
                """INSERT INTO metrics (
                    timestamp, cpu_pct, ram_pct, ram_used_gb, disk_pct,
                    cpu_temp_c, net_sent_mb, net_recv_mb, process_cnt,
                    active_users, signups_today, api_calls_today,
                    revenue_today, error_rate_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    packet.timestamp,
                    v.cpu_percent, v.ram_percent, v.ram_used_gb, v.disk_percent,
                    v.cpu_temp_c, v.net_sent_mb, v.net_recv_mb, v.process_count,
                    b.active_users, b.signups_today, b.api_calls_today,
                    b.revenue_today, b.error_rate_pct,
                ),
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"MetricsStore insert failed: {e}")

    def query_recent(self, minutes: int = 60) -> List[Dict[str, Any]]:
        """Return the last N minutes of metrics as a list of dicts."""
        try:
            cursor = self._conn.execute(
                """SELECT timestamp, cpu_pct, ram_pct, disk_pct,
                          net_sent_mb, net_recv_mb, active_users,
                          api_calls_today, revenue_today
                   FROM metrics
                   WHERE timestamp >= datetime('now', ?)
                   ORDER BY timestamp ASC""",
                (f"-{minutes} minutes",),
            )
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"MetricsStore query failed: {e}")
            return []

    def get_summary(self) -> Dict[str, Any]:
        """Return aggregate stats for the last hour."""
        try:
            row = self._conn.execute(
                """SELECT
                    COUNT(*) as samples,
                    AVG(cpu_pct) as avg_cpu,
                    MAX(cpu_pct) as peak_cpu,
                    AVG(ram_pct) as avg_ram,
                    MAX(ram_pct) as peak_ram,
                    AVG(disk_pct) as avg_disk
                 FROM metrics
                 WHERE timestamp >= datetime('now', '-60 minutes')"""
            ).fetchone()
            return {
                "samples": row[0],
                "avg_cpu": round(row[1] or 0, 1),
                "peak_cpu": round(row[2] or 0, 1),
                "avg_ram": round(row[3] or 0, 1),
                "peak_ram": round(row[4] or 0, 1),
                "avg_disk": round(row[5] or 0, 1),
            }
        except Exception as e:
            logger.error(f"MetricsStore summary failed: {e}")
            return {}

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# =========================================================================
# System vitals collector
# =========================================================================

# Cache the initial network counters so we can compute deltas.
_net_baseline = psutil.net_io_counters()


def collect_vitals() -> SystemVitals:
    """Collect a snapshot of system hardware vitals via psutil."""
    v = SystemVitals()

    # CPU
    v.cpu_percent = psutil.cpu_percent(interval=0)
    freq = psutil.cpu_freq()
    if freq:
        v.cpu_freq_mhz = freq.current

    # RAM
    mem = psutil.virtual_memory()
    v.ram_percent = mem.percent
    v.ram_used_gb = mem.used / (1024 ** 3)
    v.ram_total_gb = mem.total / (1024 ** 3)

    # Disk (root partition)
    try:
        if platform.system() == "Windows":
            disk = psutil.disk_usage("C:\\")
        else:
            disk = psutil.disk_usage("/")
        v.disk_percent = disk.percent
        v.disk_used_gb = disk.used / (1024 ** 3)
        v.disk_total_gb = disk.total / (1024 ** 3)
    except Exception:
        pass

    # Temperature (Linux only — Windows requires WMI, skip gracefully)
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for label in ("coretemp", "cpu_thermal", "k10temp", "acpitz"):
                if label in temps and temps[label]:
                    v.cpu_temp_c = temps[label][0].current
                    break
    except Exception:
        pass

    # Network I/O (cumulative since boot)
    try:
        net = psutil.net_io_counters()
        v.net_sent_mb = net.bytes_sent / (1024 ** 2)
        v.net_recv_mb = net.bytes_recv / (1024 ** 2)
    except Exception:
        pass

    # Process count + boot time
    v.process_count = len(psutil.pids())
    try:
        boot = psutil.boot_time()
        v.boot_time = datetime.fromtimestamp(boot).isoformat()
        v.uptime_hours = (time.time() - boot) / 3600
    except Exception:
        pass

    return v


def collect_business_metrics() -> BusinessMetrics:
    """Collect business metrics.

    Currently returns simulated data with realistic drift.
    Replace with real API calls (Stripe webhooks, analytics endpoints)
    when available — the schema stays the same.
    """
    hour = datetime.now().hour
    # Simulate realistic daily patterns
    base_users = max(10, int(50 * (1.0 + 0.5 * (-(abs(hour - 14) - 7) / 7))))
    return BusinessMetrics(
        active_users=base_users + random.randint(-5, 15),
        signups_today=random.randint(3, 45),
        api_calls_today=random.randint(200, 5000),
        revenue_today=round(random.uniform(0, 350.0), 2),
        error_rate_pct=round(random.uniform(0.01, 2.5), 2),
        uptime_pct=round(random.uniform(99.5, 100.0), 2),
    )


# =========================================================================
# Analytics Engine (async heartbeat)
# =========================================================================

class AnalyticsEngine:
    """Background telemetry engine running as an async task.

    Parameters
    ----------
    interval
        Seconds between heartbeats (default 2).
    event_callback
        Called with each ``TelemetryPacket.to_dict()`` for WebSocket broadcast.
    db_path
        Override the default SQLite path.
    persist_every
        Only write to SQLite every N heartbeats (reduces disk I/O).
        Default 15 → ~30s at 2s interval.
    """

    def __init__(
        self,
        interval: float = 2.0,
        event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        db_path: Optional[Path] = None,
        persist_every: int = 15,
    ) -> None:
        self._interval = interval
        self._emit = event_callback or (lambda e: None)
        self._persist_every = persist_every
        self._store = MetricsStore(db_path)
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._heartbeat_count = 0

        # State injection points — set externally by DAEMON / orchestrator
        self.daemon_state: str = "idle"
        self.active_agent: Optional[str] = None

        # Latest snapshot for instant access
        self._latest: Optional[TelemetryPacket] = None

        logger.info(
            f"📡 AnalyticsEngine created — interval: {interval}s, "
            f"persist every {persist_every} beats"
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Start the background heartbeat as an asyncio Task.

        Must be called from within a running event loop, or pass one
        explicitly.
        """
        if self._running:
            return

        self._running = True

        if loop:
            self._task = loop.create_task(self._heartbeat_loop())
        else:
            self._task = asyncio.ensure_future(self._heartbeat_loop())

        logger.info("📡 AnalyticsEngine started.")

    def stop(self) -> None:
        """Stop the heartbeat and close the database."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._store.close()
        logger.info("📡 AnalyticsEngine stopped.")

    # ------------------------------------------------------------------
    # Heartbeat loop
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Core async loop — runs until stopped."""
        logger.info("📡 Heartbeat loop running.")

        while self._running:
            try:
                # Collect on a thread to avoid blocking the event loop
                # (psutil calls can take a few ms)
                vitals = await asyncio.to_thread(collect_vitals)
                business = await asyncio.to_thread(collect_business_metrics)

                packet = TelemetryPacket(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    vitals=vitals,
                    business=business,
                    daemon_state=self.daemon_state,
                    active_agent=self.active_agent,
                )
                self._latest = packet
                self._heartbeat_count += 1

                # Broadcast to WebSocket clients
                self._emit(packet.to_dict())

                # Persist to SQLite periodically
                if self._heartbeat_count % self._persist_every == 0:
                    await asyncio.to_thread(self._store.insert, packet)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}", exc_info=True)

            await asyncio.sleep(self._interval)

    # ------------------------------------------------------------------
    # Public queries
    # ------------------------------------------------------------------

    def get_snapshot(self) -> Dict[str, Any]:
        """Return the latest telemetry packet as a dict.

        Used for instant voice readouts like "How's the system doing?"
        """
        if self._latest:
            return self._latest.to_dict()
        # If heartbeat hasn't run yet, collect a one-shot
        vitals = collect_vitals()
        return {
            "type": "telemetry",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "vitals": vitals.to_dict(),
            "business": BusinessMetrics().to_dict(),
            "daemon_state": self.daemon_state,
            "active_agent": self.active_agent,
        }

    def get_history(self, minutes: int = 60) -> List[Dict[str, Any]]:
        """Query historical metrics from SQLite."""
        return self._store.query_recent(minutes)

    def get_summary(self) -> Dict[str, Any]:
        """Aggregate stats for the last hour."""
        return self._store.get_summary()

    def format_voice_readout(self) -> str:
        """Format current metrics as a natural-language string for TTS.

        Returns a concise sentence suitable for DAEMON to speak aloud.
        """
        s = self.get_snapshot()
        v = s.get("vitals", {})
        cpu = v.get("cpu_percent", 0)
        ram = v.get("ram_percent", 0)
        disk = v.get("disk_percent", 0)

        # Determine health status
        if cpu > 90 or ram > 90:
            health = "System is under heavy load."
        elif cpu > 70 or ram > 70:
            health = "Moderate load — nothing critical."
        else:
            health = "All systems nominal."

        return (
            f"CPU at {cpu}%, RAM at {ram}%, disk at {disk}%. "
            f"{health}"
        )
