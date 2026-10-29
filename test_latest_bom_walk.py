"""Walk the checked-in plan against the newest exploded BOM.

Unit cases for the allotment rules stay in test_picklist_engine.py.
This file is the regression: real parents, both lines, HOLD left out,
and a 180-unit window that does not drop later sequences from the walk.
"""

import math
import re
from pathlib import Path

import pandas as pd
import pytest

from picklist_engine import StockBalances, clean_sap_key, consolidate, run_walk

ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "plan_batches.csv"
WINDOW_UNITS = 180
BOX = 60


def latest_exploded_bom() -> Path:
    files = list(ROOT.glob("BOM_FINAL_EXPLODED*.parquet"))
    if not files:
        pytest.fail("No BOM_FINAL_EXPLODED parquet in the repo")

    def day(path: Path) -> int:
        match = re.search(r"\((\d+)", path.name)
        return int(match.group(1)) if match else -1

    return max(files, key=day)


def load_plan() -> pd.DataFrame:
    plan = pd.read_csv(PLAN_PATH)
    plan["Status"] = plan["Status"].astype(str).str.strip().str.upper()
    plan["ScaleX_Sequence_No"] = (
        plan[plan["Status"] == "PLAN"].groupby("Line", sort=False).cumcount() + 1
    )
    plan["ScaleX_Sequence_No"] = plan["ScaleX_Sequence_No"].fillna(0).astype(int)
    return plan


def window_keys(plan: pd.DataFrame, target: int = WINDOW_UNITS) -> set:
    """Same cutoff as the screen: per line, from sequence 1, until Count sums to the target."""
    keys = set()
    planned = plan[plan["Status"] == "PLAN"]
    for line in ("Line_1", "Line_2"):
        rows = planned[planned["Line"].str.lower() == line.lower()].sort_values("ScaleX_Sequence_No")
        total = 0
        for _, batch in rows.iterrows():
            seq_no = int(batch["ScaleX_Sequence_No"])
            keys.add(f"{line}#{seq_no}")
            total += int(batch["Count"])
            if total >= target:
                break
    return keys


def test_latest_exploded_bom_is_the_16_oct_file():
    path = latest_exploded_bom()
    assert path.name == "BOM_FINAL_EXPLODED (16th Oct).parquet"
    older = ROOT / "BOM_FINAL_EXPLODED (15th Oct).parquet"
    assert not pd.read_parquet(path).equals(pd.read_parquet(older))


def test_plan_walks_every_plan_sequence_on_the_latest_bom():
    bom = pd.read_parquet(latest_exploded_bom())
    plan = load_plan()
    parents = set(bom["Parent Material"].map(clean_sap_key))
    planned = plan[plan["Status"] == "PLAN"]
    assert set(planned["SKUName"].map(clean_sap_key)) <= parents

    child_counts = (
        bom.assign(parent=bom["Parent Material"].map(clean_sap_key), child=bom["Child Material"].map(clean_sap_key))
        .groupby("parent")["child"]
        .nunique()
    )
    expected_rows = sum(int(child_counts.get(clean_sap_key(sku), 0)) for sku in planned["SKUName"])

    stock = StockBalances.from_maps({}, {}, {}, {}, {})
    walk = run_walk(
        plan, bom, stock, handled_units=set(), pss_lookup=lambda _part, _desc: (BOX, "FLC"), snapshot_id="16oct",
    )

    plan_keys = {f"{row.Line}#{int(row.ScaleX_Sequence_No)}" for row in planned.itertuples(index=False)}
    assert set(walk["seq_key"]) == plan_keys
    assert set(walk["line"]) == {"Line_1", "Line_2"}
    assert len(walk) == expected_rows
    assert (walk["status"] == "RED").all()
    assert walk["trigger"].sum() == 0
    assert (plan["Status"] == "HOLD").any()
    assert len(plan_keys) == len(planned)
    assert 0 not in set(walk["sequence_no"])


def test_window_keeps_later_sequences_and_both_lines_in_the_quantity():
    bom = pd.read_parquet(latest_exploded_bom())
    plan = load_plan()
    parts = sorted(bom["Child Material"].map(clean_sap_key).unique())
    stock = StockBalances.from_maps({}, {}, {}, {}, {part: 1_000_000_000 for part in parts})
    walk = run_walk(
        plan, bom, stock, handled_units=set(), pss_lookup=lambda _part, _desc: (BOX, "FLC"), snapshot_id="16oct-w004",
    )
    stored = walk.copy()
    keys = window_keys(plan)
    visible = consolidate(walk, keys)
    pd.testing.assert_frame_equal(walk, stored)

    later = set(walk["seq_key"]) - keys
    assert later
    assert later <= set(walk["seq_key"])

    window_rows = walk[walk["seq_key"].isin(keys)]
    on_both = window_rows.groupby("part_key")["line"].nunique()
    shared = on_both[on_both == 2].index[0]
    shown = visible.set_index(visible["Part ID"].map(clean_sap_key)).loc[shared]
    line_1 = consolidate(window_rows[window_rows["line"] == "Line_1"], None)
    line_1.index = line_1["Part ID"].map(clean_sap_key)

    need = float(window_rows.loc[window_rows["part_key"] == shared, "need"].sum())
    line_1_need = float(window_rows.loc[(window_rows["part_key"] == shared) & (window_rows["line"] == "Line_1"), "need"].sum())
    assert int(shown["Demand to Fulfil"]) == math.ceil(need)
    assert int(shown["Demand to Fulfil"]) > int(line_1.loc[shared, "Demand to Fulfil"])
    assert math.ceil(need) > math.ceil(line_1_need)
    assert int(shown["Need to be Picked"]) == math.ceil(need / BOX) * BOX
    assert int(shown["Need to be Picked"]) >= int(line_1.loc[shared, "Need to be Picked"])
