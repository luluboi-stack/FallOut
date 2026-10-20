"""SAP stock sources for the sequence walk: MB52, COGI, and open STO plus a snapshot id.

Databricks reads the sap.sapabap1 catalog. The live BOM is mast joined to stas
joined to stpo, so alternative 01 is the item nodes STAS assigns. The walk
explodes that frame. The file is the fallback only when STAS is missing or the
live query returns no rows.
"""
import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

import pandas as pd

try:
    from databricks import sql as databricks_sql
except ImportError:
    databricks_sql = None

from picklist_engine import clean_sap_key, find_column_by_priority

SOURCE_SAP_UPLOAD = "SAP upload"
SOURCE_DATABRICKS = "Databricks"
SOURCE_OPTIONS = [SOURCE_SAP_UPLOAD, SOURCE_DATABRICKS]

CATALOG = "sap.sapabap1"

DEFAULT_DATABRICKS_HOST = "8259552823336828.8.gcp.databricks.com"
DEFAULT_DATABRICKS_HTTP_PATH = "/sql/1.0/warehouses/823716c4-00e1-47a9-b328-5fa943653443"

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
    "mb52_table": f"{CATALOG}.nsdm_v_mard_hourly",
    "cogi_table": f"{CATALOG}.affw_hourly",
    "cogi_fallback_table": f"{CATALOG}.affw",
    "ekko_table": f"{CATALOG}.ekko",
    "ekpo_table": f"{CATALOG}.ekpo",
    "eket_table": f"{CATALOG}.eket",
    "mast_table": f"{CATALOG}.mast",
    "stpo_table": f"{CATALOG}.stpo",
    "stas_table": f"{CATALOG}.stas",
}

DATABRICKS_TABLE_ENV = {
    "mb52_table": "DATABRICKS_MB52_TABLE",
    "cogi_table": "DATABRICKS_COGI_TABLE",
    "cogi_fallback_table": "DATABRICKS_COGI_FALLBACK_TABLE",
    "ekko_table": "DATABRICKS_EKKO_TABLE",
    "ekpo_table": "DATABRICKS_EKPO_TABLE",
    "eket_table": "DATABRICKS_EKET_TABLE",
    "mast_table": "DATABRICKS_MAST_TABLE",
    "stpo_table": "DATABRICKS_STPO_TABLE",
    "stas_table": "DATABRICKS_STAS_TABLE",
}

STAS_BOM_MESSAGE = "Alternative 01 from STAS is the live BOM."

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TIMESTAMP_CANDIDATES = (
    "loaded_at",
    "load_timestamp",
    "ingestion_timestamp",
    "ingest_timestamp",
    "snapshot_ts",
    "snapshot_time",
    "updated_at",
    "update_ts",
    "etl_load_ts",
    "dh_load_ts",
    "_loaded_at",
    "event_time",
    "ts",
)


class DataSourceError(RuntimeError):
    pass


class DataSourceNotConfigured(DataSourceError):
    pass


@dataclass(frozen=True)
class SapSnapshot:
    mb52: pd.DataFrame
    cogi: pd.DataFrame
    sto: pd.DataFrame
    snapshot_id: str
    source: str

    @property
    def is_complete(self) -> bool:
        """A picklist is only logged once MB52, COGI, and STO are all loaded."""
        return not (self.mb52.empty or self.cogi.empty or self.sto.empty)


@dataclass(frozen=True)
class BomLoad:
    """Single-level BOM plus whether the walk may replace the file with it."""

    frame: pd.DataFrame
    accepted: bool
    reason: str


def frame_fingerprint(*frames: Optional[pd.DataFrame]) -> str:
    h = hashlib.sha256()
    for df in frames:
        if df is None or df.empty:
            h.update(b"<empty>")
            continue
        h.update("|".join(map(str, df.columns)).encode("utf-8"))
        h.update(pd.util.hash_pandas_object(df.astype(str), index=False).values.tobytes())
    return h.hexdigest()[:16]


class SapUploadSource:
    """Frames already parsed from the sidebar uploads. Snapshot id is a content hash."""

    name = SOURCE_SAP_UPLOAD

    def __init__(self, mb52: pd.DataFrame, cogi: pd.DataFrame, sto: pd.DataFrame, lx02: Optional[pd.DataFrame] = None):
        self._mb52 = mb52 if mb52 is not None else pd.DataFrame()
        self._cogi = cogi if cogi is not None else pd.DataFrame()
        self._sto = sto if sto is not None else pd.DataFrame()
        self._lx02 = lx02 if lx02 is not None else pd.DataFrame()

    def load(self) -> SapSnapshot:
        snapshot_id = "sap_" + frame_fingerprint(self._mb52, self._cogi, self._sto, self._lx02)
        return SapSnapshot(self._mb52, self._cogi, self._sto, snapshot_id, self.name)


@dataclass(frozen=True)
class DatabricksSettings:
    host: str
    http_path: str
    token: str
    mb52_table: str
    cogi_table: str
    cogi_fallback_table: str
    ekko_table: str
    ekpo_table: str
    eket_table: str
    mast_table: str
    stpo_table: str
    stas_table: str

    @classmethod
    def resolve(
        cls,
        secrets: Optional[Mapping[str, Any]] = None,
        environ: Optional[Mapping[str, str]] = None,
    ) -> "DatabricksSettings":
        """Host and HTTP path default to the asia-south1 warehouse. A token is optional.

        An empty token means browser SSO. Table names fall back to sap.sapabap1.
        """
        environ = os.environ if environ is None else environ
        secrets = secrets or {}
        values = {}
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

    @staticmethod
    def missing(secrets: Optional[Mapping[str, Any]] = None, environ: Optional[Mapping[str, str]] = None):
        """Nothing is required. Host and HTTP path have defaults, and SSO covers a missing token."""
        return []


def _databricks_hostname(value: str) -> str:
    host = str(value).strip()
    host = re.sub(r"^https?://", "", host, flags=re.IGNORECASE)
    return host.strip("/")


DATABRICKS_REFRESH_SECONDS = 60 * 60


def databricks_cache_key(host: str, http_path: str) -> str:
    return f"{host}|{http_path}"


def should_refresh_databricks(
    cached_key: Optional[str],
    cached_at: Optional[float],
    now: float,
    cache_key: str,
    force: bool,
    max_age_seconds: float = DATABRICKS_REFRESH_SECONDS,
) -> bool:
    """True when the cache is empty, the warehouse changed, the user forced a sync, or the pull is older than 60 minutes."""
    if force:
        return True
    if not cached_key or cached_key != cache_key or cached_at is None:
        return True
    return (now - float(cached_at)) > max_age_seconds


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


def _sql_text(column: str) -> str:
    return f"TRIM(CAST({quote_column(column)} AS STRING))"


def _sql_number(column: str) -> str:
    return f"CAST({quote_column(column)} AS DOUBLE)"


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


def stock_snapshot_column(columns: Sequence[str]) -> Optional[str]:
    """Hourly stock keeps one load: batch_ts, or batch_id when the timestamp is absent."""
    return find_column(columns, "batch_ts") or find_column(columns, "batch_id")


def build_stock_sql(table: str, columns: Sequence[str]) -> str:
    material = _require_column(columns, "matnr", "material")
    plant = _require_column(columns, "werks", "plant")
    sloc = _require_column(columns, "lgort", "storage_location")
    qty = _require_column(columns, "labst", "unrestricted")
    filters = [
        f"UPPER({_sql_text(plant)}) IN ('M002', 'W004')",
        f"UPPER({_sql_text(sloc)}) IN ('RM01', 'PS01', 'PV01')",
    ]
    snapshot = stock_snapshot_column(columns)
    if snapshot:
        quoted = quote_column(snapshot)
        filters.append(f"{quoted} = (SELECT MAX({quoted}) FROM {quote_table(table)})")
    return f"""
        SELECT
            {_sql_text(material)} AS `Material`,
            {_sql_text(plant)} AS `Plant`,
            {_sql_text(sloc)} AS `Storage Location`,
            {_sql_number(qty)} AS `Unrestricted`
        FROM {quote_table(table)}
        WHERE {" AND ".join(filters)}
    """


def build_cogi_sql(table: str, columns: Sequence[str]) -> str:
    material = _require_column(columns, "matnr", "material")
    plant = _require_column(columns, "werks", "plant")
    qty = _require_column(columns, "erfmg", "menge", "qty")
    filters = [f"UPPER({_sql_text(plant)}) LIKE '%M002%'"]
    snapshot = stock_snapshot_column(columns)
    if snapshot:
        quoted = quote_column(snapshot)
        filters.append(f"{quoted} = (SELECT MAX({quoted}) FROM {quote_table(table)})")
    return f"""
        SELECT
            {_sql_text(material)} AS `Material`,
            {_sql_text(plant)} AS `Plant`,
            {_sql_number(qty)} AS `Qty in Unit of Entry`
        FROM {quote_table(table)}
        WHERE {" AND ".join(filters)}
    """


def build_sto_sql(
    ekko_table: str,
    ekpo_table: str,
    eket_table: str,
    ekko_columns: Sequence[str],
    ekpo_columns: Sequence[str],
    eket_columns: Sequence[str],
) -> str:
    """Open W004 to M002 quantity is scheduled quantity minus goods receipt."""
    ebeln_h = _require_column(ekko_columns, "ebeln")
    reswk = _require_column(ekko_columns, "reswk", "supplying_plant")
    ebeln_i = _require_column(ekpo_columns, "ebeln")
    ebelp_i = _require_column(ekpo_columns, "ebelp")
    material = _require_column(ekpo_columns, "matnr", "material")
    plant = _require_column(ekpo_columns, "werks", "plant")
    ebeln_s = _require_column(eket_columns, "ebeln")
    ebelp_s = _require_column(eket_columns, "ebelp")
    menge = _require_column(eket_columns, "menge")
    wemng = find_column(eket_columns, "wemng")
    scheduled = _aliased_number("e", menge)
    received = _aliased_number("e", wemng) if wemng else "0"
    open_qty = f"({scheduled} - COALESCE({received}, 0))"

    filters = [
        f"UPPER({_aliased_text('k', reswk)}) = 'W004'",
        f"UPPER({_aliased_text('p', plant)}) = 'M002'",
        f"{open_qty} > 0",
    ]
    header_loekz = find_column(ekko_columns, "loekz")
    item_loekz = find_column(ekpo_columns, "loekz")
    item_elikz = find_column(ekpo_columns, "elikz")
    if header_loekz:
        filters.append(f"({_aliased_text('k', header_loekz)} IS NULL OR {_aliased_text('k', header_loekz)} = '')")
    if item_loekz:
        filters.append(f"({_aliased_text('p', item_loekz)} IS NULL OR {_aliased_text('p', item_loekz)} = '')")
    if item_elikz:
        filters.append(f"({_aliased_text('p', item_elikz)} IS NULL OR {_aliased_text('p', item_elikz)} = '')")

    join_header = f"{_aliased_text('k', ebeln_h)} = {_aliased_text('p', ebeln_i)}"
    join_sched = (
        f"{_aliased_text('p', ebeln_i)} = {_aliased_text('e', ebeln_s)}"
        f" AND {_aliased_text('p', ebelp_i)} = {_aliased_text('e', ebelp_s)}"
    )
    mandt_h = find_column(ekko_columns, "mandt")
    mandt_i = find_column(ekpo_columns, "mandt")
    mandt_s = find_column(eket_columns, "mandt")
    if mandt_h and mandt_i:
        join_header += f" AND {_aliased_text('k', mandt_h)} = {_aliased_text('p', mandt_i)}"
    if mandt_i and mandt_s:
        join_sched += f" AND {_aliased_text('p', mandt_i)} = {_aliased_text('e', mandt_s)}"

    return f"""
        SELECT
            {_aliased_text('p', material)} AS `Material`,
            {open_qty} AS `Quantity`,
            {_aliased_text('k', ebeln_h)} AS `Purchasing Document`,
            {_aliased_text('p', plant)} AS `Plant`,
            {_aliased_text('k', reswk)} AS `Supplying Plant`
        FROM {quote_table(ekko_table)} k
        INNER JOIN {quote_table(ekpo_table)} p
            ON {join_header}
        INNER JOIN {quote_table(eket_table)} e
            ON {join_sched}
        WHERE {" AND ".join(filters)}
    """


def _qualified(alias: str, column: str) -> str:
    return f"{alias}.{quote_column(column)}"


def _aliased_text(alias: str, column: str) -> str:
    return f"TRIM(CAST({_qualified(alias, column)} AS STRING))"


def _aliased_number(alias: str, column: str) -> str:
    return f"CAST({_qualified(alias, column)} AS DOUBLE)"


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


def build_snapshot_sql(tables: Sequence[tuple[str, Sequence[str]]]) -> str:
    parts = []
    for table, columns in tables:
        stamp = find_column(columns, *_TIMESTAMP_CANDIDATES)
        if stamp is None:
            continue
        parts.append(
            f"SELECT MAX(CAST({quote_column(stamp)} AS STRING)) AS ts FROM {quote_table(table)}"
        )
    if not parts:
        return "SELECT date_format(current_timestamp(), 'yyyy-MM-dd HH:mm:ss') AS ts"
    union = " UNION ALL ".join(parts)
    return f"SELECT MAX(ts) AS ts FROM ({union}) loads"


def _column_named(frame: pd.DataFrame, *candidates: str) -> Optional[str]:
    return find_column_by_priority(frame, list(candidates))


def _pair_sets(frame: pd.DataFrame, level_one_only: bool) -> dict[str, dict[str, float]]:
    if frame is None or frame.empty:
        return {}
    parent_col = _column_named(frame, "parent material", "parent")
    child_col = _column_named(frame, "child material", "component")
    qty_col = _column_named(frame, "comp qty", "quantity", "qty", "menge")
    if not parent_col or not child_col:
        return {}
    work = frame
    if level_one_only:
        level_col = _column_named(frame, "explosion level", "level")
        if level_col:
            level = work[level_col].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
            work = work[level.isin(["1"])]
    pairs: dict[str, dict[str, float]] = {}
    qty_series = (
        pd.to_numeric(work[qty_col].astype(str).str.replace(",", ""), errors="coerce")
        if qty_col
        else pd.Series(1.0, index=work.index)
    )
    for idx, row in work.iterrows():
        parent = clean_sap_key(row[parent_col])
        child = clean_sap_key(row[child_col])
        if not parent or not child:
            continue
        qty = float(qty_series.loc[idx]) if pd.notna(qty_series.loc[idx]) else 0.0
        pairs.setdefault(parent, {})
        pairs[parent][child] = pairs[parent].get(child, 0.0) + qty
    return pairs


def accept_stas_bom(live: pd.DataFrame) -> BomLoad:
    """STAS alternative 01 is the source of truth. Extra children are kept."""
    if live is None or live.empty:
        return BomLoad(
            pd.DataFrame(columns=["Parent Material", "Child Material", "Comp Qty."]),
            False,
            "Live BOM query returned no rows. Staying on the file.",
        )
    return BomLoad(live, True, STAS_BOM_MESSAGE)


def _bom_labels(frame: pd.DataFrame, level_one_only: bool) -> tuple[dict, dict]:
    parent_col = _column_named(frame, "parent material", "parent")
    child_col = _column_named(frame, "child material", "component")
    parents: dict[str, str] = {}
    children: dict[tuple[str, str], str] = {}
    if not parent_col or not child_col or frame is None or frame.empty:
        return parents, children
    work = frame
    if level_one_only:
        level_col = _column_named(frame, "explosion level", "level")
        if level_col:
            level = work[level_col].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
            work = work[level.isin(["1"])]
    for _, row in work.iterrows():
        parent = clean_sap_key(row[parent_col])
        child = clean_sap_key(row[child_col])
        if not parent or not child:
            continue
        parents.setdefault(parent, str(row[parent_col]).strip())
        children.setdefault((parent, child), str(row[child_col]).strip())
    return parents, children


def assess_bom_against_reference(live: pd.DataFrame, reference: pd.DataFrame) -> BomLoad:
    """Overlap-trim used only when STAS is missing or the STAS query is empty.

    Shared parents keep live children that are also reference level-1 links, with the
    live quantity. A shared parent with no overlap keeps the file level-1 rows.
    Parents only in the file stay on those level-1 rows. Live-only parents are dropped.
    """
    columns = ["Parent Material", "Child Material", "Comp Qty."]
    if live is None or live.empty:
        return BomLoad(pd.DataFrame(columns=columns), False, "Live BOM query returned no rows. Staying on the file.")
    if reference is None or reference.empty:
        return BomLoad(pd.DataFrame(columns=columns), False, "Reference BOM is missing, so the live BOM was not switched on.")

    live_pairs = _pair_sets(live, level_one_only=False)
    file_pairs = _pair_sets(reference, level_one_only=True)
    live_parents, live_children = _bom_labels(live, False)
    file_parents, file_children = _bom_labels(reference, True)
    rows = []
    for parent in sorted(file_pairs):
        file_set = file_pairs[parent]
        live_set = live_pairs.get(parent)
        if live_set:
            overlap = {child: live_set[child] for child in file_set if child in live_set}
        else:
            overlap = {}
        if overlap:
            labels_parent = live_parents
            labels_child = live_children
            chosen = overlap
        else:
            labels_parent = file_parents
            labels_child = file_children
            chosen = file_set
        for child, qty in sorted(chosen.items()):
            rows.append({
                "Parent Material": labels_parent.get(parent, parent),
                "Child Material": labels_child.get((parent, child), child),
                "Comp Qty.": qty,
            })
    frame = pd.DataFrame(rows, columns=columns)
    if frame.empty:
        return BomLoad(frame, False, "Live BOM query returned no rows. Staying on the file.")
    return BomLoad(
        frame,
        True,
        "Live BOM trimmed to reference level-1 links because STAS was not available.",
    )


def load_reference_bom(base_dir: str) -> pd.DataFrame:
    """The CS12 file used to decide whether a live BOM is safe to switch on."""
    csv_path = os.path.join(base_dir, "bom.csv")
    if os.path.exists(csv_path):
        return pd.read_csv(csv_path, dtype=str, low_memory=False)
    for name in ("BOM_FINAL_EXPLODED (15th Oct).parquet", "BOM_FINAL_EXPLODED (16th Oct).parquet"):
        path = os.path.join(base_dir, name)
        if os.path.exists(path):
            return pd.read_parquet(path)
    return pd.DataFrame()


def _is_missing_table(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "not found" in text or "does not exist" in text or "table_or_view_not_found" in text


class DatabricksSource:
    """MB52, COGI, open STO, and a single-level BOM from sap.sapabap1.

    Frames use the column names the app's SAP parsers already find.
    Host defaults to 8259552823336828.8.gcp.databricks.com and the SQL warehouse
    in asia-south1. With no DATABRICKS_TOKEN, connect uses auth_type
    databricks-oauth so the Windows desktop browser signs in once and the
    connector caches that login. A declined login or a missing browser raises
    DataSourceError; the app shows it in the sidebar and stays on SAP uploads.
    """

    name = SOURCE_DATABRICKS

    def __init__(self, settings: Optional[DatabricksSettings]):
        self.settings = settings

    @property
    def configured(self) -> bool:
        return self.settings is not None

    def load(self) -> SapSnapshot:
        return self._using(self._load_connected)

    def read_bom(self, reference: pd.DataFrame) -> BomLoad:
        return self._using(lambda conn: self._bom_connected(conn, reference))

    def pull(self, reference: pd.DataFrame) -> tuple[SapSnapshot, BomLoad]:
        def both(conn):
            return self._load_connected(conn), self._bom_connected(conn, reference)

        return self._using(both)

    def _using(self, fn):
        self._ensure_ready()
        try:
            with self._connect() as conn:
                return fn(conn)
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(str(exc)) from exc

    def _ensure_ready(self) -> None:
        if self.settings is None:
            raise DataSourceNotConfigured(
                "Databricks is not configured. Set " + ", ".join(DATABRICKS_SETTINGS.values())
                + " (or a [databricks] block in .streamlit/secrets.toml)."
            )
        if databricks_sql is None:
            raise DataSourceError("databricks-sql-connector is not installed: pip install databricks-sql-connector")

    def _connect(self):
        try:
            return databricks_sql.connect(**databricks_connect_kwargs(self.settings))
        except Exception as exc:
            raise DataSourceError(str(exc)) from exc

    def _load_connected(self, conn) -> SapSnapshot:
        s = self.settings
        mb52_cols = self._describe(conn, s.mb52_table)
        cogi_table, cogi_cols = self._cogi_columns(conn)
        ekko_cols = self._describe(conn, s.ekko_table)
        ekpo_cols = self._describe(conn, s.ekpo_table)
        eket_cols = self._describe(conn, s.eket_table)
        mb52 = self._query(conn, build_stock_sql(s.mb52_table, mb52_cols))
        cogi = self._query(conn, build_cogi_sql(cogi_table, cogi_cols))
        sto = self._query(conn, build_sto_sql(s.ekko_table, s.ekpo_table, s.eket_table, ekko_cols, ekpo_cols, eket_cols))
        loaded_at = self._snapshot_token(conn, [
            (s.mb52_table, mb52_cols),
            (cogi_table, cogi_cols),
            (s.ekko_table, ekko_cols),
        ])
        return SapSnapshot(mb52, cogi, sto, f"dbx_{loaded_at}", self.name)

    def _bom_connected(self, conn, reference: pd.DataFrame) -> BomLoad:
        s = self.settings
        mast_cols = self._describe(conn, s.mast_table)
        stpo_cols = self._describe(conn, s.stpo_table)
        stas_cols = self._describe_stas(conn)
        if stas_cols:
            frame = self._query(
                conn,
                build_bom_sql(
                    s.mast_table, s.stpo_table, mast_cols, stpo_cols, s.stas_table, stas_cols
                ),
            )
            if frame is not None and not frame.empty:
                return accept_stas_bom(frame)
        frame = self._query(conn, build_bom_sql(s.mast_table, s.stpo_table, mast_cols, stpo_cols))
        return assess_bom_against_reference(frame, reference)

    def _describe_stas(self, conn) -> Optional[list[str]]:
        try:
            return self._describe(conn, self.settings.stas_table)
        except Exception as exc:
            if _is_missing_table(exc):
                return None
            raise

    def _cogi_columns(self, conn) -> tuple[str, list[str]]:
        s = self.settings
        try:
            return s.cogi_table, self._describe(conn, s.cogi_table)
        except Exception as exc:
            if not _is_missing_table(exc):
                raise
            return s.cogi_fallback_table, self._describe(conn, s.cogi_fallback_table)

    def _describe(self, conn, table: str) -> list[str]:
        frame = self._query(conn, f"DESCRIBE TABLE {quote_table(table)}")
        if frame.empty:
            raise DataSourceError(f"DESCRIBE returned no columns for {table}")
        names = []
        for raw in frame.iloc[:, 0].astype(str):
            name = raw.strip()
            if not name or name.startswith("#"):
                continue
            if name.lower() in {"col_name", "partition information"}:
                continue
            names.append(name)
        if not names:
            raise DataSourceError(f"DESCRIBE returned no columns for {table}")
        return names

    def _snapshot_token(self, conn, tables: Sequence[tuple[str, Sequence[str]]]) -> str:
        frame = self._query(conn, build_snapshot_sql(tables))
        if frame.empty or frame.iloc[0, 0] is None or str(frame.iloc[0, 0]).strip().lower() in {"", "none", "nat"}:
            return "unknown"
        token = str(frame.iloc[0, 0]).strip()
        return re.sub(r"[^0-9A-Za-z_]+", "", token)[:40] or "unknown"

    def _query(self, conn, sql: str) -> pd.DataFrame:
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
