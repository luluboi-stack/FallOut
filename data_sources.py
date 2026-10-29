"""SAP stock sources for the sequence walk: MB52, COGI, and open STO plus a snapshot id."""
import hashlib
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import pandas as pd

try:
    from databricks import sql as databricks_sql
except ImportError:
    databricks_sql = None

SOURCE_SAP_UPLOAD = "SAP upload"
SOURCE_DATABRICKS = "Databricks"
SOURCE_OPTIONS = [SOURCE_SAP_UPLOAD, SOURCE_DATABRICKS]

DATABRICKS_SETTINGS = {
    "host": "DATABRICKS_HOST",
    "http_path": "DATABRICKS_HTTP_PATH",
    "token": "DATABRICKS_TOKEN",
    "mb52_table": "DATABRICKS_MB52_TABLE",
    "cogi_table": "DATABRICKS_COGI_TABLE",
    "sto_table": "DATABRICKS_STO_TABLE",
}


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
    sto_table: str

    @classmethod
    def resolve(
        cls,
        secrets: Optional[Mapping[str, Any]] = None,
        environ: Optional[Mapping[str, str]] = None,
    ) -> Optional["DatabricksSettings"]:
        """Reads `[databricks]` from st.secrets first, then DATABRICKS_* env vars. None when incomplete."""
        environ = os.environ if environ is None else environ
        secrets = secrets or {}
        values = {}
        for field_name, env_name in DATABRICKS_SETTINGS.items():
            val = secrets.get(field_name) or environ.get(env_name) or ""
            values[field_name] = str(val).strip()
        if not all(values.values()):
            return None
        return cls(**values)

    @staticmethod
    def missing(secrets: Optional[Mapping[str, Any]] = None, environ: Optional[Mapping[str, str]] = None):
        environ = os.environ if environ is None else environ
        secrets = secrets or {}
        return [
            env_name for field_name, env_name in DATABRICKS_SETTINGS.items()
            if not (secrets.get(field_name) or environ.get(env_name))
        ]


class DatabricksSource:
    """MB52, COGI, and STO from Databricks tables. Not live until the table schemas are shared.

    Frames must come back with the column names the app's SAP parsers already find
    (material, plant, storage location, unrestricted, ...), so the walk is unaware of the source.
    Snapshot id is the latest load timestamp across the three tables.
    """

    name = SOURCE_DATABRICKS

    def __init__(self, settings: Optional[DatabricksSettings]):
        self.settings = settings

    @property
    def configured(self) -> bool:
        return self.settings is not None

    def load(self) -> SapSnapshot:
        if self.settings is None:
            raise DataSourceNotConfigured(
                "Databricks is not configured. Set " + ", ".join(DATABRICKS_SETTINGS.values())
                + " (or a [databricks] block in .streamlit/secrets.toml)."
            )
        if databricks_sql is None:
            raise DataSourceError("databricks-sql-connector is not installed: pip install databricks-sql-connector")

        with self._connect() as conn:
            mb52 = self._read_mb52(conn)
            cogi = self._read_cogi(conn)
            sto = self._read_sto(conn)
            loaded_at = self._latest_load_timestamp(conn)
        return SapSnapshot(mb52, cogi, sto, f"dbx_{loaded_at}", self.name)

    def _connect(self):
        s = self.settings
        return databricks_sql.connect(
            server_hostname=s.host,
            http_path=s.http_path,
            access_token=s.token,
        )

    def _read_mb52(self, conn) -> pd.DataFrame:
        raise DataSourceError(f"Databricks MB52 query for {self.settings.mb52_table} is pending the table schema.")

    def _read_cogi(self, conn) -> pd.DataFrame:
        raise DataSourceError(f"Databricks COGI query for {self.settings.cogi_table} is pending the table schema.")

    def _read_sto(self, conn) -> pd.DataFrame:
        raise DataSourceError(f"Databricks STO query for {self.settings.sto_table} is pending the table schema.")

    def _latest_load_timestamp(self, conn) -> str:
        raise DataSourceError("Databricks load-timestamp column is pending the table schema.")
