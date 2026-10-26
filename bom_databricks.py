"""Live M002 BOM from Databricks: mast joined to stas joined to stpo.

Connection matches the picklist source: host and warehouse have defaults, and an
empty token uses auth_type databricks-oauth (browser SSO, no personal access token).
The SQL is the mast → stas → stpo builder from the live-BOM work, limited to the
BOM columns this page shows.

This warehouse is the one named for this standalone viewer:
    /sql/1.0/warehouses/96e0a2eb4e9f9e8b
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

import pandas as pd

try:
    from databricks import sql as databricks_sql
except ImportError:
    databricks_sql = None

CATALOG = "sap.sapabap1"

DEFAULT_DATABRICKS_HOST = "8259552823336828.8.gcp.databricks.com"
DEFAULT_DATABRICKS_HTTP_PATH = "/sql/1.0/warehouses/96e0a2eb4e9f9e8b"

DATABRICKS_SETTINGS = {
    "host": "DATABRICKS_HOST",
    "http_path": "DATABRICKS_HTTP_PATH",
    "token": "DATABRICKS_TOKEN",
}

DATABRICKS_CONNECTION_DEFAULTS = {
    "host": DEFAULT_DATABRICKS_HOST,
    "http_path": DEFAULT_DATABRICKS_HTTP_PATH,
    "token": "",
}

DATABRICKS_TABLE_DEFAULTS = {
    "mast_table": f"{CATALOG}.mast",
    "stpo_table": f"{CATALOG}.stpo",
    "stas_table": f"{CATALOG}.stas",
}

DATABRICKS_TABLE_ENV = {
    "mast_table": "DATABRICKS_MAST_TABLE",
    "stpo_table": "DATABRICKS_STPO_TABLE",
    "stas_table": "DATABRICKS_STAS_TABLE",
}

BOM_COLUMNS = [
    "Parent Material",
    "Plant",
    "BOM Usage",
    "Alt. BOM",
    "BOM Item Number",
    "Child Material",
    "Description",
    "Comp Qty.",
    "UoM",
    "Prod Sloc",
    "Back Flush",
    "valid From",
    "Valid To",
]

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DataSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatabricksSettings:
    host: str
    http_path: str
    token: str
    mast_table: str
    stpo_table: str
    stas_table: str

    @classmethod
    def resolve(
        cls,
        secrets: Optional[Mapping[str, Any]] = None,
        environ: Optional[Mapping[str, str]] = None,
    ) -> "DatabricksSettings":
        """Host and HTTP path default to the named warehouse. A token is optional.

        An empty token means browser SSO. Table names fall back to sap.sapabap1.
        """
        environ = os.environ if environ is None else environ
        secrets = secrets or {}
        values: dict[str, str] = {}
        for field_name, env_name in DATABRICKS_SETTINGS.items():
            raw = secrets.get(field_name)
            if raw is None or str(raw).strip() == "":
                raw = environ.get(env_name)
            if raw is None or str(raw).strip() == "":
                raw = DATABRICKS_CONNECTION_DEFAULTS[field_name]
            values[field_name] = str(raw).strip()
        values["host"] = _databricks_hostname(values["host"])
        for field_name, env_name in DATABRICKS_TABLE_ENV.items():
            override = secrets.get(field_name) or environ.get(env_name) or ""
            values[field_name] = str(override).strip() or DATABRICKS_TABLE_DEFAULTS[field_name]
        return cls(**values)


def _databricks_hostname(value: str) -> str:
    host = str(value).strip()
    host = re.sub(r"^https?://", "", host, flags=re.IGNORECASE)
    return host.strip("/")


def databricks_connect_kwargs(settings: DatabricksSettings) -> dict:
    """PAT when a token is set. Otherwise user-to-machine OAuth, which opens a browser once."""
    kwargs = {
        "server_hostname": settings.host,
        "http_path": settings.http_path,
    }
    if settings.token:
        kwargs["access_token"] = settings.token
    else:
        kwargs["auth_type"] = "databricks-oauth"
    return kwargs


def quote_table(name: str) -> str:
    parts = [part.strip() for part in str(name).split(".") if part.strip()]
    if not parts or any(_IDENT.match(part) is None for part in parts):
        raise DataSourceError(f"Unsafe table name: {name}")
    return ".".join(f"`{part}`" for part in parts)


def quote_column(name: str) -> str:
    if _IDENT.match(str(name)) is None:
        raise DataSourceError(f"Unsafe column name: {name}")
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
        raise DataSourceError(
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


def _parent_prefix_filter(material_sql: str, prefixes: Optional[Sequence[str]]) -> Optional[str]:
    """Limit a parent material to the given prefixes. None leaves the query unchanged."""
    if not prefixes:
        return None
    cleaned: list[str] = []
    for raw in prefixes:
        prefix = str(raw).strip().upper()
        if not prefix:
            continue
        if re.fullmatch(r"[A-Z0-9]+", prefix) is None:
            raise DataSourceError(f"Unsafe parent prefix: {raw}")
        cleaned.append(prefix)
    if not cleaned:
        return None
    body = f"UPPER(REGEXP_REPLACE({material_sql}, '^0+', ''))"
    return "(" + " OR ".join(f"{body} LIKE '{prefix}%'" for prefix in cleaned) + ")"


def build_bom_sql(
    mast_table: str,
    stpo_table: str,
    mast_columns: Sequence[str],
    stpo_columns: Sequence[str],
    stas_table: Optional[str] = None,
    stas_columns: Optional[Sequence[str]] = None,
    parent_prefixes: Optional[Sequence[str]] = None,
) -> str:
    """Parent-to-child links for plant M002, production usage, alternative 01.

    With STAS, mast joins stas on STLNR + STLAL, and stas joins stpo on STLNR + STLKN.
    MANDT is added when both sides have it. STASZ is not a STPO key unless STPO
    itself has that column. STAS has no DATUB; valid-to comes from stpo or mast.
    parent_prefixes limits the parent material. Omit it and the parent set is unchanged.
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

    prefix_sql = _parent_prefix_filter(_aliased_text("m", matnr), parent_prefixes)
    if prefix_sql:
        filters.append(prefix_sql)

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


def bom_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Columns the page shows. Missing description stays blank; names are not invented."""
    work = frame.copy() if frame is not None else pd.DataFrame()
    for column in BOM_COLUMNS:
        if column not in work.columns:
            work[column] = ""
    shown = work.loc[:, BOM_COLUMNS].copy()
    shown["Description"] = shown["Description"].fillna("").astype(str)
    return shown


def demo_fixture_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "bom_databricks_demo.csv")


def load_demo_bom(path: Optional[str] = None) -> pd.DataFrame:
    """Fixture rows for localhost. Does not open a warehouse connection."""
    csv_path = path or demo_fixture_path()
    frame = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    return bom_table(frame)


def _describe(conn, table: str) -> list[str]:
    frame = _query(conn, f"DESCRIBE TABLE {quote_table(table)}")
    if frame.empty:
        raise DataSourceError(f"DESCRIBE returned no columns for {table}")
    names = []
    for raw in frame.iloc[:, 0].astype(str):
        name = raw.strip()
        if not name or name.startswith("#"):
            continue
        lowered = name.lower()
        if lowered in {"col_name", "partition information"}:
            continue
        names.append(name)
    if not names:
        raise DataSourceError(f"DESCRIBE returned no columns for {table}")
    return names


def _query(conn, sql: str) -> pd.DataFrame:
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        rows = cursor.fetchall()
        description = cursor.description or []
        columns = [col[0] for col in description]
    except Exception as exc:
        raise DataSourceError(str(exc)) from exc
    finally:
        close = getattr(cursor, "close", None)
        if close:
            close()
    if not columns:
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame.from_records(list(rows), columns=columns)


def load_live_bom(
    settings: Optional[DatabricksSettings] = None,
    parent_prefixes: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """Describe mast, stas, and stpo, then return the direct parent-child links.

    parent_prefixes limits parents when passed. The BOM viewer omits it.
    """
    resolved = settings or DatabricksSettings.resolve()
    if databricks_sql is None:
        raise DataSourceError(
            "databricks-sql-connector is not installed: pip install databricks-sql-connector"
        )
    try:
        with databricks_sql.connect(**databricks_connect_kwargs(resolved)) as conn:
            mast_cols = _describe(conn, resolved.mast_table)
            stpo_cols = _describe(conn, resolved.stpo_table)
            stas_cols = _describe(conn, resolved.stas_table)
            sql = build_bom_sql(
                resolved.mast_table,
                resolved.stpo_table,
                mast_cols,
                stpo_cols,
                resolved.stas_table,
                stas_cols,
                parent_prefixes=parent_prefixes,
            )
            return bom_table(_query(conn, sql))
    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(str(exc)) from exc
