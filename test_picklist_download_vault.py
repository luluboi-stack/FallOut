"""Unit checks for consolidated-master picklist download vault behavior."""
import json
import os

import pandas as pd

from picklist_download_vault import (
    DEDUP_SAME_FILTER,
    EXCLUSION_FILTERED_UI,
    EXCLUSION_INCLUDED,
    EXCLUSION_NEVER_ON_PLAN,
    EXCLUSION_NOT_PICKABLE,
    EXCLUSION_OUTSIDE_WINDOW,
    STATUS_REFRESHED,
    STATUS_WRITTEN,
    DownloadFilters,
    build_download_frame,
    record_master_picklist_download,
)


def _master_row(part_id: str, need: float, status: str = "Ready for Full Transfer", **extra):
    row = {
        "Sequence_Order": 1,
        "Vehicle_SKU": "RIZTA",
        "Part ID": part_id,
        "Part Name": f"Desc {part_id}",
        "BOM Demand": need,
        "Demand to Fulfil": need,
        "Need to be Picked": need,
        "PSS Qty": 10,
        "To be picked PSS count": 1 if need else 0,
        "W004 Stock": 100,
        "M002 Stock": 0,
        "COGI Qty": 0,
        "Open STO In-Transit": 0,
        "Open PO Numbers": "",
        "Total M002 Pipeline": 0,
        "PSS Type": "Pallet",
        "Starvation_Mins": 30,
        "Priority": "P1",
        "Sequences_Covered": "Seqs #1 - #2",
        "Transfer Qty (W004 ➡️ M002)": need,
        "Transfer Status": status,
        "Is_Zero_Stock": False,
    }
    row.update(extra)
    return row


def test_filter_fingerprint_changes_with_filters():
    a = DownloadFilters(line_filter="All", target_dispatch_qty=180, active_seq_keys=("Line_1#1",))
    b = DownloadFilters(line_filter="Line_1", target_dispatch_qty=180, active_seq_keys=("Line_1#1",))
    c = DownloadFilters(line_filter="All", target_dispatch_qty=180, active_seq_keys=("Line_1#1",))
    assert a.fingerprint() != b.fingerprint()
    assert a.fingerprint() == c.fingerprint()


def test_build_download_frame_exclusion_buckets():
    full = pd.DataFrame([
        _master_row("PART_A", 20),
        _master_row("PART_B", 0, status="Sufficient M002 Stock"),
        _master_row("PART_C", 15),
        _master_row("PART_D", 12),
    ])
    window = full[full["Part ID"].isin(["PART_A", "PART_B", "PART_C"])].copy()
    downloaded = window[window["Part ID"] == "PART_A"].copy()
    filters = DownloadFilters(active_seq_keys=("Line_1#1", "Line_2#2"))

    frame = build_download_frame(
        walk_run_id="walk_test",
        snapshot_id="sap_abc",
        filters=filters,
        full_master=full,
        window_master=window,
        downloaded_df=downloaded,
        bom_part_ids=["PART_E", "PART_A"],
        pss_lookup=lambda _pid: (24, "Pallet"),
    )

    by_part = {row["part_id"]: row for _, row in frame.iterrows()}
    assert by_part["PART_A"]["exclusion_reason"] == EXCLUSION_INCLUDED
    assert bool(by_part["PART_A"]["in_downloaded_picklist"]) is True
    assert by_part["PART_B"]["exclusion_reason"] == EXCLUSION_NOT_PICKABLE
    assert by_part["PART_C"]["exclusion_reason"] == EXCLUSION_FILTERED_UI
    assert by_part["PART_D"]["exclusion_reason"] == EXCLUSION_OUTSIDE_WINDOW
    assert by_part["PART_E"]["exclusion_reason"] == EXCLUSION_NEVER_ON_PLAN
    assert "Line_1#1" in by_part["PART_A"]["sequences_supplied"]
    assert set(frame["walk_run_id"]) == {"walk_test"}


def test_record_dedupes_same_filter_and_writes_new_on_change(tmp_path):
    assert DEDUP_SAME_FILTER is True
    vault = tmp_path / "audit_vault"
    vault.mkdir()
    full = pd.DataFrame([_master_row("PART_A", 20)])
    downloaded = full.copy()
    filters = DownloadFilters(line_filter="All", active_seq_keys=("Line_1#1",))

    first = record_master_picklist_download(
        audit_vault_dir=str(vault),
        walk_run_id="walk_1",
        snapshot_id="sap_1",
        filters=filters,
        full_master=full,
        window_master=full,
        downloaded_df=downloaded,
    )
    assert first.status == STATUS_WRITTEN
    assert first.csv_path and os.path.isfile(first.csv_path)
    assert first.meta_path and os.path.isfile(first.meta_path)

    second = record_master_picklist_download(
        audit_vault_dir=str(vault),
        walk_run_id="walk_1",
        snapshot_id="sap_1",
        filters=filters,
        full_master=full,
        window_master=full,
        downloaded_df=downloaded,
    )
    assert second.status == STATUS_REFRESHED
    assert second.csv_path == first.csv_path
    assert second.download_count == 2

    with open(second.meta_path, "r", encoding="utf-8") as handle:
        meta = json.load(handle)
    assert meta["download_count"] == 2
    assert meta["filters"]["active_seq_keys"] == ["Line_1#1"]

    changed = DownloadFilters(line_filter="Line_1", active_seq_keys=("Line_1#1",))
    third = record_master_picklist_download(
        audit_vault_dir=str(vault),
        walk_run_id="walk_1",
        snapshot_id="sap_1",
        filters=changed,
        full_master=full,
        window_master=full,
        downloaded_df=downloaded,
    )
    assert third.status == STATUS_WRITTEN
    assert third.csv_path != first.csv_path
    picklist_dir = vault / "picklist"
    csv_files = list(picklist_dir.glob("*.csv"))
    assert len(csv_files) == 2


def test_skip_without_walk_run_id(tmp_path):
    result = record_master_picklist_download(
        audit_vault_dir=str(tmp_path),
        walk_run_id=None,
        snapshot_id="sap_1",
        filters=DownloadFilters(),
        full_master=pd.DataFrame(),
        window_master=pd.DataFrame(),
        downloaded_df=pd.DataFrame(),
    )
    assert result.status == "skipped"
