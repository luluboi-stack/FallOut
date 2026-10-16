import sqlite3

import pandas as pd
import pytest

from data_sources import SapUploadSource
from picklist_engine import StockBalances, walk_sequences
from walk_store import (
    WALK_NEW,
    WALK_PREVIEW,
    WALK_REUSE,
    claim_walk_run,
    find_walk_run,
    init_walk_tables,
    load_walk_rows,
    walk_action,
)

MB52 = pd.DataFrame({"Material": ["A"], "Plant": ["M002"], "Storage Location": ["RM01"], "Unrestricted": ["10"]})
COGI = pd.DataFrame({"Material": ["A"], "Plant": ["M002"], "Quantity": ["1"]})
STO = pd.DataFrame({"Material": ["A"], "Quantity": ["5"]})
EMPTY = pd.DataFrame()


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "walk.db")
    init_walk_tables(c)
    yield c
    c.close()


def sample_walk(snapshot_id="snap"):
    demand = pd.DataFrame([{
        "seq_index": i, "line": "Line_1", "sequence_no": i + 1, "seq_key": f"Line_1#{i + 1}",
        "sku": "SKU1", "batch_count": 1, "minute_offset": i * 30,
        "part_id": "A", "part_key": "A", "description": "Part A", "need": 30.0,
    } for i in range(3)])
    stock = StockBalances.from_maps({"A": 40}, {}, {}, {}, {"A": 100})
    return walk_sequences(demand, stock, lambda p, d: (50, "FLC"), snapshot_id)


def header(snapshot_id="snap", fingerprint="plan1"):
    return {"snapshot_id": snapshot_id, "batch_fingerprint": fingerprint, "source": "SAP upload",
            "timestamp": "2026-10-16 09:00:00", "client_ip": "127.0.0.1",
            "sequence_count": 3, "material_count": 1, "total_trigger": 50.0}


def decide(conn, snapshot, fingerprint="plan1"):
    existing = find_walk_run(conn, snapshot.snapshot_id, fingerprint) if snapshot.is_complete else None
    return walk_action(snapshot.is_complete, existing), existing


@pytest.mark.parametrize("mb52,cogi,sto", [
    (EMPTY, EMPTY, EMPTY),
    (MB52, EMPTY, EMPTY),
    (MB52, COGI, EMPTY),
    (EMPTY, COGI, STO),
])
def test_incomplete_dumps_are_preview_only(conn, mb52, cogi, sto):
    action, _ = decide(conn, SapUploadSource(mb52, cogi, sto).load())
    assert action == WALK_PREVIEW


def test_third_dump_writes_once_then_refresh_reuses(conn):
    snap = SapUploadSource(MB52, COGI, STO).load()
    assert decide(conn, snap) == (WALK_NEW, None)

    run_id, created = claim_walk_run(conn, "walk_1", sample_walk(snap.snapshot_id), header(snap.snapshot_id))
    assert (run_id, created) == ("walk_1", True)

    refresh = SapUploadSource(MB52.copy(), COGI.copy(), STO.copy()).load()
    assert decide(conn, refresh) == (WALK_REUSE, "walk_1")
    assert conn.execute("SELECT COUNT(*) FROM picklist_walk_run").fetchone()[0] == 1


def test_second_claim_for_same_key_does_not_create(conn):
    walk = sample_walk()
    assert claim_walk_run(conn, "walk_1", walk, header()) == ("walk_1", True)
    assert claim_walk_run(conn, "walk_2", walk, header()) == ("walk_1", False)
    assert conn.execute("SELECT COUNT(*) FROM picklist_walk").fetchone()[0] == len(walk)


def test_replaced_dump_or_plan_change_is_a_new_picklist(conn):
    claim_walk_run(conn, "walk_1", sample_walk(), header())
    new_cogi = SapUploadSource(MB52, COGI.assign(Quantity=["2"]), STO).load()
    assert decide(conn, new_cogi)[0] == WALK_NEW
    assert claim_walk_run(conn, "walk_2", sample_walk(), header(fingerprint="plan2")) == ("walk_2", True)
    ids = [r[0] for r in conn.execute("SELECT walk_run_id FROM picklist_walk_run ORDER BY walk_run_id")]
    assert ids == ["walk_1", "walk_2"]


def test_reused_walk_rows_round_trip(conn):
    walk = sample_walk()
    claim_walk_run(conn, "walk_1", walk, header())
    loaded = load_walk_rows(conn, "walk_1")
    assert loaded["seq_key"].tolist() == walk["seq_key"].tolist()
    assert loaded["trigger"].tolist() == walk["trigger"].tolist()
    assert loaded["status"].tolist() == walk["status"].tolist()
    assert (loaded["open_po_numbers"] == "").all()
