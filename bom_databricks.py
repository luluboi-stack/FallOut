"""Databricks connection and the live BOM query. BOM only.

The SQL is ``build_bom_sql`` from ``data_sources.py`` at commit 49caa6f
(branch ``cursor/live-sap-bom-d6bc``). Same mast → stas → stpo join.
This module does not import the picklist app.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

import pandas as pd

try:
    from databricks import sql as databricks_sql
except ImportError:
    databricks_sql = None

CATALOG_SCHEMA = "sap.sapabap1"
HOST = "8259552823336828.8.gcp.databricks.com"
HTTP_PATH = "/sql/1.0/warehouses/96e0a2eb4e9f9e8b"
AUTH_TYPE = "databricks-oauth"

MAST_TABLE = f"{CATALOG_SCHEMA}.mast"
STAS_TABLE = f"{CATALOG_SCHEMA}.stas"
STPO_TABLE = f"{CATALOG_SCHEMA}.stpo"

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class BomQueryError(RuntimeError):
    pass


def quote_table(name: str) -> str:
    parts = [part.strip() for part in str(name).split(".") if part.strip()]
    if not parts or any(_IDENT.match(part) is None for part in parts):
        raise BomQueryError(f"Unsafe table name: {name}")
    return ".".join(f"`{part}`" for part in parts)


def quote_column(name: str) -> str:
    if _IDENT.match(str(name)) is None:
        raise BomQueryError(f"Unsafe column name: {name}")
    return f"`{name}`"


def find_column(columns: Sequence[str], *candidates: str) -> Optional[str]:
    lookup = {str(col).lower(): str(col) for col in columns}
    for candidate in candidates:
        found = lookup.get(candidate.lower())
        if found:
            return found
    return None


def _require_column(columns: Sequence[str], *candidates: str) -> str:
    found = find_column(columns, *candidates)
    if found is None:
        raise BomQueryError(
            "Missing column " + " or ".join(candidates) + f" in {list(columns)}"
        )
    return found


def _qualified(alias: str, column: str) -> str:
    return f"{alias}.{quote_column(column)}"


def _aliased_text(alias: str, column: str) -> str:
    return f"TRIM(CAST({_qualified(alias, column)} AS STRING))"


def _aliased_number(alias: str, column: str) -> str:
    return f"CAST({_qualified(alias, column)} AS DOUBLE)"


def _date_text(alias: str, column: str) -> str:
    return f"CAST({_qualified(alias, column)} AS STRING)"


def _on_or_before_today(alias: str, column: str) -> str:
    """SAP dates arrive as yyyyMMdd strings or as real dates."""
    text = _aliased_text(alias, column)
    digits = f"REGEXP_REPLACE({_date_text(alias, column)}, '[^0-9]', '')"
    return f"""(
        ({text} IS NULL OR {text} = '')
        OR {text} = '00000000'
        OR (
            LENGTH({digits}) = 8
            AND TO_DATE({digits}, 'yyyyMMdd') <= CURRENT_DATE()
        )
        OR (
            LENGTH({digits}) <> 8
            AND CAST({_qualified(alias, column)} AS DATE) <= CURRENT_DATE()
        )
    )"""


def _on_or_after_today(alias: str, column: str) -> str:
    text = _aliased_text(alias, column)
    digits = f"REGEXP_REPLACE({_date_text(alias, column)}, '[^0-9]', '')"
    return f"""(
        ({text} IS NULL OR {text} = '')
        OR {text} IN ('00000000', '99991231', '9999-12-31')
        OR (
            LENGTH({digits}) = 8
            AND TO_DATE({digits}, 'yyyyMMdd') >= CURRENT_DATE()
        )
        OR (
            LENGTH({digits}) <> 8
            AND CAST({_qualified(alias, column)} AS DATE) >= CURRENT_DATE()
        )
    )"""


def _not_deleted(alias: str, columns: Sequence[str]) -> Optional[str]:
    """LKENZ empty or not X. A deletion flag of X drops the node."""
    column = find_column(columns, "lkenz")
    if column is None:
        return None
    text = _aliased_text(alias, column)
    return f"({text} IS NULL OR {text} = '' OR UPPER({text}) <> 'X')"


def build_bom_sql(
    mast_table: str,
    stpo_table: str,
    mast_columns: Sequence[str],
    stpo_columns: Sequence[str],
    stas_table: Optional[str] = None,
    stas_columns: Optional[Sequence[str]] = None,
) -> str:
    """Parent-to-child links for plant M002, production usage, alternative 01.

    With STAS, mast joins stas on STLNR + STLAL, and stas joins stpo on STLNR + STLKN.
    STASZ is not a STPO key unless STPO itself has that column.
    Description is a blank column, the same as the live query. STAS has DATUV and
    no DATUB; valid-to is selected only when STPO or MAST already has that column.
    """
    matnr = _require_column(mast_columns, "matnr")
    stlnr_m = _require_column(mast_columns, "stlnr")
    stlnr_p = _require_column(stpo_columns, "stlnr")
    idnrk = _require_column(stpo_columns, "idnrk")
    menge = _require_column(stpo_columns, "menge")
    werks = _require_column(mast_columns, "werks")
    stlan = _require_column(mast_columns, "stlan")
    stlal = _require_column(mast_columns, "stlal")
    use_stas = bool(stas_table and stas_columns)

    base = find_column(stpo_columns, "bmeng", "base_qty", "base_quantity") or find_column(
        mast_columns, "bmeng", "base_qty", "base_quantity"
    )
    if base and base in stpo_columns:
        base_sql = _aliased_number("p", base)
    elif base:
        base_sql = _aliased_number("m", base)
    else:
        base_sql = None
    qty_sql = _aliased_number("p", menge)
    if base_sql:
        qty_sql = f"CASE WHEN {base_sql} IS NULL OR {base_sql} = 0 THEN {qty_sql} ELSE {qty_sql} / {base_sql} END"

    def optional(alias: str, columns: Sequence[str], *candidates: str, default: str = "''") -> str:
        found = find_column(columns, *candidates)
        if found is None:
            return default
        return _aliased_text(alias, found)

    valid_to = find_column(stpo_columns, "datub", "valid_to", "validto") or find_column(
        mast_columns, "datub", "valid_to", "validto"
    )
    if valid_to and valid_to in stpo_columns:
        valid_to_sql = _aliased_text("p", valid_to)
    elif valid_to:
        valid_to_sql = _aliased_text("m", valid_to)
    else:
        valid_to_sql = "''"

    filters = [
        f"UPPER({_aliased_text('m', werks)}) = 'M002'",
        f"{_aliased_text('m', stlan)} = '1'",
        f"LPAD({_aliased_text('m', stlal)}, 2, '0') = '01'",
        f"{_aliased_text('p', idnrk)} <> ''",
    ]
    postp = find_column(stpo_columns, "postp")
    if postp:
        filters.append(f"UPPER({_aliased_text('p', postp)}) = 'L'")
    deleted_p = _not_deleted("p", stpo_columns)
    if deleted_p:
        filters.append(deleted_p)
    datuv_p = find_column(stpo_columns, "datuv")
    if datuv_p:
        filters.append(_on_or_before_today("p", datuv_p))
    if valid_to and valid_to in stpo_columns:
        filters.append(_on_or_after_today("p", valid_to))
    elif valid_to:
        filters.append(_on_or_after_today("m", valid_to))

    if use_stas:
        stlnr_a = _require_column(stas_columns, "stlnr")
        stlal_a = _require_column(stas_columns, "stlal")
        stlkn_a = _require_column(stas_columns, "stlkn")
        stlkn_p = _require_column(stpo_columns, "stlkn")
        mast_stas = (
            f"{_aliased_text('m', stlnr_m)} = {_aliased_text('a', stlnr_a)}"
            f" AND LPAD({_aliased_text('m', stlal)}, 2, '0') = LPAD({_aliased_text('a', stlal_a)}, 2, '0')"
        )
        mandt_m = find_column(mast_columns, "mandt")
        mandt_a = find_column(stas_columns, "mandt")
        mandt_p = find_column(stpo_columns, "mandt")
        if mandt_m and mandt_a:
            mast_stas += f" AND {_aliased_text('m', mandt_m)} = {_aliased_text('a', mandt_a)}"
        stas_stpo = (
            f"{_aliased_text('a', stlnr_a)} = {_aliased_text('p', stlnr_p)}"
            f" AND {_aliased_text('a', stlkn_a)} = {_aliased_text('p', stlkn_p)}"
        )
        if mandt_a and mandt_p:
            stas_stpo += f" AND {_aliased_text('a', mandt_a)} = {_aliased_text('p', mandt_p)}"
        stasz_a = find_column(stas_columns, "stasz")
        stasz_p = find_column(stpo_columns, "stasz")
        if stasz_a and stasz_p:
            stas_stpo += f" AND {_aliased_text('a', stasz_a)} = {_aliased_text('p', stasz_p)}"
        stlty = find_column(stas_columns, "stlty")
        if stlty:
            filters.append(f"UPPER({_aliased_text('a', stlty)}) = 'M'")
        deleted_a = _not_deleted("a", stas_columns)
        if deleted_a:
            filters.append(deleted_a)
        datuv_a = find_column(stas_columns, "datuv")
        if datuv_a:
            filters.append(_on_or_before_today("a", datuv_a))
        from_sql = f"""
        FROM {quote_table(mast_table)} m
        INNER JOIN {quote_table(stas_table)} a
            ON {mast_stas}
        INNER JOIN {quote_table(stpo_table)} p
            ON {stas_stpo}
        """
    else:
        join = f"{_aliased_text('m', stlnr_m)} = {_aliased_text('p', stlnr_p)}"
        mandt_m = find_column(mast_columns, "mandt")
        mandt_p = find_column(stpo_columns, "mandt")
        if mandt_m and mandt_p:
            join += f" AND {_aliased_text('m', mandt_m)} = {_aliased_text('p', mandt_p)}"
        stlal_p = find_column(stpo_columns, "stlal")
        if stlal_p:
            join += f" AND LPAD({_aliased_text('m', stlal)}, 2, '0') = LPAD({_aliased_text('p', stlal_p)}, 2, '0')"
        from_sql = f"""
        FROM {quote_table(mast_table)} m
        INNER JOIN {quote_table(stpo_table)} p
            ON {join}
        """

    return f"""
        SELECT
            {_aliased_text('m', matnr)} AS `Parent Material`,
            {_aliased_text('m', werks)} AS `Plant`,
            {_aliased_text('m', stlan)} AS `BOM Usage`,
            LPAD({_aliased_text('m', stlal)}, 2, '0') AS `Alt. BOM`,
            {optional('p', stpo_columns, 'posnr')} AS `BOM Item Number`,
            {_aliased_text('p', idnrk)} AS `Child Material`,
            '' AS `Description`,
            {qty_sql} AS `Comp Qty.`,
            {optional('p', stpo_columns, 'meins')} AS `UoM`,
            {optional('p', stpo_columns, 'lgort')} AS `Prod Sloc`,
            {optional('p', stpo_columns, 'rgekz')} AS `Back Flush`,
            {optional('p', stpo_columns, 'datuv')} AS `valid From`,
            {valid_to_sql} AS `Valid To`
        {from_sql}
        WHERE {" AND ".join(filters)}
    """


def connect():
    """SSO only. No personal access token is read or sent."""
    if databricks_sql is None:
        raise BomQueryError(
            "databricks-sql-connector is not installed: pip install databricks-sql-connector"
        )
    try:
        return databricks_sql.connect(
            server_hostname=HOST,
            http_path=HTTP_PATH,
            auth_type=AUTH_TYPE,
        )
    except Exception as exc:
        raise BomQueryError(str(exc)) from exc


def _describe(conn, table: str) -> list[str]:
    frame = _query(conn, f"DESCRIBE TABLE {quote_table(table)}")
    if frame.empty:
        raise BomQueryError(f"DESCRIBE returned no columns for {table}")
    names = []
    for raw in frame.iloc[:, 0].astype(str):
        name = raw.strip()
        if not name or name.startswith("#"):
            continue
        if name.lower() in {"col_name", "partition information"}:
            continue
        names.append(name)
    if not names:
        raise BomQueryError(f"DESCRIBE returned no columns for {table}")
    return names


def _query(conn, sql: str) -> pd.DataFrame:
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        rows = cursor.fetchall()
        description = cursor.description or []
        columns = [col[0] for col in description]
    except Exception as exc:
        raise BomQueryError(str(exc)) from exc
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()
    if not columns:
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame.from_records(list(rows), columns=columns)


def fetch_live_bom() -> pd.DataFrame:
    """Run the STAS alternative-01 BOM against Databricks. Never reads the local sample."""
    with connect() as conn:
        mast_columns = _describe(conn, MAST_TABLE)
        stpo_columns = _describe(conn, STPO_TABLE)
        stas_columns = _describe(conn, STAS_TABLE)
        sql = build_bom_sql(
            MAST_TABLE,
            STPO_TABLE,
            mast_columns,
            stpo_columns,
            STAS_TABLE,
            stas_columns,
        )
        return _query(conn, sql)
