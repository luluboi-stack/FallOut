"""The BOM table renders from the fixture with no warehouse connection."""

import sys

from streamlit.testing.v1 import AppTest

from bom_databricks import (
    BOM_COLUMNS,
    DEFAULT_DATABRICKS_HOST,
    DEFAULT_DATABRICKS_HTTP_PATH,
    DatabricksSettings,
    build_bom_sql,
    databricks_connect_kwargs,
    load_demo_bom,
)
from bom_databricks_view import demo_requested

MAST_COLS = ["mandt", "matnr", "werks", "stlan", "stlal", "stlnr"]
STPO_COLS = ["mandt", "stlnr", "stlkn", "idnrk", "menge", "postp", "posnr", "meins", "datuv", "lkenz"]
STAS_COLS = ["mandt", "stlty", "stlnr", "stlal", "stlkn", "stasz", "datuv", "lkenz"]


class _RefuseSql:
    @staticmethod
    def connect(*_args, **_kwargs):
        raise AssertionError("demo mode must not open a Databricks connection")


def test_demo_fixture_is_the_table_and_does_not_open_databricks(monkeypatch):
    monkeypatch.setattr("bom_databricks.databricks_sql", _RefuseSql)
    frame = load_demo_bom()
    assert list(frame.columns) == BOM_COLUMNS
    assert len(frame) == 3
    assert frame["Parent Material"].tolist() == ["DEMO-PARENT-1", "DEMO-PARENT-1", "DEMO-PARENT-2"]
    assert frame["Child Material"].tolist() == ["DEMO-CHILD-A", "DEMO-CHILD-B", "DEMO-CHILD-C"]
    assert set(frame["Description"]) == {""}
    assert set(frame["Plant"]) == {"M002"}


def test_demo_flag_is_the_switch():
    assert demo_requested(["bom_databricks_view.py", "--demo"], {})
    assert not demo_requested(["bom_databricks_view.py"], {})
    assert demo_requested(["bom_databricks_view.py"], {"BOM_VIEW_DEMO": "1"})


def test_streamlit_demo_renders_the_fixture_table(monkeypatch):
    monkeypatch.setattr("bom_databricks.databricks_sql", _RefuseSql)
    monkeypatch.setattr(sys, "argv", ["bom_databricks_view.py", "--demo"])
    app = AppTest.from_file("bom_databricks_view.py", default_timeout=30)
    app.run()
    assert not app.exception
    assert len(app.dataframe) == 1
    shown = app.dataframe[0].value
    assert list(shown.columns) == BOM_COLUMNS
    assert len(shown) == 3
    assert "DEMO-CHILD-A" in set(shown["Child Material"].astype(str))
    captions = [getattr(item, "value", "") for item in list(app.caption) + list(app.markdown)]
    assert any("3 component rows" in text for text in captions)


def test_oauth_defaults_match_the_named_warehouse():
    settings = DatabricksSettings.resolve(environ={})
    assert settings.host == DEFAULT_DATABRICKS_HOST
    assert settings.http_path == DEFAULT_DATABRICKS_HTTP_PATH
    assert settings.mast_table == "sap.sapabap1.mast"
    assert settings.stas_table == "sap.sapabap1.stas"
    assert settings.stpo_table == "sap.sapabap1.stpo"
    oauth = databricks_connect_kwargs(settings)
    assert oauth["auth_type"] == "databricks-oauth"
    assert "access_token" not in oauth


def test_stas_join_uses_stlnr_stlal_stlkn_and_skips_missing_datub():
    sql = build_bom_sql(
        "sap.sapabap1.mast",
        "sap.sapabap1.stpo",
        MAST_COLS,
        STPO_COLS,
        "sap.sapabap1.stas",
        STAS_COLS,
    )
    assert "INNER JOIN `sap`.`sapabap1`.`stas` a" in sql
    assert "INNER JOIN `sap`.`sapabap1`.`stpo` p" in sql
    assert "m.`stlnr`" in sql and "a.`stlnr`" in sql and "a.`stlal`" in sql
    assert "a.`stlkn`" in sql and "p.`stlkn`" in sql
    assert "a.`mandt`" in sql and "p.`mandt`" in sql
    assert "a.`stasz`" not in sql
    assert "a.`datub`" not in sql
    assert "datub" not in sql.lower()
    assert "'M002'" in sql and "= '1'" in sql and "'01'" in sql
    assert "'L'" in sql and "<> 'X'" in sql
    assert "'' AS `Description`" in sql
    assert "CURRENT_DATE()" in sql

    with_dates = build_bom_sql(
        "sap.sapabap1.mast",
        "sap.sapabap1.stpo",
        MAST_COLS,
        STPO_COLS + ["datub"],
        "sap.sapabap1.stas",
        STAS_COLS + ["datub"],
    )
    assert "p.`datub`" in with_dates
    assert "a.`datub`" not in with_dates

    with_stasz = build_bom_sql(
        "sap.sapabap1.mast",
        "sap.sapabap1.stpo",
        MAST_COLS,
        STPO_COLS + ["stasz"],
        "sap.sapabap1.stas",
        STAS_COLS,
    )
    assert "a.`stasz`" in with_stasz and "p.`stasz`" in with_stasz
