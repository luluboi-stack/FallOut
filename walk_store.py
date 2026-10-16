"""SQLite log for sequence walks: one picklist_walk_run header plus every picklist_walk row.

A walk is keyed by (snapshot_id, batch_fingerprint). The same key always maps to the same
walk_run_id, so refreshes and filter changes never create a second log or audit.
"""
import sqlite3
from typing import Optional, Tuple

import pandas as pd

from picklist_engine import OPENING_STOCK_COLUMNS, WALK_COLUMNS

WALK_PREVIEW = "preview"
WALK_REUSE = "reuse"
WALK_NEW = "new"

_TEXT_COLUMNS = ["line", "seq_key", "sku", "part_id", "part_key", "description", "status",
                 "pss_type", "open_po_numbers", "snapshot_id"]


def init_walk_tables(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS picklist_walk_run (
            walk_run_id TEXT PRIMARY KEY,
            snapshot_id TEXT,
            batch_fingerprint TEXT,
            source TEXT,
            timestamp DATETIME,
            client_ip TEXT,
            sequence_count INTEGER,
            material_count INTEGER,
            total_trigger REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS picklist_walk (
            walk_run_id TEXT,
            walk_order INTEGER,
            line TEXT,
            sequence_no INTEGER,
            seq_key TEXT,
            sku TEXT,
            batch_count INTEGER,
            minute_offset INTEGER,
            part_id TEXT,
            part_key TEXT,
            description TEXT,
            need REAL,
            plant_taken REAL,
            sto_taken REAL,
            w004_taken REAL,
            trigger REAL,
            shortfall REAL,
            status TEXT,
            plant_after REAL,
            sto_after REAL,
            w004_after REAL,
            pss_qty INTEGER,
            pss_type TEXT,
            m002_stock REAL,
            cogi_qty REAL,
            plant_opening REAL,
            sto_opening REAL,
            w004_opening REAL,
            open_po_numbers TEXT,
            snapshot_id TEXT,
            m002_rm01 REAL,
            m002_ps01 REAL,
            m002_pv01 REAL,
            w004_rm01 REAL,
            sto REAL,
            cogi REAL
        )
    """)
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(picklist_walk)")}
    for col in OPENING_STOCK_COLUMNS:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE picklist_walk ADD COLUMN {col} REAL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_picklist_walk_run ON picklist_walk (walk_run_id)")
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_picklist_walk_run_key "
            "ON picklist_walk_run (snapshot_id, batch_fingerprint)"
        )
    except sqlite3.IntegrityError:
        # Older databases may already hold duplicate keys; lookups still return the first run.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_picklist_walk_run_key "
            "ON picklist_walk_run (snapshot_id, batch_fingerprint)"
        )
    conn.commit()


def walk_action(snapshot_complete: bool, existing_walk_run_id: Optional[str]) -> str:
    """preview: not every SAP dump is loaded, log nothing. reuse: this picklist is already logged. new: log it once."""
    if not snapshot_complete:
        return WALK_PREVIEW
    if existing_walk_run_id:
        return WALK_REUSE
    return WALK_NEW


def find_walk_run(conn: sqlite3.Connection, snapshot_id: str, batch_fingerprint: str) -> Optional[str]:
    row = conn.execute(
        "SELECT walk_run_id FROM picklist_walk_run WHERE snapshot_id = ? AND batch_fingerprint = ? "
        "ORDER BY timestamp ASC, walk_run_id ASC LIMIT 1",
        (snapshot_id, batch_fingerprint),
    ).fetchone()
    return row[0] if row else None


def load_walk_rows(conn: sqlite3.Connection, walk_run_id: str) -> pd.DataFrame:
    df = pd.read_sql(
        f"SELECT {', '.join(WALK_COLUMNS)} FROM picklist_walk WHERE walk_run_id = ? ORDER BY walk_order",
        conn, params=(walk_run_id,),
    )
    for col in _TEXT_COLUMNS:
        df[col] = df[col].fillna("").astype(str)
    return df


def claim_walk_run(
    conn: sqlite3.Connection, walk_run_id: str, walk_df: pd.DataFrame, header: dict
) -> Tuple[str, bool]:
    """Logs the walk under walk_run_id unless its key is already logged.

    Returns (walk_run_id to use, created). created is False when another run, refresh, or
    client already logged the same snapshot and plan; the caller must then skip the audit.
    """
    existing = find_walk_run(conn, header["snapshot_id"], header["batch_fingerprint"])
    if existing:
        return existing, False
    cur = conn.execute("""
        INSERT OR IGNORE INTO picklist_walk_run
        (walk_run_id, snapshot_id, batch_fingerprint, source, timestamp, client_ip,
         sequence_count, material_count, total_trigger)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        walk_run_id, header["snapshot_id"], header["batch_fingerprint"], header["source"],
        header["timestamp"], header["client_ip"], int(header["sequence_count"]),
        int(header["material_count"]), float(header["total_trigger"]),
    ))
    if cur.rowcount != 1:
        conn.commit()
        return find_walk_run(conn, header["snapshot_id"], header["batch_fingerprint"]) or walk_run_id, False
    rows = walk_df[WALK_COLUMNS].copy()
    rows.insert(0, "walk_run_id", walk_run_id)
    if not rows.empty:
        rows.to_sql("picklist_walk", conn, if_exists="append", index=False)
    conn.commit()
    return walk_run_id, True
