import json
import logging
import os
import sqlite3
import threading
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sparx.db")
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    c = sqlite3.connect(_DB_PATH, timeout=10, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def init_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS prospects (
                prospect_key TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        c.commit()
    logger.info("SQLite DB ready at %s", _DB_PATH)


def upsert_prospect(profile: dict) -> None:
    key = profile.get("prospect_key")
    if not key:
        return
    with _lock:
        try:
            with _conn() as c:
                c.execute(
                    """
                    INSERT INTO prospects (prospect_key, data, updated_at)
                    VALUES (?, ?, datetime('now'))
                    ON CONFLICT(prospect_key) DO UPDATE SET
                        data = excluded.data,
                        updated_at = datetime('now')
                    """,
                    (key, json.dumps(profile)),
                )
                c.commit()
        except Exception as e:
            logger.warning("DB upsert failed for %r: %s", key, e)


def get_all_prospects() -> List[Dict]:
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT data FROM prospects ORDER BY updated_at DESC"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]
    except Exception as e:
        logger.warning("DB get_all failed: %s", e)
        return []


def get_prospect(key: str) -> Optional[Dict]:
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT data FROM prospects WHERE prospect_key = ?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else None
    except Exception as e:
        logger.warning("DB get_prospect failed for %r: %s", key, e)
        return None


def find_prospect_key(email: Optional[str] = None, name: Optional[str] = None,
                       company: Optional[str] = None) -> Optional[str]:
    """
    Best-effort lookup of a prospect_key by email (preferred) or name+company.
    Used to reconcile records fetched from external systems (e.g. Attio) that
    don't carry our prospect_key.
    """
    try:
        with _conn() as c:
            rows = c.execute("SELECT prospect_key, data FROM prospects").fetchall()
    except Exception as e:
        logger.warning("DB find_prospect_key failed: %s", e)
        return None

    email_norm = (email or "").strip().lower()
    name_norm = (name or "").strip().lower()
    company_norm = (company or "").strip().lower()

    if email_norm:
        for key, raw in rows:
            data = json.loads(raw)
            if (data.get("email") or "").strip().lower() == email_norm:
                return key

    if name_norm:
        for key, raw in rows:
            data = json.loads(raw)
            if (data.get("name") or "").strip().lower() == name_norm and (
                not company_norm or (data.get("company") or "").strip().lower() == company_norm
            ):
                return key

    return None


def update_prospect(key: str, updates: dict) -> Optional[Dict]:
    with _lock:
        try:
            with _conn() as c:
                row = c.execute(
                    "SELECT data FROM prospects WHERE prospect_key = ?", (key,)
                ).fetchone()
                if not row:
                    return None
                data = json.loads(row[0])
                data.update(updates)
                c.execute(
                    "UPDATE prospects SET data = ?, updated_at = datetime('now') WHERE prospect_key = ?",
                    (json.dumps(data), key),
                )
                c.commit()
                return data
        except Exception as e:
            logger.warning("DB update failed for %r: %s", key, e)
            return None
