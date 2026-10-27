"""Unrestricted stock, COGI, and open STO for the live BOM checker.

Pure comparisons and SQL live here. The Streamlit page is live_bom_stock_view.py.
Demo fixtures never open a warehouse connection.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

import pandas as pd

import bom_databricks
from bom_databricks import (
    BOM_COLUMNS,
    DataSourceError,
    DatabricksSettings,
    _aliased_text,
    _require_column,
    bom_table,
    build_bom_sql,
    databricks_connect_kwargs,
    find_column,
    quote_column,
    quote_table,
)

# Unrestricted stock for live M002 and W004 (MARD-LABST). Env: DATABRICKS_MARD_TABLE.
MARD_TABLE = "sap.sapabap1.nsdm_v_mard_hourly"
# Hourly copies repeat the balance. batch_ts is the load instant on nsdm_v_mard_hourly.
STOCK_SNAPSHOT_COLUMNS = (
    "batch_ts",
    "read_at",
    "load_timestamp",
    "snapshot_ts",
    "ingestion_timestamp",
    "di_sequence_number",
    "calday",
)
# COGI failed goods movements (AFFW). Env: DATABRICKS_COGI_TABLE.
COGI_TABLE = "sap.sapabap1.affw"
# Hourly COGI extract, queried only after AFFW DESCRIBE fails. Env override replaces both.
COGI_TABLE_FALLBACK = "sap.sapabap1.affw_hourly"
# STO header: supplying plant and document type. Env: DATABRICKS_EKKO_TABLE.
EKKO_TABLE = "sap.sapabap1.ekko"
# STO item: material and receiving plant. Env: DATABRICKS_EKPO_TABLE.
EKPO_TABLE = "sap.sapabap1.ekpo"
# STO schedule line: open qty is menge minus wemng. Env: DATABRICKS_EKET_TABLE.
EKET_TABLE = "sap.sapabap1.eket"

# Parents the replenishment walk reads.
WALK_PARENT_PREFIXES = ("S450", "SEL", "SRIZ")

M002_SLOCS = ("RM01", "PS01", "PV01")
W004_SLOC = "RM01"

TOOL_EQUATION = (
    "m002_raw = RM01 + PS01 + PV01 unrestricted; "
    "cogi = summed COGI qty (0 when no row); "
    "m002_effective = max(0, m002_raw - cogi); "
    "open_sto = open STO qty in transit W004 → M002 (0 when no row); "
    "w004_rm01 = W004 RM01 unrestricted"
)

EQUATION_COLUMNS = [
    "Material",
    "M002 RM01",
    "M002 PS01",
    "M002 PV01",
    "m002_raw",
    "cogi",
    "m002_effective",
    "open_sto",
    "w004_rm01",
]

STOCK_COLUMNS = ["Material", "Plant", "Storage Location", "Unrestricted"]
COGI_COLUMNS = ["Material", "Quantity"]
STO_COLUMNS = ["Material", "Open Qty"]


def clean_sap_key(val: Any) -> str:
    """Same rule as app.clean_sap_key: alphanumerics, upper case, leading zeros dropped."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(val)).upper()
    return s.lstrip("0")


@dataclass(frozen=True)
class StockTables:
    mard_tables: tuple[str, ...]
    cogi_tables: tuple[str, ...]
    ekko_table: str
    ekpo_table: str
    eket_table: str

    @classmethod
    def resolve(cls, environ: Optional[Mapping[str, str]] = None) -> "StockTables":
        env = os.environ if environ is None else environ

        def pick(env_name: str, default: str) -> str:
            raw = str(env.get(env_name, "") or "").strip()
            return raw or default

        cogi_override = str(env.get("DATABRICKS_COGI_TABLE", "") or "").strip()
        cogi_tables = (cogi_override,) if cogi_override else (COGI_TABLE, COGI_TABLE_FALLBACK)
        mard_override = str(env.get("DATABRICKS_MARD_TABLE", "") or "").strip()
        mard_tables = (mard_override,) if mard_override else (MARD_TABLE,)
        return cls(
            mard_tables=mard_tables,
            cogi_tables=cogi_tables,
            ekko_table=pick("DATABRICKS_EKKO_TABLE", EKKO_TABLE),
            ekpo_table=pick("DATABRICKS_EKPO_TABLE", EKPO_TABLE),
            eket_table=pick("DATABRICKS_EKET_TABLE", EKET_TABLE),
        )


@dataclass
class PageFeeds:
    bom: pd.DataFrame
    bom_error: Optional[str]
    stock: pd.DataFrame
    stock_error: Optional[str]
    cogi: pd.DataFrame
    cogi_error: Optional[str]
    sto: pd.DataFrame
    sto_error: Optional[str]
    demo: bool
    stock_table: Optional[str] = None
    stock_period: Optional[str] = None


def empty_stock() -> pd.DataFrame:
    return pd.DataFrame(columns=STOCK_COLUMNS)


def empty_cogi() -> pd.DataFrame:
    return pd.DataFrame(columns=COGI_COLUMNS)


def empty_sto() -> pd.DataFrame:
    return pd.DataFrame(columns=STO_COLUMNS)


def empty_bom() -> pd.DataFrame:
    return pd.DataFrame(columns=BOM_COLUMNS)


def _qty_sql(alias: str, column: str) -> str:
    """Numeric quantity. Thousands commas are removed so a string balance is not read as zero."""
    text = _aliased_text(alias, column)
    cleaned = f"REGEXP_REPLACE({text}, ',', '')"
    return f"CAST({cleaned} AS DOUBLE)"


_INDIA_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def india_stamp(value: str) -> str:
    """A timestamp written in India time: YYYY-MM-DD HH:MM:SS."""
    text = str(value).strip()
    if _INDIA_STAMP.match(text) is None:
        raise DataSourceError(
            "Stock time must be YYYY-MM-DD HH:MM:SS in India time, "
            f"for example 2026-10-27 11:00:00. Got: {value}"
        )
    return text


def _batch_instant_sql(
    table: str,
    snap: str,
    period_start: Optional[str],
    period_end: Optional[str],
) -> str:
    """One batch_ts only. A period keeps the latest batch inside that India-time window."""
    column = quote_column(snap)
    if period_start or period_end:
        if not period_start or not period_end:
            raise DataSourceError("Stock period needs both a start and an end.")
        start = india_stamp(period_start)
        end = india_stamp(period_end)
        if start >= end:
            raise DataSourceError("Stock period start must be before the end.")

        def local(alias: str) -> str:
            return (
                f"from_utc_timestamp(CAST({alias}.{column} AS TIMESTAMP), 'Asia/Kolkata')"
            )

        window = f"{local('i')} >= TIMESTAMP '{start}' AND {local('i')} < TIMESTAMP '{end}'"
        return (
            f"s.{column} = (SELECT MAX(i.{column}) FROM {quote_table(table)} i WHERE {window}) AND "
        )
    return (
        f"s.{column} = (SELECT MAX(i.{column}) FROM {quote_table(table)} i "
        f"WHERE CAST(i.{column} AS TIMESTAMP) <= current_timestamp()) AND "
    )


def build_stock_sql(
    table: str,
    columns: Sequence[str],
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
) -> str:
    """Unrestricted labst for live M002 and W004 from the same stock table.

    M002 floor is RM01 + PS01 + PV01. W004 is RM01, the pick source.
    """
    matnr = _require_column(columns, "matnr")
    werks = _require_column(columns, "werks")
    lgort = _require_column(columns, "lgort")
    labst = find_column(columns, "labst")
    if labst is None:
        raise DataSourceError(
            "Unrestricted column labst is missing from the stock table. "
            "Quality inspection and blocked stock are not a substitute. "
            f"Columns: {list(columns)}"
        )
    plant = _aliased_text("s", werks)
    sloc = _aliased_text("s", lgort)
    m002_slocs = ", ".join(f"'{name}'" for name in M002_SLOCS)
    where = (
        f"(UPPER({plant}) = 'M002' AND UPPER({sloc}) IN ({m002_slocs}))"
        f" OR (UPPER({plant}) = 'W004' AND UPPER({sloc}) = '{W004_SLOC}')"
    )
    material = _aliased_text("s", matnr)
    snap = find_column(columns, *STOCK_SNAPSHOT_COLUMNS)
    if (period_start or period_end) and snap is None:
        raise DataSourceError(
            "This stock table has no batch_ts column, so a time period would mix every load together."
        )
    snapshot_filter = _batch_instant_sql(table, snap, period_start, period_end) if snap else ""
    return f"""
        SELECT
            {material} AS `Material`,
            {plant} AS `Plant`,
            {sloc} AS `Storage Location`,
            SUM({_qty_sql("s", labst)}) AS `Unrestricted`
        FROM {quote_table(table)} s
        WHERE {snapshot_filter}({where})
        GROUP BY {material}, {plant}, {sloc}
    """


def build_cogi_sql(table: str, columns: Sequence[str]) -> str:
    """Sum failed-goods-movement quantity by material for plant M002."""
    matnr = _require_column(columns, "matnr", "material")
    werks = _require_column(columns, "werks", "plant")
    qty = find_column(columns, "erfmg", "quantity", "qty", "menge")
    if qty is None:
        raise DataSourceError(
            "COGI quantity column erfmg is missing. "
            f"Columns: {list(columns)}"
        )
    material = _aliased_text("c", matnr)
    return f"""
        SELECT
            {material} AS `Material`,
            SUM({_qty_sql("c", qty)}) AS `Quantity`
        FROM {quote_table(table)} c
        WHERE UPPER({_aliased_text("c", werks)}) = 'M002'
            AND {material} <> ''
        GROUP BY {material}
    """


def _eq(left_alias: str, left_col: str, right_alias: str, right_col: str) -> str:
    return f"{_aliased_text(left_alias, left_col)} = {_aliased_text(right_alias, right_col)}"


def _eq_doc(left_alias: str, left_col: str, right_alias: str, right_col: str) -> str:
    """Match a purchasing-document key with or without leading zeros."""
    left = f"REGEXP_REPLACE({_aliased_text(left_alias, left_col)}, '^0+', '')"
    right = f"REGEXP_REPLACE({_aliased_text(right_alias, right_col)}, '^0+', '')"
    return f"{left} = {right}"


def _and_mandt(
    clause: str,
    left_alias: str,
    left_columns: Sequence[str],
    right_alias: str,
    right_columns: Sequence[str],
) -> str:
    left = find_column(left_columns, "mandt")
    right = find_column(right_columns, "mandt")
    if left and right:
        return clause + " AND " + _eq(left_alias, left, right_alias, right)
    return clause


def _is_blank(alias: str, column: str) -> str:
    text = _aliased_text(alias, column)
    return f"({text} IS NULL OR {text} = '')"


def _open_qty_expr(alias: str, columns: Sequence[str]) -> str:
    menge = find_column(columns, "menge")
    wemng = find_column(columns, "wemng")
    if menge is None or wemng is None:
        raise DataSourceError(
            "Open STO quantity needs menge minus wemng (received). "
            "Ordered quantity alone is not used."
        )
    return (
        f"GREATEST({_qty_sql(alias, menge)} - "
        f"COALESCE({_qty_sql(alias, wemng)}, 0), 0)"
    )


def build_open_sto_sql(
    ekko_table: str,
    ekpo_table: str,
    ekko_columns: Sequence[str],
    ekpo_columns: Sequence[str],
    eket_table: Optional[str] = None,
    eket_columns: Optional[Sequence[str]] = None,
) -> str:
    """Open stock-transport qty from W004 to M002, summed by material.

    Schedule lines (EKET) supply open qty when that table is described.
    Otherwise EKPO is used only when it has received quantity (wemng).
    """
    ebeln_h = _require_column(ekko_columns, "ebeln")
    ebeln_i = _require_column(ekpo_columns, "ebeln")
    ebelp_i = _require_column(ekpo_columns, "ebelp")
    matnr = _require_column(ekpo_columns, "matnr")
    reswk = _require_column(ekko_columns, "reswk")
    werks = _require_column(ekpo_columns, "werks")
    loekz_i = _require_column(ekpo_columns, "loekz")
    elikz = _require_column(ekpo_columns, "elikz")

    header_item = _and_mandt(
        _eq_doc("ko", ebeln_h, "po", ebeln_i),
        "ko",
        ekko_columns,
        "po",
        ekpo_columns,
    )
    joins = [
        f"FROM {quote_table(ekko_table)} ko",
        f"INNER JOIN {quote_table(ekpo_table)} po ON {header_item}",
    ]
    qty_alias = "po"
    qty_columns: Sequence[str] = ekpo_columns
    if eket_table and eket_columns is not None:
        if find_column(eket_columns, "menge") and find_column(eket_columns, "wemng"):
            ebeln_e = _require_column(eket_columns, "ebeln")
            ebelp_e = _require_column(eket_columns, "ebelp")
            schedule = _and_mandt(
                _eq_doc("po", ebeln_i, "et", ebeln_e) + " AND " + _eq_doc("po", ebelp_i, "et", ebelp_e),
                "po",
                ekpo_columns,
                "et",
                eket_columns,
            )
            joins.append(f"INNER JOIN {quote_table(eket_table)} et ON {schedule}")
            qty_alias = "et"
            qty_columns = eket_columns
    open_expr = _open_qty_expr(qty_alias, qty_columns)
    # RESWK is filled only on a stock transport. Document type is not limited to UB.
    filters = [
        f"UPPER({_aliased_text('ko', reswk)}) = 'W004'",
        f"UPPER({_aliased_text('po', werks)}) = 'M002'",
        _is_blank("po", loekz_i),
        _is_blank("po", elikz),
        f"{open_expr} > 0",
    ]
    loekz_h = find_column(ekko_columns, "loekz")
    if loekz_h:
        filters.append(_is_blank("ko", loekz_h))
    material = _aliased_text("po", matnr)
    return f"""
        SELECT
            {material} AS `Material`,
            SUM({open_expr}) AS `Open Qty`
        {' '.join(joins)}
        WHERE {" AND ".join(filters)}
        GROUP BY {material}
    """


def _frame_column(frame: pd.DataFrame, *names: str) -> Optional[str]:
    lookup = {str(col).strip().lower(): col for col in frame.columns}
    for name in names:
        found = lookup.get(name.strip().lower())
        if found is not None:
            return found
    return None


def _to_qty(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.replace(",", "", regex=False).str.strip()
    text = text.replace({"": "0", "None": "0", "nan": "0", "NaN": "0"})
    return pd.to_numeric(text, errors="coerce").fillna(0.0)


def _sum_by_material(frame: pd.DataFrame, *qty_names: str) -> pd.Series:
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    mat_col = _frame_column(frame, "Material", "matnr", "material")
    qty_col = _frame_column(frame, *qty_names)
    if mat_col is None or qty_col is None:
        raise DataSourceError(
            "Expected a material column and one of: " + ", ".join(qty_names)
        )
    keys = frame[mat_col].map(clean_sap_key)
    values = _to_qty(frame[qty_col])
    grouped = values.groupby(keys).sum()
    return grouped[grouped.index != ""]


def unrestricted_buckets(stock: pd.DataFrame) -> pd.DataFrame:
    """Sum unrestricted quantity into the M002 floor and W004 RM01."""
    columns = ["Material", "M002 RM01", "M002 PS01", "M002 PV01", "m002_raw", "w004_rm01"]
    if stock is None or stock.empty:
        return pd.DataFrame(columns=columns)
    mat_col = _frame_column(stock, "Material", "matnr")
    plant_col = _frame_column(stock, "Plant", "werks")
    sloc_col = _frame_column(stock, "Storage Location", "lgort", "sloc")
    qty_col = _frame_column(stock, "Unrestricted", "labst")
    if mat_col is None or plant_col is None or sloc_col is None or qty_col is None:
        raise DataSourceError(
            "Stock rows need material, plant, storage location, and unrestricted labst."
        )
    work = pd.DataFrame(
        {
            "Material": stock[mat_col].map(clean_sap_key),
            "Plant": stock[plant_col].astype(str).str.strip().str.upper(),
            "SLoc": stock[sloc_col].astype(str).str.strip().str.upper(),
            "Qty": _to_qty(stock[qty_col]),
        }
    )
    work = work[work["Material"] != ""]

    def bucket(plant: str, sloc: str) -> pd.Series:
        mask = (work["Plant"] == plant) & (work["SLoc"] == sloc)
        return work.loc[mask].groupby("Material")["Qty"].sum()

    m_rm = bucket("M002", "RM01")
    m_ps = bucket("M002", "PS01")
    m_pv = bucket("M002", "PV01")
    w_rm = bucket("W004", "RM01")
    materials = sorted(set(m_rm.index) | set(m_ps.index) | set(m_pv.index) | set(w_rm.index))
    rows = []
    for material in materials:
        rm = float(m_rm.get(material, 0.0))
        ps = float(m_ps.get(material, 0.0))
        pv = float(m_pv.get(material, 0.0))
        rows.append(
            {
                "Material": material,
                "M002 RM01": rm,
                "M002 PS01": ps,
                "M002 PV01": pv,
                "m002_raw": rm + ps + pv,
                "w004_rm01": float(w_rm.get(material, 0.0)),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def tool_quantities(stock: pd.DataFrame, cogi: pd.DataFrame, sto: pd.DataFrame) -> pd.DataFrame:
    """One row per material using the replenishment tool's stock equation."""
    buckets = unrestricted_buckets(stock)
    cogi_sum = _sum_by_material(cogi, "Quantity", "cogi", "erfmg", "qty")
    sto_sum = _sum_by_material(sto, "Open Qty", "open_sto", "open qty", "quantity")
    indexed = buckets.set_index("Material") if not buckets.empty else buckets
    materials = sorted(set(buckets["Material"]) | set(cogi_sum.index) | set(sto_sum.index))
    rows = []
    for material in materials:
        if not indexed.empty and material in indexed.index:
            rm = float(indexed.at[material, "M002 RM01"])
            ps = float(indexed.at[material, "M002 PS01"])
            pv = float(indexed.at[material, "M002 PV01"])
            raw = float(indexed.at[material, "m002_raw"])
            w004 = float(indexed.at[material, "w004_rm01"])
        else:
            rm = ps = pv = raw = w004 = 0.0
        cogi_qty = float(cogi_sum.get(material, 0.0))
        open_sto = float(sto_sum.get(material, 0.0))
        rows.append(
            {
                "Material": material,
                "M002 RM01": rm,
                "M002 PS01": ps,
                "M002 PV01": pv,
                "m002_raw": raw,
                "cogi": cogi_qty,
                "m002_effective": max(0.0, raw - cogi_qty),
                "open_sto": open_sto,
                "w004_rm01": w004,
            }
        )
    return pd.DataFrame(rows, columns=EQUATION_COLUMNS)


def material_quantities(
    bom: pd.DataFrame,
    stock: pd.DataFrame,
    cogi: pd.DataFrame,
    sto: pd.DataFrame,
) -> pd.DataFrame:
    """True quantity of every BOM material, plus any material that has stock, COGI, or open STO.

    A missing stock row is 0. That 0 is the warehouse balance, not a blank feed.
    """
    quantities = tool_quantities(stock, cogi, sto)
    indexed = quantities.set_index("Material") if not quantities.empty else quantities
    children, _on_bom = bom_material_keys(bom)
    extra = set(quantities["Material"]) if not quantities.empty else set()
    materials = sorted(set(children) | extra)
    walk = _walk_skus_by_child(bom)
    rows = []
    for material in materials:
        if not indexed.empty and material in indexed.index:
            source = indexed.loc[material]
            if isinstance(source, pd.DataFrame):
                source = source.iloc[0]
            values = {column: float(source[column]) for column in EQUATION_COLUMNS if column != "Material"}
        else:
            values = {column: 0.0 for column in EQUATION_COLUMNS if column != "Material"}
        values["Material"] = material
        values["Walk SKU"] = walk.get(material, "")
        rows.append(values)
    columns = ["Walk SKU", *EQUATION_COLUMNS]
    return pd.DataFrame(rows, columns=columns)


def explode_walk_bom(
    bom: pd.DataFrame,
    prefixes: Sequence[str] = WALK_PARENT_PREFIXES,
) -> pd.DataFrame:
    """Walk S450, SEL, and SRIZ, then explode any child that is itself a parent.

    The other parent stays the child of the walk SKU. Its components are added
    with that same walk SKU, so stock and open STO follow the component.
    """
    if bom is None or bom.empty:
        return bom
    parent_col = _frame_column(bom, "Parent Material")
    child_col = _frame_column(bom, "Child Material")
    if parent_col is None or child_col is None:
        return bom
    work = bom.copy()
    work["_parent"] = work[parent_col].map(clean_sap_key)
    work["_child"] = work[child_col].map(clean_sap_key)
    grouped: dict[str, list[int]] = {}
    for idx, parent in work["_parent"].items():
        if parent:
            grouped.setdefault(str(parent), []).append(idx)
    seeds = sorted(key for key in grouped if any(key.startswith(prefix) for prefix in prefixes))
    keep_idx: list[int] = []
    walk_skus: list[str] = []
    for seed in seeds:
        seen: set[str] = set()
        stack = [seed]
        while stack:
            parent = stack.pop()
            if parent in seen:
                continue
            seen.add(parent)
            for idx in grouped.get(parent, []):
                keep_idx.append(idx)
                walk_skus.append(seed)
                child = str(work.at[idx, "_child"])
                if child and child not in seen and child in grouped:
                    stack.append(child)
    trimmed = work.drop(columns=["_parent", "_child"])
    if not keep_idx:
        empty = trimmed.iloc[0:0].copy()
        empty.insert(0, "Walk SKU", pd.Series(dtype=str))
        empty.insert(1, "Parent as child", pd.Series(dtype=str))
        return empty
    out = trimmed.loc[keep_idx].copy()
    out.insert(0, "Walk SKU", walk_skus)
    out = _map_kit_parents(out, parent_col, child_col)
    front = [column for column in ("Walk SKU", "Parent as child", parent_col, child_col) if column in out.columns]
    rest = [column for column in out.columns if column not in front]
    return out.loc[:, front + rest].reset_index(drop=True)


def _map_kit_parents(frame: pd.DataFrame, parent_col: str, child_col: str) -> pd.DataFrame:
    """Map a kit that is both a parent and a child.

    Under an S450, SEL, or SRIZ walk SKU the kit stays the child.
    Each of that kit's parts stays parent = kit, child = part, and
    Parent as child repeats the kit on those rows.
    """
    parents = {clean_sap_key(value) for value in frame[parent_col]}
    children = {clean_sap_key(value) for value in frame[child_col]}
    kits = parents & children
    kits.discard("")
    mapped = []
    for parent, child in zip(frame[parent_col], frame[child_col]):
        parent_key = clean_sap_key(parent)
        child_key = clean_sap_key(child)
        if parent_key in kits:
            mapped.append(str(parent).strip())
        elif child_key in kits:
            mapped.append(str(child).strip())
        else:
            mapped.append("")
    out = frame.copy()
    out.insert(1, "Parent as child", mapped)
    return out


def bom_material_keys(bom: pd.DataFrame) -> tuple[set[str], set[str]]:
    """Child materials, and every material number that appears on the BOM."""
    if bom is None or bom.empty:
        return set(), set()
    children: set[str] = set()
    parents: set[str] = set()
    child_col = _frame_column(bom, "Child Material")
    parent_col = _frame_column(bom, "Parent Material")
    if child_col:
        children = {clean_sap_key(value) for value in bom[child_col]}
    if parent_col:
        parents = {clean_sap_key(value) for value in bom[parent_col]}
    children.discard("")
    parents.discard("")
    return children, children | parents


def _walk_skus_by_child(bom: pd.DataFrame) -> dict[str, str]:
    walk_col = _frame_column(bom, "Walk SKU")
    child_col = _frame_column(bom, "Child Material")
    if walk_col is None or child_col is None or bom is None or bom.empty:
        return {}
    found: dict[str, set[str]] = {}
    for child, walk in zip(bom[child_col], bom[walk_col]):
        key = clean_sap_key(child)
        sku = clean_sap_key(walk)
        if key and sku:
            found.setdefault(key, set()).add(sku)
    return {key: ", ".join(sorted(values)) for key, values in found.items()}


def _qty(indexed: pd.DataFrame, material: str, column: str) -> float:
    if indexed.empty or material not in indexed.index:
        return 0.0
    return float(indexed.at[material, column])


def compare_bom_stock(
    bom: pd.DataFrame,
    stock: pd.DataFrame,
    cogi: pd.DataFrame,
    sto: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Four checker sections. A missing COGI row is not a missing part."""
    quantities = tool_quantities(stock, cogi, sto)
    indexed = quantities.set_index("Material") if not quantities.empty else quantities
    children, on_bom = bom_material_keys(bom)
    parent_col = _frame_column(bom, "Parent Material")
    immediate = set()
    if parent_col is not None:
        immediate = {clean_sap_key(value) for value in bom[parent_col]}
        immediate.discard("")
    leaves = children - immediate
    if not leaves:
        leaves = children
    walk_for = _walk_skus_by_child(bom)

    missing_rows = []
    for material in sorted(leaves):
        raw = _qty(indexed, material, "m002_raw")
        w004 = _qty(indexed, material, "w004_rm01")
        if raw <= 0 and w004 <= 0:
            missing_rows.append(
                {
                    "Walk SKU": walk_for.get(material, ""),
                    "Material": material,
                    "m002_raw": raw,
                    "w004_rm01": w004,
                    "cogi": _qty(indexed, material, "cogi"),
                    "open_sto": _qty(indexed, material, "open_sto"),
                }
            )

    stock_rows = []
    cogi_rows = []
    sto_rows = []
    if not quantities.empty:
        for material, row in quantities.set_index("Material").iterrows():
            key = str(material)
            if key in on_bom:
                continue
            if float(row["m002_raw"]) > 0 or float(row["w004_rm01"]) > 0:
                stock_rows.append(
                    {
                        "Material": key,
                        "m002_raw": float(row["m002_raw"]),
                        "w004_rm01": float(row["w004_rm01"]),
                    }
                )
            if float(row["cogi"]) > 0:
                cogi_rows.append({"Material": key, "cogi": float(row["cogi"])})
            if float(row["open_sto"]) > 0:
                sto_rows.append({"Material": key, "open_sto": float(row["open_sto"])})

    return {
        "quantities": quantities,
        "missing_unrestricted": pd.DataFrame(
            missing_rows,
            columns=["Walk SKU", "Material", "m002_raw", "w004_rm01", "cogi", "open_sto"],
        ),
        "stock_not_on_bom": pd.DataFrame(
            stock_rows, columns=["Material", "m002_raw", "w004_rm01"]
        ),
        "cogi_not_on_bom": pd.DataFrame(cogi_rows, columns=["Material", "cogi"]),
        "sto_not_on_bom": pd.DataFrame(sto_rows, columns=["Material", "open_sto"]),
    }


def _fixture(name: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", name)


def _read_fixture(name: str) -> pd.DataFrame:
    return pd.read_csv(_fixture(name), dtype=str, keep_default_na=False)


def load_demo_feeds() -> PageFeeds:
    """Fixture BOM, stock, COGI, and open STO. Does not open Databricks."""
    bom = explode_walk_bom(bom_table(_read_fixture("live_bom_stock_bom.csv")))
    stock = _read_fixture("live_bom_stock_mard.csv")
    keep = [column for column in STOCK_COLUMNS if column in stock.columns]
    if keep != STOCK_COLUMNS:
        raise DataSourceError(f"Demo stock fixture is missing {set(STOCK_COLUMNS) - set(keep)}")
    return PageFeeds(
        bom=bom,
        bom_error=None,
        stock=stock.loc[:, STOCK_COLUMNS].copy(),
        stock_error=None,
        cogi=_read_fixture("live_bom_stock_cogi.csv"),
        cogi_error=None,
        sto=_read_fixture("live_bom_stock_sto.csv"),
        sto_error=None,
        demo=True,
    )


def load_bom_frame(conn: Any, settings: DatabricksSettings) -> pd.DataFrame:
    mast_cols = bom_databricks._describe(conn, settings.mast_table)
    stpo_cols = bom_databricks._describe(conn, settings.stpo_table)
    stas_cols = bom_databricks._describe(conn, settings.stas_table)
    sql = build_bom_sql(
        settings.mast_table,
        settings.stpo_table,
        mast_cols,
        stpo_cols,
        settings.stas_table,
        stas_cols,
    )
    return explode_walk_bom(bom_table(bom_databricks._query(conn, sql)))


def load_stock_frame(
    conn: Any,
    tables: Sequence[str],
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
) -> tuple[pd.DataFrame, str]:
    """DESCRIBE each stock candidate and query the first table that exists."""
    if not tables:
        raise DataSourceError("No stock table configured")
    errors: list[str] = []
    for table in tables:
        try:
            columns = bom_databricks._describe(conn, table)
        except DataSourceError as exc:
            if not _table_absent(exc):
                raise
            errors.append(f"{table}: {exc}")
            continue
        return bom_databricks._query(
            conn, build_stock_sql(table, columns, period_start, period_end)
        ), table
    raise DataSourceError("Stock table is not in the catalog. " + " | ".join(errors))


def _table_absent(exc: BaseException) -> bool:
    text = str(exc).lower()
    markers = (
        "table_or_view_not_found",
        "table or view not found",
        "cannot be found",
        "does not exist",
        "no such table",
        "not found",
    )
    return any(marker in text for marker in markers)


def load_cogi_frame(conn: Any, tables: Sequence[str]) -> tuple[pd.DataFrame, str]:
    """DESCRIBE each COGI candidate and query the first table that exists."""
    if not tables:
        raise DataSourceError("No COGI table configured")
    errors: list[str] = []
    for table in tables:
        try:
            columns = bom_databricks._describe(conn, table)
        except DataSourceError as exc:
            if not _table_absent(exc):
                raise
            errors.append(f"{table}: {exc}")
            continue
        return bom_databricks._query(conn, build_cogi_sql(table, columns)), table
    raise DataSourceError("COGI table is not in the catalog. " + " | ".join(errors))


def load_open_sto_frame(conn: Any, tables: StockTables) -> pd.DataFrame:
    ekko_cols = bom_databricks._describe(conn, tables.ekko_table)
    ekpo_cols = bom_databricks._describe(conn, tables.ekpo_table)
    eket_cols = None
    eket_error: Optional[BaseException] = None
    try:
        eket_cols = bom_databricks._describe(conn, tables.eket_table)
    except DataSourceError as exc:
        if not _table_absent(exc):
            raise
        eket_error = exc
    try:
        sql = build_open_sto_sql(
            tables.ekko_table,
            tables.ekpo_table,
            ekko_cols,
            ekpo_cols,
            tables.eket_table if eket_cols is not None else None,
            eket_cols,
        )
    except DataSourceError as exc:
        if eket_cols is None:
            raise DataSourceError(
                f"{tables.eket_table} is not in the catalog ({eket_error}). {exc}"
            ) from exc
        raise
    return bom_databricks._query(conn, sql)


def _one(action: Any, empty: pd.DataFrame) -> tuple[pd.DataFrame, Optional[str]]:
    try:
        frame = action()
        if frame is None:
            return empty.copy(), None
        return frame, None
    except DataSourceError as exc:
        return empty.copy(), str(exc)
    except Exception as exc:
        return empty.copy(), str(exc)


def _failed_feeds(message: str) -> PageFeeds:
    return PageFeeds(
        bom=empty_bom(),
        bom_error=message,
        stock=empty_stock(),
        stock_error=message,
        cogi=empty_cogi(),
        cogi_error=message,
        sto=empty_sto(),
        sto_error=message,
        demo=False,
    )


def load_live_feeds(
    settings: Optional[DatabricksSettings] = None,
    tables: Optional[StockTables] = None,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
) -> PageFeeds:
    """One SSO connection. Stock is one batch_ts, optionally inside an India-time period."""
    resolved = settings or DatabricksSettings.resolve()
    stock_tables = tables or StockTables.resolve()
    connector = bom_databricks.databricks_sql
    if connector is None:
        return _failed_feeds(
            "databricks-sql-connector is not installed: pip install databricks-sql-connector"
        )
    try:
        connection = connector.connect(**databricks_connect_kwargs(resolved))
    except Exception as exc:
        return _failed_feeds(str(exc))
    with connection as conn:
        bom, bom_error = _one(lambda: load_bom_frame(conn, resolved), empty_bom())
        stock = empty_stock()
        stock_error = None
        stock_table = None
        try:
            stock, stock_table = load_stock_frame(
                conn, stock_tables.mard_tables, period_start, period_end
            )
        except DataSourceError as exc:
            stock_error = str(exc)
        except Exception as exc:
            stock_error = str(exc)
        cogi, cogi_error = _one(
            lambda: load_cogi_frame(conn, stock_tables.cogi_tables)[0], empty_cogi()
        )
        sto, sto_error = _one(lambda: load_open_sto_frame(conn, stock_tables), empty_sto())
    return PageFeeds(
        bom=bom,
        bom_error=bom_error,
        stock=stock,
        stock_error=stock_error,
        cogi=cogi,
        cogi_error=cogi_error,
        sto=sto,
        sto_error=sto_error,
        demo=False,
        stock_table=stock_table,
        stock_period=(
            f"{india_stamp(period_start)} to {india_stamp(period_end)} India time"
            if period_start and period_end
            else "live batch at the current time"
        ),
    )
