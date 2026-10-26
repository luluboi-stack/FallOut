"""PV01 counts toward the M002 floor. The pick still comes from W004 RM01."""
import pandas as pd

from picklist_engine import COVERED, RED, TRIGGERED, StockBalances, consolidate, walk_sequences
from pv01_floor import decide_trigger

PART = "PARTA"
PSS = 50


def _walk(need, rm01=0.0, ps01=0.0, pv01=0.0, cogi=0.0, sto=0.0, w004=0.0):
    stock = StockBalances.from_maps(
        m002_rm01={PART: rm01},
        m002_ps01={PART: ps01},
        cogi={PART: cogi},
        sto={PART: sto},
        w004_rm01={PART: w004},
        m002_pv01={PART: pv01},
    )
    demand = pd.DataFrame([{
        "seq_index": 0, "line": "Line_1", "sequence_no": 1, "seq_key": "Line_1#1",
        "sku": "SKU1", "batch_count": 1, "minute_offset": 0,
        "part_id": PART, "part_key": PART, "description": "Part A", "need": float(need),
    }])
    rows = walk_sequences(demand, stock, lambda _part, _desc: (PSS, "FLC"), "snap-pv01")
    master = consolidate(rows).iloc[0]
    return rows.iloc[0], master


def test_pv01_covers_request_sends_no_trigger():
    decision = decide_trigger(request=25, pv01=25, w004=100, pss_qty=10)
    assert decision.trigger == 0
    assert decision.need_pick == 0
    assert decision.pss_count == 0

    row, master = _walk(need=25, pv01=25, w004=100)
    assert row["plant_taken"] == 25
    assert row["trigger"] == 0
    assert row["status"] == COVERED
    assert master["Need to be Picked"] == 0
    assert master["To be picked PSS count"] == 0


def test_partial_pv01_triggers_only_the_remainder_with_same_pss_rounding():
    # Request 80 rounds to 100 at PSS 50. Floor covers 60, so the trigger is the
    # remainder 20, which rounds to 50 — the same PSS step as any other trigger of 20.
    decision = decide_trigger(request=80, pv01=60, w004=1000, pss_qty=PSS)
    assert decision.floor_taken == 60
    assert decision.trigger == 20
    assert decision.need_pick == 50
    assert decision.pss_count == 1

    row, master = _walk(need=80, pv01=60, w004=1000)
    assert row["plant_taken"] == 60
    assert row["w004_taken"] == 20
    assert row["trigger"] == 20
    assert row["status"] == TRIGGERED
    assert master["Demand to Fulfil"] == 20
    assert master["Need to be Picked"] == 50
    assert master["To be picked PSS count"] == 1


def test_zero_pv01_matches_rm01_ps01_floor():
    # Old floor: max(0, 30 + 20 − 10) = 40, then STO 15, then W004 for the rest.
    # Request 80 − 40 − 15 = 25. PSS 50 rounds 25 to 50.
    decision = decide_trigger(
        request=80, rm01=30, ps01=20, pv01=0, cogi=10, open_sto=15, w004=1000, pss_qty=PSS,
    )
    assert decision.floor_opening == 40
    assert decision.floor_taken == 40
    assert decision.sto_taken == 15
    assert decision.trigger == 25
    assert decision.need_pick == 50

    drained = decide_trigger(
        request=80, rm01=30, ps01=20, pv01=0, cogi=10,
        prior_sequence_drain=40, open_sto=15, w004=1000, pss_qty=PSS,
    )
    assert drained.floor_remaining == 0
    assert drained.trigger == 65
    assert drained.need_pick == 100

    row, master = _walk(need=80, rm01=30, ps01=20, pv01=0, cogi=10, sto=15, w004=1000)
    assert row["plant_opening"] == 40
    assert row["trigger"] == 25
    assert master["Need to be Picked"] == 50


def test_w004_zero_does_not_pick_from_pv01():
    # Floor takes 60. The other 20 stays unmet. W004 is 0, so the trigger stays 0
    # and Need Pick stays 0 — the same zero-stock result as before PV01 was floor.
    decision = decide_trigger(request=80, pv01=60, w004=0, pss_qty=PSS)
    assert decision.floor_taken == 60
    assert decision.shortfall == 20
    assert decision.trigger == 0
    assert decision.need_pick == 0

    row, master = _walk(need=80, pv01=60, w004=0)
    assert row["plant_taken"] == 60
    assert row["w004_taken"] == 0
    assert row["trigger"] == 0
    assert row["shortfall"] == 20
    assert row["status"] == RED
    assert master["Need to be Picked"] == 0
    assert master["Transfer Qty (W004 ➡️ M002)"] == 0
    assert bool(master["Is_Zero_Stock"])
