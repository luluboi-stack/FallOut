"""Live BOM and unrestricted stock checker.

BOM tab: mast → stas → stpo for plant M002, usage 1, alternative 01, valid today,
not deleted, parents S450, SEL, and SRIZ. Stock tab: unrestricted MARD-LABST,
COGI, and open STO. The checker lists missing unrestricted stock and quantities
whose material is not on this BOM.

Demo fixtures, no warehouse:

    python3 -m streamlit run live_bom_stock_view.py -- --demo

Live SSO, same warehouse as the BOM viewer:

    python3 -m streamlit run live_bom_stock_view.py
"""

from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st

from live_stock import (
    WALK_PARENT_PREFIXES,
    PageFeeds,
    compare_bom_stock,
    empty_cogi,
    empty_stock,
    empty_sto,
    load_demo_feeds,
    load_live_feeds,
    unrestricted_buckets,
)


def demo_requested(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> bool:
    args = sys.argv if argv is None else argv
    env = os.environ if environ is None else environ
    if "--demo" in args:
        return True
    return env.get("LIVE_BOM_STOCK_DEMO", "").strip().lower() in {"1", "true", "yes"}


def _show_frame(frame: pd.DataFrame, empty_note: str) -> None:
    if frame is None or frame.empty:
        st.caption(empty_note)
        return
    st.dataframe(frame, use_container_width=True, hide_index=True)
    st.caption(f"{len(frame)} rows")


def _sap_stock(feeds: PageFeeds) -> pd.DataFrame | None:
    """Current unrestricted balance per material. Not a sequence total and not filtered by the walk."""
    if feeds.stock_error:
        return None
    return unrestricted_buckets(feeds.stock)


def _render_bom(feeds: PageFeeds) -> None:
    prefixes = ", ".join(WALK_PARENT_PREFIXES)
    st.caption(
        f"Walk SKUs {prefixes}. A kit parent is kept as the child of the S450, SEL, or SRIZ SKU. "
        "Parent as child repeats that kit on every one of its parts. "
        "Parent Material and Child Material stay the parent-to-child link."
    )
    if feeds.bom_error:
        st.error(feeds.bom_error)
        return
    _show_frame(feeds.bom, "No BOM rows.")


def _render_stock(feeds: PageFeeds) -> None:
    if feeds.stock_table:
        st.caption(f"Live M002 and W004 unrestricted stock from {feeds.stock_table}.")
    else:
        st.caption("Live M002 and W004 unrestricted stock.")
    st.caption(
        "One batch only. This is not added across hours or sequences, and it is not limited to the walk. "
        "M002 is RM01 + PS01 + PV01 unrestricted. W004 is RM01 unrestricted."
    )
    if feeds.stock_period:
        st.caption(feeds.stock_period)
    if feeds.stock_error:
        st.error(feeds.stock_error)
    else:
        st.subheader("SAP unrestricted stock")
        _show_frame(_sap_stock(feeds), "No stock rows for M002 or W004.")
    if feeds.cogi_error:
        st.error(f"COGI: {feeds.cogi_error}")
    elif not feeds.cogi_error:
        st.subheader("COGI")
        _show_frame(feeds.cogi, "No COGI rows for plant M002.")
    if feeds.sto_error:
        st.error(f"Open STO: {feeds.sto_error}")
    else:
        st.subheader("Open STO")
        _show_frame(feeds.sto, "No open stock transport quantity from W004 to M002.")


def _section(title: str, note: str, frame: pd.DataFrame | None, error: str | None) -> None:
    st.subheader(title)
    st.caption(note)
    if error:
        st.error(error)
        return
    _show_frame(frame if frame is not None else pd.DataFrame(), "None.")


def _render_checker(feeds: PageFeeds) -> None:
    st.header("Checker")
    st.caption(
        "Matched on the cleaned material number: punctuation stripped, upper case, leading zeros dropped."
    )
    bom_problem = f"BOM did not load: {feeds.bom_error}" if feeds.bom_error else None
    stock_problem = bom_problem or (f"Stock did not load: {feeds.stock_error}" if feeds.stock_error else None)
    cogi_problem = bom_problem or (f"COGI did not load: {feeds.cogi_error}" if feeds.cogi_error else None)
    sto_problem = bom_problem or (f"Open STO did not load: {feeds.sto_error}" if feeds.sto_error else None)

    checks = None
    if bom_problem is None:
        checks = compare_bom_stock(
            feeds.bom,
            feeds.stock if not feeds.stock_error else empty_stock(),
            feeds.cogi if not feeds.cogi_error else empty_cogi(),
            feeds.sto if not feeds.sto_error else empty_sto(),
        )

    def take(name: str) -> pd.DataFrame | None:
        if checks is None:
            return None
        return checks[name]

    _section(
        "On the BOM, no unrestricted stock",
        "Only the BOM parts whose unrestricted quantity is 0 in M002 RM01, PS01, PV01, and W004 RM01. Parts with stock are in the quantity table above. Zero COGI does not put a part on this list.",
        take("missing_unrestricted"),
        stock_problem,
    )
    _section(
        "Unrestricted stock not on this BOM",
        "Unrestricted quantity in those locations whose material is not a parent or child on this BOM.",
        take("stock_not_on_bom"),
        stock_problem,
    )
    _section(
        "COGI not on this BOM",
        "COGI quantity the walk would not see. A BOM part with no COGI row is not listed.",
        take("cogi_not_on_bom"),
        cogi_problem,
    )
    _section(
        "Open STO not on this BOM",
        "Open W004 to M002 quantity whose material is not on this BOM.",
        take("sto_not_on_bom"),
        sto_problem,
    )


def page(demo: bool) -> None:
    st.set_page_config(page_title="Live BOM and stock", layout="wide")
    st.title("Live BOM and stock")
    if demo:
        st.caption("Demo fixtures. Databricks is not contacted.")
        feeds = load_demo_feeds()
    else:
        st.caption("Live SSO. Stock is the latest batch already loaded at the current time.")
        if st.sidebar.button("Refresh live stock", type="primary") or "live_feeds" not in st.session_state:
            st.session_state.live_feeds = load_live_feeds()
        feeds = st.session_state.live_feeds
    bom_tab, stock_tab = st.tabs(["BOM", "Stock"])
    with bom_tab:
        _render_bom(feeds)
    with stock_tab:
        _render_stock(feeds)
    _render_checker(feeds)


def main() -> None:
    page(demo_requested())


if __name__ == "__main__":
    main()
