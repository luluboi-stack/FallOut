import pandas as pd
import pytest

from data_sources import (
    DATABRICKS_SETTINGS,
    DATABRICKS_TABLE_DEFAULTS,
    DEFAULT_DATABRICKS_HOST,
    DEFAULT_DATABRICKS_HTTP_PATH,
    BomLoad,
    DatabricksSettings,
    DatabricksSource,
    DataSourceError,
    DataSourceNotConfigured,
    SapUploadSource,
    assess_bom_against_reference,
    build_bom_sql,
    build_cogi_sql,
    build_snapshot_sql,
    build_stock_sql,
    build_sto_sql,
    databricks_connect_kwargs,
    quote_table,
    stock_snapshot_column,
)


MAST_COLS = ["mandt", "matnr", "werks", "stlan", "stlal", "stlnr"]
STPO_COLS = ["mandt", "stlnr", "stlal", "idnrk", "menge", "meins", "posnr", "lgort", "rgekz", "datuv", "lkenz", "postp"]
STOCK_COLS = ["matnr", "werks", "lgort", "labst", "loaded_at"]
COGI_COLS = ["matnr", "werks", "erfmg", "loaded_at"]
EKKO_COLS = ["mandt", "ebeln", "reswk", "loekz"]
EKPO_COLS = ["mandt", "ebeln", "ebelp", "matnr", "werks", "loekz", "elikz"]
EKET_COLS = ["mandt", "ebeln", "ebelp", "menge", "wemng"]

TABLES = {
    "nsdm_v_mard_hourly": STOCK_COLS,
    "affw_hourly": COGI_COLS,
    "affw": COGI_COLS,
    "ekko": EKKO_COLS,
    "ekpo": EKPO_COLS,
    "eket": EKET_COLS,
    "mast": MAST_COLS,
    "stpo": STPO_COLS,
}

BOM_DESCRIPTION = [
    "Parent Material", "Plant", "BOM Usage", "Alt. BOM", "BOM Item Number",
    "Child Material", "Description", "Comp Qty.", "UoM", "Prod Sloc",
    "Back Flush", "valid From", "Valid To",
]


def test_sap_upload_snapshot_id_tracks_content():
    mb52 = pd.DataFrame({"Material": ["A"], "Unrestricted": ["10"]})
    a = SapUploadSource(mb52, pd.DataFrame(), pd.DataFrame()).load()
    b = SapUploadSource(mb52.copy(), pd.DataFrame(), pd.DataFrame()).load()
    c = SapUploadSource(mb52.assign(Unrestricted=["11"]), pd.DataFrame(), pd.DataFrame()).load()
    assert a.snapshot_id == b.snapshot_id
    assert a.snapshot_id != c.snapshot_id
    assert a.mb52 is mb52


def test_databricks_settings_default_warehouse_and_optional_token():
    settings = DatabricksSettings.resolve({}, environ={})
    assert settings.host == DEFAULT_DATABRICKS_HOST
    assert settings.http_path == DEFAULT_DATABRICKS_HTTP_PATH
    assert settings.token == ""
    assert settings.mb52_table == DATABRICKS_TABLE_DEFAULTS["mb52_table"]
    assert settings.cogi_table.endswith("affw_hourly")
    assert settings.ekko_table.endswith("ekko")
    assert settings.ekpo_table.endswith("ekpo")
    assert settings.eket_table.endswith("eket")
    assert settings.mast_table.endswith("mast")
    assert settings.stpo_table.endswith("stpo")
    assert DatabricksSettings.missing({"host": "h"}, environ={}) == []
    overridden = DatabricksSettings.resolve({}, environ={
        "DATABRICKS_HOST": "https://other.cloud.databricks.com/",
        "DATABRICKS_HTTP_PATH": "/sql/1.0/warehouses/abc",
        "DATABRICKS_TOKEN": "dapi-test",
    })
    assert overridden.host == "other.cloud.databricks.com"
    assert overridden.http_path == "/sql/1.0/warehouses/abc"
    assert overridden.token == "dapi-test"


def test_connect_uses_browser_sso_unless_a_token_is_set():
    oauth = databricks_connect_kwargs(DatabricksSettings.resolve({}, environ={}))
    assert oauth["server_hostname"] == DEFAULT_DATABRICKS_HOST
    assert oauth["http_path"] == DEFAULT_DATABRICKS_HTTP_PATH
    assert oauth["auth_type"] == "databricks-oauth"
    assert "access_token" not in oauth
    pat = databricks_connect_kwargs(DatabricksSettings.resolve({}, environ={"DATABRICKS_TOKEN": "dapi-test"}))
    assert pat["access_token"] == "dapi-test"
    assert "auth_type" not in pat


def test_declined_browser_login_is_a_data_source_error():
    source = DatabricksSource(DatabricksSettings.resolve({}, environ={}))

    def declined():
        raise RuntimeError("browser declined")

    source._connect = declined
    with pytest.raises(DataSourceError, match="browser declined"):
        source.pull(pd.DataFrame())


def test_databricks_source_without_settings_is_not_configured():
    src = DatabricksSource(None)
    assert not src.configured
    with pytest.raises(DataSourceNotConfigured):
        src.load()


def test_table_names_reject_sql():
    with pytest.raises(DataSourceError):
        quote_table("sap.sapabap1.mast; drop table mast")


def test_stock_sql_splits_plants_and_storage_locations():
    sql = build_stock_sql("sap.sapabap1.nsdm_v_mard_hourly", STOCK_COLS)
    assert "`Material`" in sql and "`Unrestricted`" in sql
    assert "'M002'" in sql and "'W004'" in sql
    assert "'RM01'" in sql and "'PS01'" in sql and "'PV01'" in sql
    assert "`labst`" in sql


def test_stock_frame_keeps_the_latest_batch_timestamp_only():
    columns = ["matnr", "werks", "lgort", "labst", "batch_id", "batch_ts"]
    sql = build_stock_sql("sap.sapabap1.nsdm_v_mard_hourly", columns)
    assert (
        "`batch_ts` = (SELECT MAX(`batch_ts`) FROM `sap`.`sapabap1`.`nsdm_v_mard_hourly`)"
        in sql
    )
    assert "MAX(`batch_id`)" not in sql
    assert "'M002'" in sql and "'W004'" in sql
    assert "'RM01'" in sql and "'PS01'" in sql and "'PV01'" in sql
    assert "`labst`" in sql
    assert "insme" not in sql.lower() and "speme" not in sql.lower()

    frame = pd.DataFrame({
        "batch_ts": ["2026-10-18 08:00:00", "2026-10-18 09:00:00"],
        "batch_id": [1, 2],
        "Unrestricted": [416399, 100],
    })
    stamp = stock_snapshot_column(columns)
    latest = frame[frame[stamp] == frame[stamp].max()]
    assert latest["Unrestricted"].sum() == 100
    assert frame["Unrestricted"].sum() != latest["Unrestricted"].sum()

    id_only = ["matnr", "werks", "lgort", "labst", "batch_id"]
    id_sql = build_stock_sql("sap.sapabap1.nsdm_v_mard_hourly", id_only)
    assert (
        "`batch_id` = (SELECT MAX(`batch_id`) FROM `sap`.`sapabap1`.`nsdm_v_mard_hourly`)"
        in id_sql
    )


def test_cogi_sql_uses_entry_quantity_at_m002():
    sql = build_cogi_sql("sap.sapabap1.affw_hourly", COGI_COLS)
    assert "`Qty in Unit of Entry`" in sql
    assert "`erfmg`" in sql
    assert "M002" in sql


def test_cogi_frame_keeps_the_latest_batch_timestamp_only():
    columns = ["matnr", "werks", "lgort", "erfmg", "batch_id", "batch_ts"]
    sql = build_cogi_sql("sap.sapabap1.affw_hourly", columns)
    assert (
        "`batch_ts` = (SELECT MAX(`batch_ts`) FROM `sap`.`sapabap1`.`affw_hourly`)"
        in sql
    )
    assert "MAX(`batch_id`)" not in sql
    assert "M002" in sql
    assert "`erfmg`" in sql
    assert "msgty" not in sql.lower() and "shkzg" not in sql.lower()

    frame = pd.DataFrame({
        "batch_ts": ["2026-10-18 08:00:00", "2026-10-18 09:00:00"],
        "batch_id": [1, 2],
        "Qty in Unit of Entry": [40, 7],
    })
    stamp = stock_snapshot_column(columns)
    latest = frame[frame[stamp] == frame[stamp].max()]
    assert latest["Qty in Unit of Entry"].sum() == 7
    assert frame["Qty in Unit of Entry"].sum() != latest["Qty in Unit of Entry"].sum()

    id_only = ["matnr", "werks", "menge", "batch_id"]
    id_sql = build_cogi_sql("sap.sapabap1.affw_hourly", id_only)
    assert (
        "`batch_id` = (SELECT MAX(`batch_id`) FROM `sap`.`sapabap1`.`affw_hourly`)"
        in id_sql
    )
    assert "`menge`" in id_sql


def test_sto_sql_nets_goods_receipt_for_w004_to_m002():
    sql = build_sto_sql(
        "sap.sapabap1.ekko",
        "sap.sapabap1.ekpo",
        "sap.sapabap1.eket",
        EKKO_COLS,
        EKPO_COLS,
        EKET_COLS,
    )
    assert "`Quantity`" in sql and "`Purchasing Document`" in sql and "`Supplying Plant`" in sql
    assert "`menge`" in sql and "`wemng`" in sql
    assert "- COALESCE" in sql
    assert "'W004'" in sql and "'M002'" in sql
    assert "`loekz`" in sql and "`elikz`" in sql
    assert "> 0" in sql


def test_bom_sql_filters_production_alternative_and_stock_items():
    sql = build_bom_sql("sap.sapabap1.mast", "sap.sapabap1.stpo", MAST_COLS, STPO_COLS)
    assert "`Parent Material`" in sql and "`Child Material`" in sql and "`Comp Qty.`" in sql
    assert "'M002'" in sql
    assert "= '1'" in sql
    assert "'01'" in sql
    assert "`postp`" in sql and "'L'" in sql
    assert "`lkenz`" in sql
    assert "`datuv`" in sql
    assert "9999-12-31" in sql
    assert "p.`stlal`" in sql


def test_bom_sql_divides_by_base_quantity_when_present():
    sql = build_bom_sql(
        "sap.sapabap1.mast",
        "sap.sapabap1.stpo",
        MAST_COLS,
        STPO_COLS + ["bmeng"],
    )
    assert "`bmeng`" in sql
    assert "/" in sql


def test_snapshot_sql_uses_load_timestamp():
    sql = build_snapshot_sql([("sap.sapabap1.nsdm_v_mard_hourly", STOCK_COLS)])
    assert "`loaded_at`" in sql
    assert "MAX" in sql


def test_live_bom_accepted_when_level1_matches_and_subassembly_stays_below():
    reference = pd.DataFrame({
        "Parent Material": ["S450", "S450", "S450"],
        "Explosion Level": ["1", "1", "2"],
        "Child Material": ["KIT1", "PARTA", "INSIDE"],
        "Comp Qty.": ["1", "2", "4"],
    })
    live = pd.DataFrame({
        "Parent Material": ["S450", "S450", "KIT1"],
        "Child Material": ["KIT1", "PARTA", "INSIDE"],
        "Comp Qty.": ["1", "2", "4"],
    })
    result = assess_bom_against_reference(live, reference)
    assert result.accepted
    assert "S450" not in result.frame["Parent Material"].tolist() or "KIT1" in set(result.frame["Child Material"])


def test_live_bom_refused_when_alternatives_are_mixed():
    reference = pd.DataFrame({
        "Parent Material": ["S450", "S450"],
        "Explosion Level": ["1", "1"],
        "Child Material": ["KIT1", "PARTA"],
        "Comp Qty.": ["1", "2"],
    })
    live = pd.DataFrame({
        "Parent Material": ["S450"] * 20,
        "Child Material": [f"C{i}" for i in range(20)],
        "Comp Qty.": ["1"] * 20,
    })
    result = assess_bom_against_reference(live, reference)
    assert isinstance(result, BomLoad)
    assert not result.accepted
    assert "stas" in result.reason


def test_live_bom_refused_when_level1_components_differ():
    children = [f"P{i}" for i in range(5)]
    reference = pd.DataFrame({
        "Parent Material": ["S450"] * 5,
        "Explosion Level": ["1"] * 5,
        "Child Material": children,
        "Comp Qty.": ["1"] * 5,
    })
    live = pd.DataFrame({
        "Parent Material": ["S450"] * 5,
        "Child Material": [f"X{i}" for i in range(5)],
        "Comp Qty.": ["1"] * 5,
    })
    result = assess_bom_against_reference(live, reference)
    assert not result.accepted
    assert "components" in result.reason


def test_live_bom_refused_when_level1_quantities_disagree():
    parents = ["S450"] * 5
    children = [f"P{i}" for i in range(5)]
    reference = pd.DataFrame({
        "Parent Material": parents,
        "Explosion Level": ["1"] * 5,
        "Child Material": children,
        "Comp Qty.": ["1"] * 5,
    })
    live = pd.DataFrame({
        "Parent Material": parents,
        "Child Material": children,
        "Comp Qty.": ["9"] * 5,
    })
    result = assess_bom_against_reference(live, reference)
    assert not result.accepted
    assert "quantities" in result.reason


class RecordingCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = []
        self.rows = []

    def execute(self, sql):
        self.conn.sqls.append(sql)
        if sql.strip().upper().startswith("DESCRIBE"):
            table = sql.split(".")[-1].strip().strip("`")
            if table in self.conn.missing:
                raise RuntimeError(f"TABLE_OR_VIEW_NOT_FOUND: {table}")
            self.rows = [(col, "string", "") for col in self.conn.columns[table]]
            self.description = [("col_name",), ("data_type",), ("comment",)]
            return
        if " AS ts" in sql or "date_format" in sql:
            self.rows = [("2026-10-18 07:00:00",)]
            self.description = [("ts",)]
            return
        if "`Unrestricted`" in sql:
            self.rows = [("100", "M002", "RM01", 5.0), ("100", "W004", "RM01", 7.0), ("100", "M002", "PS01", 3.0)]
            self.description = [("Material",), ("Plant",), ("Storage Location",), ("Unrestricted",)]
            return
        if "`Qty in Unit of Entry`" in sql:
            self.rows = [("100", "M002", 2.0)]
            self.description = [("Material",), ("Plant",), ("Qty in Unit of Entry",)]
            return
        if "`Supplying Plant`" in sql:
            self.rows = [("100", 4.0, "4500001234", "M002", "W004")]
            self.description = [("Material",), ("Quantity",), ("Purchasing Document",), ("Plant",), ("Supplying Plant",)]
            return
        if "`Parent Material`" in sql:
            self.rows = [("S450", "M002", "1", "01", "0010", "KIT1", "", 1.0, "EA", "PS01", "", "20260101", "9999-12-31")]
            self.description = [(col,) for col in BOM_DESCRIPTION]
            return
        raise AssertionError(sql)

    def fetchall(self):
        return self.rows

    def close(self):
        return None


class RecordingConn:
    def __init__(self, missing=()):
        self.columns = TABLES
        self.missing = set(missing)
        self.sqls = []

    def cursor(self):
        return RecordingCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _connected_source(conn):
    settings = DatabricksSettings.resolve({}, environ={env: "x" for env in DATABRICKS_SETTINGS.values()})
    source = DatabricksSource(settings)
    source._connect = lambda: conn
    return source


def test_pull_returns_parser_columns_and_checked_bom():
    source = _connected_source(RecordingConn())
    reference = pd.DataFrame({
        "Parent Material": ["S450"],
        "Explosion Level": ["1"],
        "Child Material": ["KIT1"],
        "Comp Qty.": ["1"],
    })
    snapshot, bom = source.pull(reference)
    assert list(snapshot.mb52.columns) == ["Material", "Plant", "Storage Location", "Unrestricted"]
    assert set(snapshot.mb52["Plant"]) == {"M002", "W004"}
    assert list(snapshot.cogi.columns) == ["Material", "Plant", "Qty in Unit of Entry"]
    assert list(snapshot.sto.columns) == ["Material", "Quantity", "Purchasing Document", "Plant", "Supplying Plant"]
    assert snapshot.sto.iloc[0]["Quantity"] == 4.0
    assert snapshot.snapshot_id == "dbx_20261018070000"
    assert bom.accepted
    assert list(bom.frame.columns)[:2] == ["Parent Material", "Plant"]


def test_pull_executes_describe_and_filtered_sql():
    conn = RecordingConn()
    source = _connected_source(conn)
    reference = pd.DataFrame({
        "Parent Material": ["S450"],
        "Explosion Level": ["1"],
        "Child Material": ["KIT1"],
        "Comp Qty.": ["1"],
    })
    source.pull(reference)
    sql = "\n".join(conn.sqls)
    assert "DESCRIBE TABLE `sap`.`sapabap1`.`mast`" in sql
    assert "DESCRIBE TABLE `sap`.`sapabap1`.`stpo`" in sql
    assert "DESCRIBE TABLE `sap`.`sapabap1`.`nsdm_v_mard_hourly`" in sql
    assert "'M002'" in sql and "'W004'" in sql
    assert "`wemng`" in sql


def test_cogi_falls_back_to_affw_when_hourly_table_is_missing():
    conn = RecordingConn(missing={"affw_hourly"})
    source = _connected_source(conn)
    snapshot = source.load()
    assert not snapshot.cogi.empty
    sql = "\n".join(conn.sqls)
    assert "`affw_hourly`" in sql
    assert "DESCRIBE TABLE `sap`.`sapabap1`.`affw`" in sql
