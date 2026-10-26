"""Fixture checks for unrestricted buckets, the BOM comparison, and demo mode."""

import sys

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import bom_databricks
from bom_databricks import DataSourceError, DatabricksSettings, build_bom_sql
from live_stock import (
    COGI_TABLE,
    COGI_TABLE_FALLBACK,
    EKKO_TABLE,
    MARD_TABLE,
    MARD_TABLE_FALLBACK,
    StockTables,
    WALK_PARENT_PREFIXES,
    build_cogi_sql,
    build_open_sto_sql,
    build_stock_sql,
    compare_bom_stock,
    empty_cogi,
    empty_sto,
    load_bom_frame,
    load_cogi_frame,
    load_demo_feeds,
    load_stock_frame,
    tool_quantities,
)

MAST_COLS = ["mandt", "matnr", "werks", "stlan", "stlal", "stlnr"]
STPO_COLS = ["mandt", "stlnr", "stlkn", "idnrk", "menge", "postp", "posnr", "meins", "datuv", "lkenz"]
STAS_COLS = ["mandt", "stlty", "stlnr", "stlal", "stlkn", "stasz", "datuv", "lkenz"]
MARD_COLS = ["matnr", "werks", "lgort", "labst", "insme", "speme"]
EKKO_COLS = ["mandt", "ebeln", "bsart", "reswk", "loekz"]
EKPO_COLS = ["mandt", "ebeln", "ebelp", "matnr", "werks", "menge", "loekz", "elikz"]
EKET_COLS = ["mandt", "ebeln", "ebelp", "menge", "wemng"]


class _RefuseSql:
    @staticmethod
    def connect(*_args, **_kwargs):
        raise AssertionError("demo mode must not open a Databricks connection")


def _stock_row(material, plant, sloc, qty, insme=0, speme=0):
    return {
        "Material": material,
        "Plant": plant,
        "Storage Location": sloc,
        "Unrestricted": qty,
        "insme": insme,
        "speme": speme,
    }


def test_pv01_unrestricted_counts_in_m002_bucket():
    sql = build_stock_sql("sap.sapabap1.mard", MARD_COLS)
    assert "`labst`" in sql
    assert "insme" not in sql.lower()
    assert "speme" not in sql.lower()
    assert "'PV01'" in sql and "'PS01'" in sql and "'RM01'" in sql
    assert "'M002'" in sql and "'W004'" in sql
    with pytest.raises(DataSourceError, match="labst"):
        build_stock_sql("sap.sapabap1.mard", ["matnr", "werks", "lgort", "insme", "speme"])

    stock = pd.DataFrame(
        [
            _stock_row("P", "M002", "PV01", 2, insme=100, speme=50),
            _stock_row("P", "m002", "pv01", 3, insme=100, speme=50),
            _stock_row("P", "M002", "RM01", 1, insme=9, speme=9),
            _stock_row("P", "M002", "PS01", 4, insme=9, speme=9),
            _stock_row("P", "W004", "RM01", 7, insme=9, speme=9),
            _stock_row("P", "M002", "FG01", 100, insme=0, speme=0),
        ]
    )
    bom = pd.DataFrame({"Parent Material": ["S450X"], "Child Material": ["P"]})
    result = compare_bom_stock(bom, stock, empty_cogi(), empty_sto())
    row = result["quantities"].set_index("Material").loc["P"]
    assert row["M002 PV01"] == 5
    assert row["M002 RM01"] == 1
    assert row["M002 PS01"] == 4
    assert row["m002_raw"] == 10
    assert row["w004_rm01"] == 7
    assert row["cogi"] == 0
    assert row["open_sto"] == 0
    assert row["m002_effective"] == 10
    assert result["missing_unrestricted"].empty

    heavy = tool_quantities(stock, pd.DataFrame({"Material": ["P"], "Quantity": [100]}), empty_sto())
    assert heavy.set_index("Material").loc["P", "m002_effective"] == 0


def test_bom_part_with_all_unrestricted_buckets_zero_is_missing():
    bom = pd.DataFrame(
        {
            "Parent Material": ["SEL1", "SEL1", "SEL1"],
            "Child Material": ["0000ABC", "CHILD-ZERO", "HAS"],
        }
    )
    stock = pd.DataFrame(
        [
            _stock_row("ABC", "M002", "RM01", 0),
            _stock_row("ABC", "M002", "PV01", 0),
            _stock_row("child-zero", "M002", "RM01", 0),
            _stock_row("HAS", "W004", "RM01", 1),
        ]
    )
    result = compare_bom_stock(bom, stock, empty_cogi(), empty_sto())
    assert result["missing_unrestricted"]["Material"].tolist() == ["ABC", "CHILDZERO"]
    assert result["stock_not_on_bom"].empty


def test_stock_part_absent_from_bom_is_listed():
    bom = pd.DataFrame({"Parent Material": ["SRIZ1"], "Child Material": ["ONBOM"]})
    stock = pd.DataFrame(
        [
            _stock_row("ONBOM", "M002", "PS01", 2),
            _stock_row("EXTRA", "M002", "PV01", 6),
            _stock_row("SRIZ1", "W004", "RM01", 4),
        ]
    )
    result = compare_bom_stock(bom, stock, empty_cogi(), empty_sto())
    assert result["stock_not_on_bom"]["Material"].tolist() == ["EXTRA"]
    assert result["missing_unrestricted"].empty


def test_cogi_material_absent_from_bom_is_listed_and_a_gap_is_not():
    bom = pd.DataFrame(
        {
            "Parent Material": ["S450A", "S450A"],
            "Child Material": ["ONBOM", "QUIET"],
        }
    )
    stock = pd.DataFrame(
        [
            _stock_row("ONBOM", "M002", "RM01", 4),
            _stock_row("QUIET", "M002", "RM01", 1),
        ]
    )
    cogi = pd.DataFrame({"Material": ["GHOST", "GHOST", "ONBOM"], "Quantity": [1, 2, 0]})
    result = compare_bom_stock(bom, stock, cogi, empty_sto())
    assert result["cogi_not_on_bom"]["Material"].tolist() == ["GHOST"]
    assert result["cogi_not_on_bom"]["cogi"].tolist() == [3]
    listed = set(result["cogi_not_on_bom"]["Material"])
    assert "QUIET" not in listed
    assert "ONBOM" not in listed
    quiet = result["quantities"].set_index("Material").loc["QUIET"]
    assert quiet["cogi"] == 0
    assert result["missing_unrestricted"].empty
    assert "missing_cogi" not in result


def test_open_sto_material_absent_from_bom_is_listed():
    bom = pd.DataFrame(
        {
            "Parent Material": ["S450A", "S450A"],
            "Child Material": ["ONBOM", "NOSTO"],
        }
    )
    stock = pd.DataFrame(
        [
            _stock_row("ONBOM", "M002", "RM01", 1),
            _stock_row("NOSTO", "M002", "PS01", 1),
        ]
    )
    sto = pd.DataFrame({"Material": ["TRANSIT", "ONBOM"], "Open Qty": [8, 2]})
    result = compare_bom_stock(bom, stock, empty_cogi(), sto)
    assert result["sto_not_on_bom"]["Material"].tolist() == ["TRANSIT"]
    assert result["sto_not_on_bom"]["open_sto"].tolist() == [8]
    indexed = result["quantities"].set_index("Material")
    assert indexed.loc["ONBOM", "open_sto"] == 2
    assert indexed.loc["NOSTO", "open_sto"] == 0
    assert "NOSTO" not in set(result["sto_not_on_bom"]["Material"])


def test_parent_prefix_filter_is_optional():
    bare = build_bom_sql(
        "sap.sapabap1.mast",
        "sap.sapabap1.stpo",
        MAST_COLS,
        STPO_COLS,
        "sap.sapabap1.stas",
        STAS_COLS,
    )
    assert "S450" not in bare
    filtered = build_bom_sql(
        "sap.sapabap1.mast",
        "sap.sapabap1.stpo",
        MAST_COLS,
        STPO_COLS,
        "sap.sapabap1.stas",
        STAS_COLS,
        parent_prefixes=WALK_PARENT_PREFIXES,
    )
    assert "LIKE 'S450%'" in filtered
    assert "LIKE 'SEL%'" in filtered
    assert "LIKE 'SRIZ%'" in filtered
    assert bare != filtered


def test_cogi_sql_sums_erfmg_for_m002():
    sql = build_cogi_sql("sap.sapabap1.affw", ["matnr", "werks", "erfmg", "menge", "insme"])
    assert "`erfmg`" in sql
    assert "menge" not in sql.lower()
    assert "insme" not in sql.lower()
    assert "'M002'" in sql


def test_open_sto_sql_uses_received_quantity_not_ordered_quantity():
    sql = build_open_sto_sql(
        "sap.sapabap1.ekko",
        "sap.sapabap1.ekpo",
        EKKO_COLS,
        EKPO_COLS,
        "sap.sapabap1.eket",
        EKET_COLS,
    )
    assert "INNER JOIN `sap`.`sapabap1`.`eket` et" in sql
    assert "et.`wemng`" in sql
    assert "et.`menge`" in sql
    assert "po.`menge`" not in sql
    assert "po.`wemng`" not in sql
    assert "GREATEST" in sql
    assert "'W004'" in sql and "'M002'" in sql
    assert "IN ('UB')" in sql
    assert "`loekz`" in sql and "`elikz`" in sql
    assert "> 0" in sql

    on_item = build_open_sto_sql(
        "sap.sapabap1.ekko",
        "sap.sapabap1.ekpo",
        EKKO_COLS,
        EKPO_COLS + ["wemng"],
        None,
        None,
    )
    assert "eket" not in on_item.lower()
    assert "po.`wemng`" in on_item
    with pytest.raises(DataSourceError, match="wemng"):
        build_open_sto_sql(
            "sap.sapabap1.ekko",
            "sap.sapabap1.ekpo",
            EKKO_COLS,
            EKPO_COLS,
            None,
            None,
        )


def test_stock_describe_falls_back_when_mard_is_absent(monkeypatch):
    calls = []

    def describe(_conn, table):
        calls.append(table)
        if table == MARD_TABLE:
            raise DataSourceError(
                "[TABLE_OR_VIEW_NOT_FOUND] The table or view sap.sapabap1.mard cannot be found."
            )
        if table == MARD_TABLE_FALLBACK:
            return ["matnr", "werks", "lgort", "labst", "insme", "speme"]
        raise DataSourceError(table)

    def query(_conn, sql):
        assert "nsdm_v_mard_hourly" in sql
        assert "`labst`" in sql
        assert "insme" not in sql.lower()
        return pd.DataFrame(
            {
                "Material": ["P"],
                "Plant": ["M002"],
                "Storage Location": ["PV01"],
                "Unrestricted": [5],
            }
        )

    monkeypatch.setattr(bom_databricks, "_describe", describe)
    monkeypatch.setattr(bom_databricks, "_query", query)
    frame, used = load_stock_frame(object(), (MARD_TABLE, MARD_TABLE_FALLBACK))
    assert used == MARD_TABLE_FALLBACK
    assert calls == [MARD_TABLE, MARD_TABLE_FALLBACK]
    assert frame["Unrestricted"].iloc[0] == 5


def test_cogi_describe_falls_back_only_when_affw_is_absent(monkeypatch):
    calls = []

    def describe(_conn, table):
        calls.append(("describe", table))
        if table == COGI_TABLE:
            raise DataSourceError("TABLE_OR_VIEW_NOT_FOUND")
        if table == COGI_TABLE_FALLBACK:
            return ["matnr", "werks", "erfmg", "menge"]
        raise DataSourceError(table)

    def query(_conn, sql):
        calls.append(("query", sql))
        assert "affw_hourly" in sql
        assert "`erfmg`" in sql
        assert "menge" not in sql.lower()
        return pd.DataFrame({"Material": ["A"], "Quantity": [1]})

    monkeypatch.setattr(bom_databricks, "_describe", describe)
    monkeypatch.setattr(bom_databricks, "_query", query)
    frame, used = load_cogi_frame(object(), (COGI_TABLE, COGI_TABLE_FALLBACK))
    assert used == COGI_TABLE_FALLBACK
    assert calls[0] == ("describe", COGI_TABLE)
    assert frame["Quantity"].iloc[0] == 1

    calls.clear()

    def describe_affw(_conn, table):
        calls.append(table)
        assert table == COGI_TABLE
        return ["matnr", "werks", "erfmg", "menge"]

    monkeypatch.setattr(bom_databricks, "_describe", describe_affw)
    monkeypatch.setattr(
        bom_databricks,
        "_query",
        lambda _conn, sql: pd.DataFrame({"Material": ["A"], "Quantity": [2]}),
    )
    _frame, used = load_cogi_frame(object(), (COGI_TABLE, COGI_TABLE_FALLBACK))
    assert used == COGI_TABLE
    assert calls == [COGI_TABLE]

    def describe_denied(_conn, table):
        raise DataSourceError(f"permission denied for {table}")

    monkeypatch.setattr(bom_databricks, "_describe", describe_denied)
    with pytest.raises(DataSourceError, match="permission denied"):
        load_cogi_frame(object(), (COGI_TABLE, COGI_TABLE_FALLBACK))


def test_live_bom_query_limits_walk_parents(monkeypatch):
    seen = []

    def describe(_conn, table):
        if table.endswith("mast"):
            return MAST_COLS
        if table.endswith("stpo"):
            return STPO_COLS
        if table.endswith("stas"):
            return STAS_COLS
        raise DataSourceError(table)

    def query(_conn, sql):
        seen.append(sql)
        return pd.DataFrame({"Parent Material": ["S450X"], "Child Material": ["P"]})

    monkeypatch.setattr(bom_databricks, "_describe", describe)
    monkeypatch.setattr(bom_databricks, "_query", query)
    frame = load_bom_frame(object(), DatabricksSettings.resolve(environ={}))
    assert "LIKE 'S450%'" in seen[0]
    assert "LIKE 'SEL%'" in seen[0]
    assert "LIKE 'SRIZ%'" in seen[0]
    assert list(frame.columns)
    assert frame["Child Material"].iloc[0] == "P"


def test_stock_table_names_follow_env():
    custom = StockTables.resolve(
        environ={
            "DATABRICKS_MARD_TABLE": "sap.sapabap1.mard_custom",
            "DATABRICKS_COGI_TABLE": "sap.sapabap1.affw_custom",
        }
    )
    assert custom.mard_tables == ("sap.sapabap1.mard_custom",)
    assert custom.cogi_tables == ("sap.sapabap1.affw_custom",)
    defaults = StockTables.resolve(environ={})
    assert defaults.mard_tables == (MARD_TABLE, MARD_TABLE_FALLBACK)
    assert defaults.cogi_tables == (COGI_TABLE, COGI_TABLE_FALLBACK)
    assert defaults.ekko_table == EKKO_TABLE


def test_demo_loader_does_not_call_databricks(monkeypatch):
    monkeypatch.setattr(bom_databricks, "databricks_sql", _RefuseSql)
    monkeypatch.setattr(sys, "argv", ["live_bom_stock_view.py", "--demo"])
    feeds = load_demo_feeds()
    assert feeds.demo
    assert feeds.bom_error is None and feeds.stock_error is None
    assert feeds.cogi_error is None and feeds.sto_error is None
    result = compare_bom_stock(feeds.bom, feeds.stock, feeds.cogi, feeds.sto)
    indexed = result["quantities"].set_index("Material")
    part = indexed.loc["100001"]
    assert part["M002 PV01"] == 4
    assert part["m002_raw"] == 15
    assert part["cogi"] == 2
    assert part["m002_effective"] == 13
    assert part["open_sto"] == 1
    assert part["w004_rm01"] == 7
    assert result["missing_unrestricted"]["Material"].tolist() == ["CHILDZERO"]
    assert indexed.loc["CHILDZERO", "m002_raw"] == 0
    assert result["stock_not_on_bom"]["Material"].tolist() == ["EXTRASTOCK"]
    assert result["cogi_not_on_bom"]["Material"].tolist() == ["OFFBOMCOGI"]
    assert "CHILDNOCOGI" not in set(result["cogi_not_on_bom"]["Material"])
    assert indexed.loc["CHILDNOCOGI", "cogi"] == 0
    assert result["sto_not_on_bom"]["Material"].tolist() == ["OFFBOMSTO"]
    assert "CHILDPV" not in set(result["missing_unrestricted"]["Material"])

    app = AppTest.from_file("live_bom_stock_view.py", default_timeout=30)
    app.run()
    assert not app.exception
    shown = " ".join(str(item.value) for item in app.dataframe)
    assert "CHILDZERO" in shown
    assert "OFFBOMCOGI" in shown
    assert "OFFBOMSTO" in shown
    assert "EXTRASTOCK" in shown
    titles = [getattr(item, "value", "") for item in list(app.subheader) + list(app.header)]
    assert "COGI" in titles
    assert "Open STO" in titles
    assert "On the BOM, no unrestricted stock" in titles
    assert "Unrestricted stock not on this BOM" in titles
    assert "COGI not on this BOM" in titles
    assert "Open STO not on this BOM" in titles
