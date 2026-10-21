"""The viewer SQL stays on the alt-01 mast → stas → stpo join."""

import inspect

import pandas as pd

from bom_databricks import (
    AUTH_TYPE,
    CATALOG_SCHEMA,
    HOST,
    HTTP_PATH,
    build_bom_sql,
    fetch_live_bom,
)
from bom_viewer import BLANK_DESCRIPTION, DISPLAY_COLUMNS, SAMPLE_BANNER, load_sample, shape_table

MAST_COLS = ["mandt", "matnr", "werks", "stlan", "stlal", "stlnr"]
STPO_COLS = [
    "mandt", "stlnr", "stlal", "stlkn", "idnrk", "menge", "meins",
    "posnr", "lgort", "rgekz", "datuv", "lkenz", "postp",
]
STAS_COLS = ["mandt", "stlty", "stlnr", "stlal", "stlkn", "stasz", "datuv", "lkenz"]


def _stas_sql(**extra_stpo: list[str]) -> str:
    stpo = list(STPO_COLS)
    for name in extra_stpo.get("stpo", []):
        stpo.append(name)
    return build_bom_sql(
        "sap.sapabap1.mast",
        "sap.sapabap1.stpo",
        MAST_COLS,
        stpo,
        "sap.sapabap1.stas",
        STAS_COLS,
    )


def test_connection_is_sso_on_the_named_warehouse():
    assert HOST == "8259552823336828.8.gcp.databricks.com"
    assert HTTP_PATH == "/sql/1.0/warehouses/96e0a2eb4e9f9e8b"
    assert CATALOG_SCHEMA == "sap.sapabap1"
    assert AUTH_TYPE == "databricks-oauth"
    source = inspect.getsource(fetch_live_bom)
    assert "bom_sample" not in source
    assert "access_token" not in inspect.getsource(__import__("bom_databricks").connect)


def test_stas_join_is_stlnr_stlal_then_stlnr_stlkn():
    sql = _stas_sql()
    assert "INNER JOIN `sap`.`sapabap1`.`stas` a" in sql
    assert "INNER JOIN `sap`.`sapabap1`.`stpo` p" in sql
    assert "m.`stlnr`" in sql and "a.`stlnr`" in sql and "a.`stlal`" in sql
    assert "a.`stlkn`" in sql and "p.`stlkn`" in sql
    assert "a.`mandt`" in sql and "p.`mandt`" in sql
    assert "a.`stasz`" not in sql
    assert "'M002'" in sql and "= '1'" in sql and "'01'" in sql
    assert "'L'" in sql and "`menge`" in sql and "<> 'X'" in sql
    assert "a.`datuv`" in sql
    assert "datub" not in sql.lower()
    assert "'' AS `Description`" in sql


def test_stasz_joins_only_when_stpo_has_the_column():
    sql = _stas_sql(stpo=["stasz"])
    assert "a.`stasz`" in sql and "p.`stasz`" in sql


def test_valid_to_comes_from_stpo_datub_when_present():
    sql = _stas_sql(stpo=["datub"])
    assert "p.`datub`" in sql
    assert ">= CURRENT_DATE()" in sql


def test_sample_table_labels_a_blank_description():
    assert "LOCAL SAMPLE" in SAMPLE_BANNER
    table = shape_table(load_sample())
    assert list(table.columns) == DISPLAY_COLUMNS
    assert len(table) == 3
    assert set(table["description"]) == {BLANK_DESCRIPTION}
    assert set(table["alt"]) == {"01"}
    assert table.loc[0, "parent"] == "PARENT-SAMPLE-1"
    assert table.loc[0, "valid to"] == ""
    assert table.loc[1, "valid to"] == "99991231"


def test_shape_keeps_a_description_when_the_query_has_one():
    frame = pd.DataFrame({
        "Parent Material": ["P"],
        "Child Material": ["C"],
        "BOM Item Number": ["0010"],
        "Comp Qty.": [2],
        "Alt. BOM": ["01"],
        "valid From": ["20240101"],
        "Valid To": [""],
        "Description": ["Motor cover"],
    })
    table = shape_table(frame)
    assert table.loc[0, "description"] == "Motor cover"
    assert table.loc[0, "qty"] == "2"
