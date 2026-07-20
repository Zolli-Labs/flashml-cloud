"""Node registry persistence (SQLite).

Nodes are disposable; state is not. Registrations and heartbeats land here
so the dashboard can show online/offline and heartbeat-loss timelines even
after an agent dies. Jobs/events stay in FlashRuntime's ledger — the cloud
API proxies them rather than duplicating state at POC scale.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flashruntime.protocol.v1alpha1 import (
    NodeHeartbeat,
    NodeRegistration,
    NodeStatusView,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    registration TEXT NOT NULL,
    last_heartbeat TEXT,
    last_status TEXT NOT NULL DEFAULT 'online',
    accepted_task_count INTEGER NOT NULL DEFAULT 0,
    registered_at TEXT NOT NULL
);
"""


class NodeStore:
    def __init__(self, path: str | Path, offline_after_seconds: float = 30.0):
        self._path = str(path)
        self._lock = threading.Lock()
        self.offline_after = timedelta(seconds=offline_after_seconds)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def register(self, registration: NodeRegistration) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO nodes (node_id, registration, last_heartbeat, registered_at) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(node_id) DO UPDATE SET registration=excluded.registration, "
                "last_heartbeat=excluded.last_heartbeat, last_status='online'",
                (registration.node_id, registration.model_dump_json(), now, now),
            )

    def heartbeat(self, hb: NodeHeartbeat) -> bool:
        """Record a heartbeat; False if the node was never registered."""
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "UPDATE nodes SET last_heartbeat=?, last_status=? WHERE node_id=?",
                (hb.timestamp.isoformat(), hb.status, hb.node_id),
            )
            return cur.rowcount > 0

    def add_accepted_tasks(self, node_id: str, count: int) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE nodes SET accepted_task_count = accepted_task_count + ? "
                "WHERE node_id=?",
                (count, node_id),
            )

    def list_nodes(self) -> list[NodeStatusView]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT * FROM nodes ORDER BY registered_at").fetchall()
        now = datetime.now(timezone.utc)
        views = []
        for row in rows:
            last = (
                datetime.fromisoformat(row["last_heartbeat"])
                if row["last_heartbeat"] else None
            )
            online = (
                row["last_status"] == "online"
                and last is not None
                and now - last < self.offline_after
            )
            views.append(NodeStatusView(
                registration=NodeRegistration.model_validate_json(row["registration"]),
                online=online,
                last_heartbeat=last,
                accepted_task_count=row["accepted_task_count"],
            ))
        return views
