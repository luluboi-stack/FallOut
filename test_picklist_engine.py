import pandas as pd
import pytest

from picklist_engine import (
    COLOUR_NONE,
    supplement_bom,
    COLOUR_RED,
    COLOUR_YELLOW,
    COVERED,
    PARTIAL,
    RED,
    STATUS_OUT_OF_STOCK,
    STATUS_PARTIAL,
    STATUS_READY,
    STATUS_SUFFICIENT,
    TRIGGERED,
    StockBalances,
    audit_decision,
    colour_for,
    consolidate,
    order_plan_sequences,
    pick_quantity,
    run_walk,
    walk_sequences,
)

PART = "PARTA"


def fixed_pss(size=50, ptype="FLC"):
    return lambda part_id, desc: (size, ptype)


def demand_frame(needs, part=PART, line="Line_1", start_seq=1):
    rows = []
    for i, need in enumerate(needs):
        seq = start_seq + i
        rows.append({
            "seq_index": i, "line": line, "sequence_no": seq, "seq_key": f"{line}#{seq}",
            "sku": "SKU1", "batch_count": 1, "minute_offset": i * 30,
            "part_id": part, "part_key": part, "description": "Part A", "need": float(need),
        })
    return pd.DataFrame(rows)


def balances(plant=0.0, sto=0.0, w004=0.0, cogi=0.0, part=PART):
    return StockBalances.from_maps(
        m002_rm01={part: plant}, m002_ps01={}, cogi={part: cogi}, sto={part: sto}, w004_rm01={part: w004},
    )


def walk(needs, **stock):
    return walk_sequences(demand_frame(needs), balances(**stock), fixed_pss(), "snap-1")


def test_abc_triggers_0_20_30():
    rows = walk([30, 30, 30], plant=40, w004=1000)
    assert rows["trigger"].tolist() == [0, 20, 30]
    assert rows["status"].tolist() == [COVERED, TRIGGERED, TRIGGERED]


def test_sequence_1_to_10_runs_out_at_200():
    rows = walk([30] * 10, plant=40, w004=200)
    triggers = rows.set_index("sequence_no")["trigger"]
    assert triggers.sum() == 200
    assert triggers[2] == 20
    assert triggers.loc[3:8].sum() == 180
    assert triggers.loc[9:10].tolist() == [0, 0]
    assert rows.set_index("sequence_no").loc[9:10, "status"].tolist() == [RED, RED]


@pytest.mark.parametrize("n_seqs,boxes", [(1, 1), (2, 2), (3, 2), (4, 3), (5, 3)])
def test_trigger_30_pss_50_rounding(n_seqs, boxes):
    rows = walk([30] * 5, w004=1000)
    keys = [f"Line_1#{s}" for s in range(1, n_seqs + 1)]
    master = consolidate(rows, keys)
    row = master.iloc[0]
    assert row["Demand to Fulfil"] == 30 * n_seqs
    assert row["To be picked PSS count"] == boxes
    assert row["Need to be Picked"] == boxes * 50
    assert row["Transfer Qty (W004 ➡️ M002)"] == boxes * 50
    assert row["Transfer Status"] == STATUS_READY


def test_cogi_floors_plant_at_zero():
    rows = walk([30], plant=10, cogi=50, w004=1000)
    assert rows.iloc[0]["plant_opening"] == 0
    assert rows.iloc[0]["plant_taken"] == 0
    assert rows.iloc[0]["trigger"] == 30


def test_sto_is_used_before_w004():
    rows = walk([30, 30], plant=10, sto=30, w004=1000)
    assert rows["plant_taken"].tolist() == [10, 0]
    assert rows["sto_taken"].tolist() == [20, 10]
    assert rows["trigger"].tolist() == [0, 20]
    assert rows["status"].tolist() == [COVERED, TRIGGERED]


def test_opening_stock_columns_are_distinct_and_walk_uses_combined_plant():
    stock = StockBalances.from_maps(
        m002_rm01={PART: 11}, m002_ps01={PART: 7}, cogi={PART: 3}, sto={PART: 5},
        w004_rm01={PART: 100}, m002_pv01={PART: 13},
    )
    rows = walk_sequences(demand_frame([30, 30]), stock, fixed_pss(), "snap-1")
    expected = {"m002_rm01": 11, "m002_ps01": 7, "m002_pv01": 13, "w004_rm01": 100, "sto": 5, "cogi": 3}
    for _, row in rows.iterrows():
        assert {k: row[k] for k in expected} == expected
    first, second = rows.iloc[0], rows.iloc[1]
    assert first["plant_opening"] == 15
    assert (first["plant_taken"], first["sto_taken"], first["trigger"]) == (15, 5, 10)
    assert (second["plant_taken"], second["sto_taken"], second["trigger"]) == (0, 0, 30)
    assert first["w004_taken"] == first["trigger"]


def test_yellow_mid_sequence():
    rows = walk([30, 30, 30], w004=40)
    assert rows["trigger"].tolist() == [30, 10, 0]
    assert rows["status"].tolist() == [TRIGGERED, PARTIAL, RED]

    master = consolidate(rows)
    row = master.iloc[0]
    assert row["Transfer Status"] == STATUS_PARTIAL
    assert row["Demand to Fulfil"] == 40
    assert row["Need to be Picked"] == 50


def test_red_only_when_every_needing_row_is_red():
    rows = walk([30] * 10, plant=40, w004=200)
    red_only = consolidate(rows, ["Line_1#9", "Line_1#10"]).iloc[0]
    assert red_only["Transfer Status"] == STATUS_OUT_OF_STOCK
    assert red_only["Need to be Picked"] == 0
    assert bool(red_only["Is_Zero_Stock"])

    mixed = consolidate(rows, ["Line_1#8", "Line_1#9"]).iloc[0]
    assert mixed["Transfer Status"] == STATUS_PARTIAL
    assert mixed["Need to be Picked"] == 50


def test_covered_selection_is_sufficient():
    rows = walk([30, 30, 30], plant=40, w004=1000)
    row = consolidate(rows, ["Line_1#1"]).iloc[0]
    assert row["Transfer Status"] == STATUS_SUFFICIENT
    assert row["Need to be Picked"] == 0
    assert row["To be picked PSS count"] == 0


def test_filter_reads_stored_rows_without_rewalking():
    rows = walk([30] * 5, w004=1000)
    before = rows.copy()
    a = consolidate(rows, ["Line_1#1"])
    b = consolidate(rows, None)
    pd.testing.assert_frame_equal(rows, before)
    assert a.iloc[0]["Need to be Picked"] == 50
    assert b.iloc[0]["Need to be Picked"] == 150


def test_empty_selection_returns_no_rows():
    rows = walk([30], w004=1000)
    assert consolidate(rows, set()).empty


def test_colour_rules():
    assert colour_for([COVERED, TRIGGERED], 30) == COLOUR_NONE
    assert colour_for([TRIGGERED, RED], 30) == COLOUR_YELLOW
    assert colour_for([PARTIAL], 10) == COLOUR_YELLOW
    assert colour_for([COVERED, RED], 0) == COLOUR_RED
    assert colour_for([COVERED], 0) == COLOUR_NONE


def test_pick_quantity_edges():
    assert pick_quantity(0, 50) == (0, 0)
    assert pick_quantity(50, 50) == (50, 1)
    assert pick_quantity(50.0000000001, 50) == (50, 1)
    assert pick_quantity(51, 50) == (100, 2)


def test_starvation_and_priority_from_earliest_selected_sequence():
    rows = walk([30] * 6, w004=1000)
    row = consolidate(rows, ["Line_1#3", "Line_1#5"]).iloc[0]
    assert row["Sequence_Order"] == 3
    assert row["Starvation_Mins"] == 0
    assert row["Sequences_Covered"] == "Seqs #3 - #5"


def test_audit_decision_codes():
    assert audit_decision(STATUS_READY, 0)[1] == "ACCEPTED_DEFICIT_FULFILLED"
    assert audit_decision(STATUS_OUT_OF_STOCK, 0)[1] == "REJECTED_W004_STOCKOUT"
    assert audit_decision(STATUS_SUFFICIENT, 5)[1] == "REJECTED_COVERED_BY_STO"
    assert audit_decision(STATUS_SUFFICIENT, 0)[1] == "REJECTED_SUFFICIENT_M002"


def batches(rows):
    df = pd.DataFrame(rows, columns=["SKUName", "Status", "Line", "Count"])
    df["ScaleX_Sequence_No"] = df[df["Status"] == "PLAN"].groupby("Line", sort=False).cumcount() + 1
    df["ScaleX_Sequence_No"] = df["ScaleX_Sequence_No"].fillna(0).astype(int)
    return df


BOM = pd.DataFrame({
    "Parent Material": ["SKU1", "SKU1", "SUB1", "SKU2", "HU1"],
    "Child Material": ["SUB1", "HU1", "PARTA", "PARTA", "PARTZ"],
    "Comp Qty.": ["2", "1", "1", "1", "5"],
    "Description": ["Sub", "Handled unit", "Part A", "Part A", "Inner"],
})


def test_run_walk_end_to_end_lines_share_balance_and_hold_is_skipped():
    plan = batches([
        ("SKU1", "HOLD", "Line_1", 10),
        ("SKU1", "PLAN", "Line_1", 10),
        ("SKU2", "PLAN", "Line_2", 10),
        ("SKU2", "PLAN", "Line_1", 5),
    ])
    stock = StockBalances.from_maps(
        m002_rm01={"PARTA": 20}, m002_ps01={}, cogi={}, sto={}, w004_rm01={"PARTA": 100, "HU1": 0},
    )
    rows = run_walk(plan, BOM, stock, handled_units={"HU1"}, pss_lookup=fixed_pss(), snapshot_id="snap-x")

    assert set(rows["part_key"]) == {"PARTA", "HU1"}
    parta = rows[rows["part_key"] == "PARTA"].sort_values("walk_order")
    assert parta["seq_key"].tolist() == ["Line_1#1", "Line_2#1", "Line_1#2"]
    assert parta["need"].tolist() == [20, 10, 5]
    assert parta["trigger"].tolist() == [0, 10, 5]
    hu = rows[rows["part_key"] == "HU1"].iloc[0]
    assert hu["status"] == RED
    assert (rows["snapshot_id"] == "snap-x").all()


def _bom_row(parent, child, desc, item="10", qty=1):
    return {
        "Parent Material": parent, "Child Material": child, "Description": desc,
        "BOM Item Number": item, "Comp Qty.": qty, "Alt. BOM": "01",
        "valid From": "2026-01-01", "Valid To": "9999-12-31",
    }


def test_supplement_bom_adds_missing_part_and_newer_revision():
    old = pd.DataFrame([_bom_row("SEL1", "BW00001486FBB", "FRONT SHROUD AND BEZEL ASSEMBLY - Black")] * 8)
    old = pd.concat([old, pd.DataFrame([_bom_row("SEL1", "KEEP1", "Kept part")] * 8)], ignore_index=True)
    reference = pd.DataFrame([
        _bom_row("SEL1", "BW00001486FBC", "FRONT SHROUD AND BEZEL ASSEMBLY - Black"),
        _bom_row("SEL1", "NEW1", "Only on 16 Oct"),
        _bom_row("SEL1", "KEEP1", "Kept part"),
    ])
    out = supplement_bom(old, reference)
    children = set(out["Child Material"])
    assert "BW00001486FBC" in children
    assert "BW00001486FBB" not in children
    assert "NEW1" in children
    assert "KEEP1" in children
    assert (out["Child Material"] == "KEEP1").sum() == 1


def test_supplement_bom_leaves_a_normal_file_unchanged():
    current = pd.DataFrame([_bom_row("SEL1", "PARTA", "Part")])
    reference = pd.DataFrame([_bom_row("SEL1", "PARTB", "Other")])
    out = supplement_bom(current, reference)
    assert out["Child Material"].tolist() == ["PARTA"]


def test_order_plan_sequences_offsets_per_line():
    plan = order_plan_sequences(batches([
        ("A", "PLAN", "Line_1", 10),
        ("B", "PLAN", "Line_1", 20),
        ("C", "PLAN", "Line_2", 15),
    ]))
    assert plan["seq_key"].tolist() == ["Line_1#1", "Line_2#1", "Line_1#2"]
    assert plan["minute_offset"].tolist() == [0, 0, 10]
