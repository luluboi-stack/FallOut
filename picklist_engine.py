"""Hourly sequence picklist: backend walk and frontend consolidation.

Pure pandas, no Streamlit. See docs/picklist_logic.md for the algorithm.
"""
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd

COVERED = "COVERED"
TRIGGERED = "TRIGGERED"
PARTIAL = "PARTIAL"
RED = "RED"

COLOUR_RED = "RED"
COLOUR_YELLOW = "YELLOW"
COLOUR_NONE = ""

STATUS_READY = "Ready for Full Transfer"
STATUS_PARTIAL = "Partial Transfer Available"
STATUS_OUT_OF_STOCK = "W004 Out of Stock"
STATUS_SUFFICIENT = "Sufficient M002 Stock"

EPS = 1e-9

WALK_COLUMNS = [
    "walk_order", "line", "sequence_no", "seq_key", "sku", "batch_count", "minute_offset",
    "part_id", "part_key", "description", "need",
    "plant_taken", "sto_taken", "w004_taken", "trigger", "shortfall", "status",
    "plant_after", "sto_after", "w004_after",
    "pss_qty", "pss_type",
    "m002_stock", "cogi_qty", "plant_opening", "sto_opening", "w004_opening", "open_po_numbers",
    "snapshot_id",
]

MASTER_COLUMNS = [
    "Sequence_Order", "Vehicle_SKU", "Part ID", "Part Name", "BOM Demand", "Demand to Fulfil",
    "Need to be Picked", "PSS Qty", "To be picked PSS count", "W004 Stock", "M002 Stock",
    "COGI Qty", "Open STO In-Transit", "Open PO Numbers", "Total M002 Pipeline", "PSS Type",
    "Starvation_Mins", "Priority", "Sequences_Covered", "Transfer Qty (W004 ➡️ M002)",
    "Transfer Status", "Is_Zero_Stock",
]


def clean_sap_key(val: Any) -> str:
    s = re.sub(r'[^A-Za-z0-9]', '', str(val)).upper()
    return s.lstrip("0")


def find_column_by_priority(df: pd.DataFrame, candidates: List[str], exclude: List[str] = None) -> Optional[str]:
    exclude = exclude or []
    for cand in candidates:
        for col in df.columns:
            c_clean = str(col).strip().lower()
            if cand in c_clean and not any(ex in c_clean for ex in exclude):
                return col
    return None


def priority_label(starvation_mins: int) -> str:
    if starvation_mins <= 60:
        return "🚨 P1: CRITICAL (<60m)"
    if starvation_mins <= 120:
        return "🟡 P2: HIGH (60-120m)"
    return "🟢 P3: NORMAL (>120m)"


def _ceil(x: float) -> int:
    return int(math.ceil(x - EPS)) if x > EPS else 0


def pick_quantity(trigger_sum: float, pss_qty: int) -> Tuple[int, int]:
    """Returns (pick qty, box count): ceil(sum / PSS) * PSS, or 0 when nothing triggered."""
    if trigger_sum <= EPS:
        return 0, 0
    if pss_qty <= 0:
        qty = _ceil(trigger_sum)
        return qty, qty
    boxes = _ceil(trigger_sum / pss_qty)
    return boxes * pss_qty, boxes


@dataclass
class StockBalances:
    """Opening balances per material key, set once before the walk."""
    plant: Dict[str, float] = field(default_factory=dict)
    sto: Dict[str, float] = field(default_factory=dict)
    w004: Dict[str, float] = field(default_factory=dict)
    m002_stock: Dict[str, float] = field(default_factory=dict)
    cogi: Dict[str, float] = field(default_factory=dict)
    open_pos: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_maps(
        cls,
        m002_rm01: Dict[str, float],
        m002_ps01: Dict[str, float],
        cogi: Dict[str, float],
        sto: Dict[str, float],
        w004_rm01: Dict[str, float],
        open_pos: Optional[Dict[str, str]] = None,
    ) -> "StockBalances":
        keys = set(m002_rm01) | set(m002_ps01) | set(cogi)
        m002 = {k: float(m002_rm01.get(k, 0.0)) + float(m002_ps01.get(k, 0.0)) for k in keys}
        plant = {k: max(0.0, m002[k] - float(cogi.get(k, 0.0))) for k in keys}
        return cls(
            plant=plant,
            sto={k: float(v) for k, v in sto.items()},
            w004={k: float(v) for k, v in w004_rm01.items()},
            m002_stock=m002,
            cogi={k: float(v) for k, v in cogi.items()},
            open_pos=dict(open_pos or {}),
        )


def order_plan_sequences(batches_df: pd.DataFrame) -> pd.DataFrame:
    """PLAN sequences in walk order: sequence number, then line. HOLD is never walked."""
    if batches_df.empty or "Status" not in batches_df.columns:
        return pd.DataFrame(columns=["Line", "ScaleX_Sequence_No", "SKUName", "Count", "seq_key", "minute_offset"])
    plan = batches_df[batches_df["Status"].astype(str).str.strip().str.upper() == "PLAN"].copy()
    plan["ScaleX_Sequence_No"] = plan["ScaleX_Sequence_No"].astype(int)
    plan["Count"] = pd.to_numeric(plan["Count"], errors="coerce").fillna(0).astype(int)
    plan = plan.sort_values(by=["ScaleX_Sequence_No", "Line"], kind="stable").reset_index(drop=True)
    plan["seq_key"] = plan["Line"].astype(str) + "#" + plan["ScaleX_Sequence_No"].astype(str)
    plan["minute_offset"] = plan.groupby("Line", sort=False)["Count"].cumsum() - plan["Count"]
    return plan


def build_bom_explosion(bom_df: pd.DataFrame, handled_units: Set[str]) -> Callable[[str], List[dict]]:
    """Returns sku -> [{part_id, description, unit_qty}], using the app's BOM recursion and handled-unit rule."""
    bom = bom_df.copy()
    bom.columns = [str(c).strip() for c in bom.columns]
    parent_col = find_column_by_priority(bom, ["parent material", "parent", "sku", "vehicle"]) or "Parent Material"
    child_col = find_column_by_priority(bom, ["child material", "component", "material", "part"]) or "Child Material"
    qty_col = find_column_by_priority(bom, ["comp qty", "quantity", "qty", "menge"]) or "Comp Qty."
    desc_col = find_column_by_priority(bom, ["component description", "material description", "description"]) or "Description"
    if desc_col not in bom.columns:
        bom[desc_col] = ""

    bom["Parent_Clean"] = bom[parent_col].apply(clean_sap_key)
    bom["Child_Clean"] = bom[child_col].apply(clean_sap_key)
    bom["Clean_Qty"] = pd.to_numeric(
        bom[qty_col].astype(str).str.replace(",", "").str.strip(), errors="coerce"
    ).fillna(0.0)

    lookup: Dict[str, List[dict]] = {}
    for parent_k, grp in bom.groupby("Parent_Clean"):
        lookup[parent_k] = grp[[child_col, "Child_Clean", desc_col, "Clean_Qty"]].to_dict(orient="records")

    def children(parent_key: str, current_qty: float, visited: Set[str]) -> List[dict]:
        if parent_key in visited:
            return []
        visited = visited | {parent_key}
        expanded = []
        for comp in lookup.get(parent_key, []):
            c_mat = comp[child_col]
            c_clean = comp["Child_Clean"]
            tot_qty = comp["Clean_Qty"] * current_qty
            is_handled_unit = (c_mat in handled_units) or (c_clean in handled_units)
            if c_clean in lookup and not is_handled_unit:
                expanded.extend(children(c_clean, tot_qty, visited))
            else:
                expanded.append({
                    "part_id": c_mat,
                    "description": str(comp.get(desc_col, "")).strip(),
                    "unit_qty": tot_qty,
                })
        return expanded

    cache: Dict[str, List[dict]] = {}

    def explode(sku: str) -> List[dict]:
        key = clean_sap_key(sku)
        if key not in cache:
            cache[key] = children(key, 1.0, set())
        return cache[key]

    return explode


def explode_sequences(plan_sequences: pd.DataFrame, explode: Callable[[str], List[dict]]) -> pd.DataFrame:
    """One row per sequence per material, in walk order. Need = unit qty x batch count."""
    rows = []
    for walk_idx, seq in plan_sequences.iterrows():
        sku = str(seq["SKUName"]).strip().upper()
        count = int(seq["Count"])
        per_part: Dict[str, dict] = {}
        for comp in explode(sku):
            key = clean_sap_key(comp["part_id"])
            if key not in per_part:
                per_part[key] = {
                    "part_id": str(comp["part_id"]).strip(),
                    "description": comp["description"],
                    "need": 0.0,
                }
            per_part[key]["need"] += comp["unit_qty"] * count
        for key, comp in per_part.items():
            rows.append({
                "seq_index": walk_idx,
                "line": seq["Line"],
                "sequence_no": int(seq["ScaleX_Sequence_No"]),
                "seq_key": seq["seq_key"],
                "sku": sku,
                "batch_count": count,
                "minute_offset": int(seq["minute_offset"]),
                "part_id": comp["part_id"],
                "part_key": key,
                "description": comp["description"],
                "need": float(comp["need"]),
            })
    return pd.DataFrame(rows)


def walk_sequences(
    demand: pd.DataFrame,
    balances: StockBalances,
    pss_lookup: Callable[[str, str], Tuple[int, str]],
    snapshot_id: str,
    descriptions: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """Takes each need from plant, then STO, then W004. Trigger is the W004 part only."""
    if demand.empty:
        return pd.DataFrame(columns=WALK_COLUMNS)

    descriptions = descriptions or {}
    remaining: Dict[str, List[float]] = {}
    material_info: Dict[str, dict] = {}
    out = []

    ordered = demand.sort_values(by=["seq_index", "part_key"], kind="stable")
    for walk_order, r in enumerate(ordered.itertuples(index=False)):
        key = r.part_key
        if key not in remaining:
            remaining[key] = [
                balances.plant.get(key, 0.0),
                balances.sto.get(key, 0.0),
                balances.w004.get(key, 0.0),
            ]
            desc = descriptions.get(r.part_id, descriptions.get(key, r.description))
            pss_qty, pss_type = pss_lookup(r.part_id, desc)
            material_info[key] = {
                "description": desc,
                "pss_qty": int(pss_qty),
                "pss_type": pss_type,
                "m002_stock": balances.m002_stock.get(key, 0.0),
                "cogi_qty": balances.cogi.get(key, 0.0),
                "plant_opening": balances.plant.get(key, 0.0),
                "sto_opening": balances.sto.get(key, 0.0),
                "w004_opening": balances.w004.get(key, 0.0),
                "open_po_numbers": balances.open_pos.get(key, ""),
            }
        bal = remaining[key]
        rest = r.need
        taken = []
        for i in range(3):
            t = min(rest, bal[i])
            bal[i] -= t
            rest -= t
            taken.append(t)
        plant_taken, sto_taken, w004_taken = taken
        shortfall = rest if rest > EPS else 0.0

        if shortfall > 0:
            status = PARTIAL if w004_taken > EPS else RED
        else:
            status = TRIGGERED if w004_taken > EPS else COVERED

        info = material_info[key]
        out.append({
            "walk_order": walk_order,
            "line": r.line,
            "sequence_no": r.sequence_no,
            "seq_key": r.seq_key,
            "sku": r.sku,
            "batch_count": r.batch_count,
            "minute_offset": r.minute_offset,
            "part_id": r.part_id,
            "part_key": key,
            "description": info["description"],
            "need": r.need,
            "plant_taken": plant_taken,
            "sto_taken": sto_taken,
            "w004_taken": w004_taken,
            "trigger": w004_taken,
            "shortfall": shortfall,
            "status": status,
            "plant_after": bal[0],
            "sto_after": bal[1],
            "w004_after": bal[2],
            "pss_qty": info["pss_qty"],
            "pss_type": info["pss_type"],
            "m002_stock": info["m002_stock"],
            "cogi_qty": info["cogi_qty"],
            "plant_opening": info["plant_opening"],
            "sto_opening": info["sto_opening"],
            "w004_opening": info["w004_opening"],
            "open_po_numbers": info["open_po_numbers"],
            "snapshot_id": snapshot_id,
        })
    return pd.DataFrame(out, columns=WALK_COLUMNS)


def run_walk(
    batches_df: pd.DataFrame,
    bom_df: pd.DataFrame,
    balances: StockBalances,
    *,
    handled_units: Set[str],
    pss_lookup: Callable[[str, str], Tuple[int, str]],
    snapshot_id: str,
    descriptions: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    plan = order_plan_sequences(batches_df)
    if plan.empty or bom_df.empty:
        return pd.DataFrame(columns=WALK_COLUMNS)
    explode = build_bom_explosion(bom_df, handled_units)
    demand = explode_sequences(plan, explode)
    return walk_sequences(demand, balances, pss_lookup, snapshot_id, descriptions)


def colour_for(statuses: Iterable[str], trigger_sum: float) -> str:
    statuses = list(statuses)
    needed_w004 = [s for s in statuses if s in (TRIGGERED, PARTIAL, RED)]
    if needed_w004 and all(s == RED for s in needed_w004):
        return COLOUR_RED
    if trigger_sum > EPS and any(s in (PARTIAL, RED) for s in statuses):
        return COLOUR_YELLOW
    return COLOUR_NONE


def transfer_status_for(colour: str, pick_qty: int) -> str:
    if colour == COLOUR_RED:
        return STATUS_OUT_OF_STOCK
    if colour == COLOUR_YELLOW:
        return STATUS_PARTIAL
    if pick_qty > 0:
        return STATUS_READY
    return STATUS_SUFFICIENT


def select_rows(walk_rows: pd.DataFrame, selected_keys: Optional[Iterable[str]] = None) -> pd.DataFrame:
    """None selects every sequence in the walk; an empty set selects none."""
    if walk_rows.empty or selected_keys is None:
        return walk_rows
    return walk_rows[walk_rows["seq_key"].isin(set(selected_keys))]


def consolidate(walk_rows: pd.DataFrame, selected_keys: Optional[Iterable[str]] = None) -> pd.DataFrame:
    """Stored walk rows for the selected sequences -> master_df, one row per material."""
    rows = select_rows(walk_rows, selected_keys)
    if rows.empty:
        return pd.DataFrame(columns=MASTER_COLUMNS)

    line_start = rows.groupby("line")["minute_offset"].min().to_dict()
    records = []
    for _, grp in rows.groupby("part_key", sort=True):
        grp = grp.sort_values("walk_order")
        first = grp.iloc[0]
        trigger_sum = float(grp["trigger"].sum())
        pss_qty = int(first["pss_qty"])
        pick, boxes = pick_quantity(trigger_sum, pss_qty)
        colour = colour_for(grp["status"], trigger_sum)
        starvation = int((grp["minute_offset"] - grp["line"].map(line_start)).min())

        records.append({
            "Sequence_Order": int(grp["sequence_no"].min()),
            "Vehicle_SKU": ", ".join(sorted(set(grp["sku"]))),
            "Part ID": first["part_id"],
            "Part Name": first["description"],
            "BOM Demand": _ceil(float(grp["need"].sum())),
            "Demand to Fulfil": _ceil(trigger_sum),
            "Need to be Picked": pick,
            "PSS Qty": pss_qty,
            "To be picked PSS count": boxes,
            "W004 Stock": int(first["w004_opening"]),
            "M002 Stock": int(first["m002_stock"]),
            "COGI Qty": int(first["cogi_qty"]),
            "Open STO In-Transit": int(first["sto_opening"]),
            "Open PO Numbers": first["open_po_numbers"],
            "Total M002 Pipeline": int(first["plant_opening"] + first["sto_opening"]),
            "PSS Type": first["pss_type"],
            "Starvation_Mins": starvation,
            "Priority": priority_label(starvation),
            "Sequences_Covered": f"Seqs #{int(grp['sequence_no'].min())} - #{int(grp['sequence_no'].max())}",
            "Transfer Qty (W004 ➡️ M002)": pick,
            "Transfer Status": transfer_status_for(colour, pick),
            "Is_Zero_Stock": colour == COLOUR_RED,
        })
    return pd.DataFrame(records, columns=MASTER_COLUMNS)


def audit_decision(transfer_status: str, sto_taken: float) -> Tuple[str, str, str]:
    """(picklist_status, decision_code, decision_reason) using the codes the RCA already reads."""
    if transfer_status == STATUS_READY:
        return "INCLUDED", "ACCEPTED_DEFICIT_FULFILLED", "Sequence walk: W004 RM01 covers every triggered sequence."
    if transfer_status == STATUS_PARTIAL:
        return "INCLUDED", "ACCEPTED_DEFICIT_FULFILLED", "Sequence walk: W004 RM01 ran out part-way; partial transfer."
    if transfer_status == STATUS_OUT_OF_STOCK:
        return "REJECTED", "REJECTED_W004_STOCKOUT", "Sequence walk: need unmet and W004 RM01 already 0 for every sequence that needed it."
    if sto_taken > EPS:
        return "REJECTED", "REJECTED_COVERED_BY_STO", "Sequence walk: plant stock plus open STO cover the selected sequences."
    return "REJECTED", "REJECTED_SUFFICIENT_M002", "Sequence walk: M002 plant stock covers the selected sequences."
