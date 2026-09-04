from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Lead:
    telegram_user_id: int
    telegram_username: str
    company: str
    name: str
    role: str
    need: str
    contact: str
    source: str = "direct"


@dataclass(frozen=True)
class StoredLead:
    id: int
    telegram_user_id: int
    telegram_username: str
    company: str
    name: str
    role: str
    need: str
    contact: str
    status: str
    created_at: str
    source: str


class Storage:
    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    user_id INTEGER PRIMARY KEY,
                    state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    telegram_username TEXT NOT NULL,
                    company TEXT NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    need TEXT NOT NULL,
                    contact TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS processed_updates (
                    update_id INTEGER PRIMARY KEY,
                    processed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(leads)").fetchall()
            }
            if "source" not in columns:
                db.execute(
                    "ALTER TABLE leads ADD COLUMN source TEXT NOT NULL DEFAULT 'direct'"
                )

    def get_session(self, user_id: int) -> tuple[str, dict[str, str]] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT state, payload_json FROM sessions WHERE user_id = ?", (user_id,)
            ).fetchone()
        if not row:
            return None
        return row["state"], json.loads(row["payload_json"])

    def set_session(self, user_id: int, state: str, payload: dict[str, str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO sessions(user_id, state, payload_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    state = excluded.state,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (user_id, state, json.dumps(payload, ensure_ascii=False), now),
            )

    def clear_session(self, user_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def save_lead(self, lead: Lead) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                INSERT INTO leads(
                    telegram_user_id, telegram_username, company, name,
                    role, need, contact, created_at, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead.telegram_user_id,
                    lead.telegram_username,
                    lead.company,
                    lead.name,
                    lead.role,
                    lead.need,
                    lead.contact,
                    datetime.now(timezone.utc).isoformat(),
                    lead.source,
                ),
            )
            return int(cursor.lastrowid)

    def list_leads(self, limit: int = 10) -> list[StoredLead]:
        safe_limit = max(1, min(50, limit))
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM leads ORDER BY id DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [StoredLead(**dict(row)) for row in rows]

    def get_lead(self, lead_id: int) -> StoredLead | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return StoredLead(**dict(row)) if row else None

    def set_lead_status(self, lead_id: int, status: str) -> bool:
        allowed = {"new", "contacted", "pilot", "closed", "rejected"}
        if status not in allowed:
            raise ValueError("Unsupported lead status")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE leads SET status = ? WHERE id = ?", (status, lead_id)
            )
        return cursor.rowcount == 1

    def lead_stats(self) -> dict[str, int]:
        stats = {status: 0 for status in ("new", "contacted", "pilot", "closed", "rejected")}
        with self._connect() as db:
            rows = db.execute(
                "SELECT status, COUNT(*) AS total FROM leads GROUP BY status"
            ).fetchall()
        for row in rows:
            stats[row["status"]] = int(row["total"])
        stats["total"] = sum(stats.values())
        return stats

    def source_stats(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT source, COUNT(*) AS total FROM leads GROUP BY source ORDER BY total DESC"
            ).fetchall()
        return {str(row["source"]): int(row["total"]) for row in rows}

    def is_update_processed(self, update_id: int) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM processed_updates WHERE update_id = ?", (update_id,)
            ).fetchone()
        return row is not None

    def mark_update_processed(self, update_id: int) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO processed_updates(update_id, processed_at) VALUES (?, ?)",
                (update_id, datetime.now(timezone.utc).isoformat()),
            )

    def get_update_offset(self) -> int:
        with self._connect() as db:
            row = db.execute(
                "SELECT value FROM runtime_state WHERE key = 'telegram_update_offset'"
            ).fetchone()
            if row:
                return max(0, int(row["value"]))
            row = db.execute(
                "SELECT COALESCE(MAX(update_id) + 1, 0) AS value FROM processed_updates"
            ).fetchone()
        return max(0, int(row["value"]))

    def set_update_offset(self, offset: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO runtime_state(key, value, updated_at)
                VALUES ('telegram_update_offset', ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (str(max(0, offset)), now),
            )
