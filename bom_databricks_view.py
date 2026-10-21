"""M002 production BOM table from Databricks, or a local fixture.

Demo, no warehouse:

    python3 -m streamlit run bom_databricks_view.py -- --demo

Live SSO (a browser opens once; no personal access token):

    python3 -m streamlit run bom_databricks_view.py

Install once if needed (NumPy 2 is required by the Streamlit table):

    pip install streamlit pandas 'numpy>=2' databricks-sql-connector
"""

from __future__ import annotations

import os
import sys

import streamlit as st

from bom_databricks import DataSourceError, load_demo_bom, load_live_bom


def demo_requested(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> bool:
    args = sys.argv if argv is None else argv
    env = os.environ if environ is None else environ
    if "--demo" in args:
        return True
    return env.get("BOM_VIEW_DEMO", "").strip().lower() in {"1", "true", "yes"}


def page(demo: bool) -> None:
    st.set_page_config(page_title="M002 BOM", layout="wide")
    st.title("M002 production BOM")
    if demo:
        st.caption("Demo fixture. Databricks is not contacted.")
        frame = load_demo_bom()
    else:
        st.caption(
            "Live mast → stas → stpo for plant M002, usage 1, alternative 01. "
            "Sign-in uses Databricks SSO."
        )
        try:
            frame = load_live_bom()
        except DataSourceError as exc:
            st.error(str(exc))
            st.stop()
    st.dataframe(frame, use_container_width=True, hide_index=True)
    st.caption(f"{len(frame)} component rows")


def main() -> None:
    page(demo_requested())


if __name__ == "__main__":
    main()
