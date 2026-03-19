"""
db.py — All SQLite logic isolated here.

Schema:
    trades(
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker        TEXT    NOT NULL,
        side          TEXT    NOT NULL,   -- 'YES'
        entry_price   INTEGER NOT NULL,   -- cents
        exit_price    INTEGER,            -- cents, NULL until closed
        contracts     INTEGER NOT NULL,
        pnl_cents     INTEGER,            -- NULL until closed
        entry_ts      TEXT    NOT NULL,   -- ISO-8601
        exit_ts       TEXT,               -- ISO-8601, NULL until closed
        entry_order_id TEXT,
        exit_order_id  TEXT
    )
"""

import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = "trades.db"

# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_db(db_path: str = DB_PATH) -> None:
    """Create the trades table if it does not exist."""
    with _connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker         TEXT    NOT NULL,
                side           TEXT    NOT NULL,
                entry_price    INTEGER NOT NULL,
                exit_price     INTEGER,
                contracts      INTEGER NOT NULL,
                pnl_cents      INTEGER,
                entry_ts       TEXT    NOT NULL,
                exit_ts        TEXT,
                entry_order_id TEXT,
                exit_order_id  TEXT
            )
        """)
        conn.commit()
    logger.info("Database initialised at %s", db_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def record_entry(
    ticker: str,
    entry_price: int,
    contracts: int,
    entry_order_id: Optional[str] = None,
    db_path: str = DB_PATH,
) -> int:
    """Insert a new open trade row and return its row id."""
    try:
        with _connect(db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades (ticker, side, entry_price, contracts, entry_ts, entry_order_id)
                VALUES (?, 'YES', ?, ?, ?, ?)
                """,
                (ticker, entry_price, contracts, _now_iso(), entry_order_id),
            )
            conn.commit()
            row_id = cursor.lastrowid
            logger.debug("Recorded entry for %s — row id %d", ticker, row_id)
            return row_id
    except Exception as exc:
        logger.error("db.record_entry error: %s", exc)
        return -1


def record_exit(
    row_id: int,
    exit_price: int,
    exit_order_id: Optional[str] = None,
    db_path: str = DB_PATH,
) -> None:
    """Update an existing trade row with exit details and P&L."""
    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT entry_price, contracts FROM trades WHERE id = ?", (row_id,)
            ).fetchone()
            if not row:
                logger.warning("record_exit: row id %d not found", row_id)
                return

            entry_price = row["entry_price"]
            contracts = row["contracts"]
            # P&L per contract = (exit_price - entry_price) cents
            pnl_cents = (exit_price - entry_price) * contracts

            conn.execute(
                """
                UPDATE trades
                SET exit_price = ?, exit_ts = ?, pnl_cents = ?, exit_order_id = ?
                WHERE id = ?
                """,
                (exit_price, _now_iso(), pnl_cents, exit_order_id, row_id),
            )
            conn.commit()
            logger.debug(
                "Recorded exit for row %d — exit %d¢, P&L %+d¢",
                row_id, exit_price, pnl_cents
            )
    except Exception as exc:
        logger.error("db.record_exit error: %s", exc)


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

def get_open_trades(db_path: str = DB_PATH) -> list[dict]:
    """Return all trades that have no exit price yet."""
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE exit_price IS NULL ORDER BY entry_ts"
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("db.get_open_trades error: %s", exc)
        return []


def get_all_trades(db_path: str = DB_PATH) -> list[dict]:
    """Return every trade row ordered by entry time."""
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY entry_ts"
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("db.get_all_trades error: %s", exc)
        return []


def get_pnl_summary(db_path: str = DB_PATH) -> dict:
    """
    Return a summary dict:
        total_trades, closed_trades, open_trades,
        total_pnl_cents, wins, losses
    """
    try:
        with _connect(db_path) as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*)                                   AS total_trades,
                    SUM(CASE WHEN exit_price IS NOT NULL THEN 1 ELSE 0 END) AS closed_trades,
                    SUM(CASE WHEN exit_price IS NULL     THEN 1 ELSE 0 END) AS open_trades,
                    COALESCE(SUM(pnl_cents), 0)                AS total_pnl_cents,
                    SUM(CASE WHEN pnl_cents > 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN pnl_cents < 0 THEN 1 ELSE 0 END) AS losses
                FROM trades
            """).fetchone()
            return dict(row) if row else {}
    except Exception as exc:
        logger.error("db.get_pnl_summary error: %s", exc)
        return {}


def has_open_position(ticker: str, db_path: str = DB_PATH) -> bool:
    """Return True if there is an unclosed trade for this ticker."""
    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT id FROM trades WHERE ticker = ? AND exit_price IS NULL LIMIT 1",
                (ticker,),
            ).fetchone()
            return row is not None
    except Exception as exc:
        logger.error("db.has_open_position error: %s", exc)
        return False
