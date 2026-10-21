"""Localhost table for the Databricks BOM. Separate from the picklist app.

    streamlit run bom_viewer.py

The page opens on bom_sample.csv so the table renders when SSO cannot finish.
Query live BOM calls fetch_live_bom, which is the Databricks mast → stas → stpo query.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from bom_databricks import fetch_live_bom

SAMPLE_PATH = Path(__file__).with_name("bom_sample.csv")
BLANK_DESCRIPTION = "blank on live query"
SAMPLE_BANNER = (
    "LOCAL SAMPLE — these rows are not from Databricks. "
    "They are stand-in rows so the table still renders when SSO cannot finish on this machine."
)

DISPLAY_COLUMNS = [
    "parent",
    "child",
    "item",
    "qty",
    "alt",
    "valid from",
    "valid to",
    "description",
]

_SOURCE_COLUMNS = {
    "parent": "Parent Material",
    "child": "Child Material",
    "item": "BOM Item Number",
    "qty": "Comp Qty.",
    "alt": "Alt. BOM",
    "valid from": "valid From",
    "valid to": "Valid To",
    "description": "Description",
}


def _cell(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def load_sample(path: Path = SAMPLE_PATH) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def shape_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Eight viewer columns. An empty description says the live query left it blank."""
    rows = []
    source = frame if frame is not None else pd.DataFrame()
    for _, row in source.iterrows():
        shaped = {}
        for display, column in _SOURCE_COLUMNS.items():
            shaped[display] = _cell(row[column]) if column in source.columns else ""
        if shaped["description"] == "":
            shaped["description"] = BLANK_DESCRIPTION
        rows.append(shaped)
    return pd.DataFrame(rows, columns=DISPLAY_COLUMNS)


def main() -> None:
    st.set_page_config(page_title="Databricks BOM", layout="wide")
    st.title("Databricks BOM")
    st.caption("BOM only. Plant M002, usage 1, alternative 01. This page does not run the picklist.")

    if st.button("Query live BOM", type="primary"):
        with st.spinner("Opening Databricks SSO and running the live BOM query…"):
            try:
                st.session_state["live_bom"] = fetch_live_bom()
                st.session_state["live_error"] = None
            except Exception as exc:
                st.session_state["live_bom"] = None
                st.session_state["live_error"] = str(exc)

    live = st.session_state.get("live_bom")
    error = st.session_state.get("live_error")
    if error:
        st.error(f"Live query did not finish: {error}")

    if live is not None:
        st.success("Live Databricks BOM. Warehouse SSO, sap.sapabap1, mast → stas → stpo.")
        table = shape_table(live)
    else:
        st.warning(SAMPLE_BANNER)
        table = shape_table(load_sample())

    st.dataframe(table, hide_index=True, width="stretch")
    st.caption(
        "One row is one component. "
        "parent = MAST material, child = STPO component, item = item number, "
        "qty = component quantity, alt = alternative, "
        "valid from = valid-from date, valid to = STPO valid-to when that column exists. "
        f"description shows “{BLANK_DESCRIPTION}” when the query returns no text."
    )


if __name__ == "__main__":
    main()
