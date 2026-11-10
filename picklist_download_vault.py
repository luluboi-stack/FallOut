"""Persist consolidated-master picklist downloads under audit_vault/picklist/.

Package shape (easy to tweak without rewriting the app):

- Module-level knobs control folder name, dedupe, and which columns land in the CSV.
- `DownloadFilters` is the filter/sequence snapshot written beside each CSV.
- `build_download_frame` produces one row per part (consolidated), including parts that
  were not in the downloaded picklist.
- `record_master_picklist_download` writes CSV + meta JSON. Same walk + same filters
  refreshes meta only (no duplicate CSV). Filter change => new file.

This path is intentionally separate from walk-time `runs_csv` / `runs_json`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import pandas as pd

from picklist_engine import EPS, audit_decision, clean_sap_key

# ---------------------------------------------------------------------------
# Knobs — change these first when iterating on the package
# ---------------------------------------------------------------------------
PICKLIST_SUBDIR = "picklist"
WRITE_META_JSON = True
DEDUP_SAME_FILTER = True
FILENAME_SEP = "__"

EXCLUSION_INCLUDED = "INCLUDED_IN_DOWNLOAD"
EXCLUSION_FILTERED_UI = "FILTERED_OUT_BY_UI"
EXCLUSION_NOT_PICKABLE = "NOT_PICKABLE_ZERO_NEED"
EXCLUSION_OUTSIDE_WINDOW = "OUTSIDE_DISPATCH_WINDOW"
EXCLUSION_NEVER_ON_PLAN = "NEVER_ON_PLAN"

STATUS_WRITTEN = "written"
STATUS_REFRESHED = "refreshed"
STATUS_SKIPPED = "skipped"
STATUS_ERROR = "error"

DOWNLOAD_ROW_COLUMNS = [
    "walk_run_id",
    "snapshot_id",
    "filter_fingerprint",
    "downloaded_at",
    "part_id",
    "part_key",
    "part_name",
    "vehicle_sku",
    "sequence_order",
    "sequences_covered",
    "bom_demand",
    "demand_to_fulfil",
    "need_to_be_picked",
    "pss_qty",
    "pss_boxes_to_pick",
    "pss_type",
    "w004_stock",
    "m002_stock",
    "cogi_qty",
    "open_sto_in_transit",
    "open_po_numbers",
    "total_m002_pipeline",
    "transfer_qty",
    "transfer_status",
    "starvation_mins",
    "priority",
    "picklist_status",
    "decision_code",
    "decision_reason",
    "in_downloaded_picklist",
    "in_dispatch_window",
    "exclusion_reason",
    "sequences_supplied",
]


@dataclass(frozen=True)
class DownloadFilters:
    """Everything that defines 'this download view'. Add fields freely; fingerprint updates."""

    line_filter: str = "All"
    status_filter: str = "All Statuses"
    pss_filter: str = "All"
    search_text: str = ""
    hide_hold: bool = True
    selection_mode: str = ""
    target_dispatch_qty: Optional[int] = None
    manual_selected_seqs: Tuple[Any, ...] = ()
    active_seq_keys: Tuple[str, ...] = ()
    cutoff_seq_keys: Tuple[str, ...] = ()

    def fingerprint(self) -> str:
        payload = {
            "line_filter": self.line_filter,
            "status_filter": self.status_filter,
            "pss_filter": self.pss_filter,
            "search_text": (self.search_text or "").strip().lower(),
            "hide_hold": bool(self.hide_hold),
            "selection_mode": self.selection_mode,
            "target_dispatch_qty": self.target_dispatch_qty,
            "manual_selected_seqs": list(self.manual_selected_seqs),
            "active_seq_keys": sorted(self.active_seq_keys),
            "cutoff_seq_keys": sorted(self.cutoff_seq_keys),
        }
        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class DownloadVaultResult:
    status: str
    message: str
    csv_path: Optional[str] = None
    meta_path: Optional[str] = None
    filter_fingerprint: Optional[str] = None
    row_count: int = 0
    download_count: int = 0

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_WRITTEN, STATUS_REFRESHED)


def picklist_vault_dir(audit_vault_dir: str) -> str:
    path = os.path.join(audit_vault_dir, PICKLIST_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def _safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "unknown"))
    return token[:120] or "unknown"


def _sequences_supplied(filters: DownloadFilters) -> str:
    if not filters.active_seq_keys:
        return ""
    return "|".join(sorted(filters.active_seq_keys))


def _master_part_key(row: Mapping[str, Any]) -> str:
    return clean_sap_key(row.get("Part ID", row.get("part_id", "")))


def _part_keys(df: Optional[pd.DataFrame]) -> Set[str]:
    if df is None or df.empty or "Part ID" not in df.columns:
        return set()
    return {clean_sap_key(v) for v in df["Part ID"].tolist() if str(v).strip()}


def _row_from_master(
    row: Mapping[str, Any],
    *,
    walk_run_id: str,
    snapshot_id: str,
    filter_fingerprint: str,
    downloaded_at: str,
    sequences_supplied: str,
    in_downloaded: bool,
    in_window: bool,
    exclusion_reason: str,
    sto_taken: float,
) -> Dict[str, Any]:
    transfer_status = str(row.get("Transfer Status", "") or "")
    status, code, reason = audit_decision(transfer_status, float(sto_taken or 0.0))
    if exclusion_reason == EXCLUSION_NEVER_ON_PLAN:
        status, code, reason = (
            "REJECTED",
            "REJECTED_HORIZON_CUTOFF",
            "Part is not on any PLAN sequence in this walk (not scheduled, or HOLD).",
        )
    elif exclusion_reason == EXCLUSION_OUTSIDE_WINDOW:
        status, code, reason = (
            "REJECTED",
            "REJECTED_OUTSIDE_DISPATCH_WINDOW",
            "Part is on the walk but outside the sequences supplied by this download.",
        )
    elif exclusion_reason == EXCLUSION_FILTERED_UI and in_window:
        status, code, reason = (
            "REJECTED",
            "REJECTED_FILTERED_OUT_BY_UI",
            "Part had pick demand in the dispatch window but was hidden by line/PSS/status/search filters.",
        )

    return {
        "walk_run_id": walk_run_id,
        "snapshot_id": snapshot_id,
        "filter_fingerprint": filter_fingerprint,
        "downloaded_at": downloaded_at,
        "part_id": str(row.get("Part ID", "") or ""),
        "part_key": _master_part_key(row),
        "part_name": str(row.get("Part Name", "") or ""),
        "vehicle_sku": str(row.get("Vehicle_SKU", "") or ""),
        "sequence_order": int(row.get("Sequence_Order", 0) or 0),
        "sequences_covered": str(row.get("Sequences_Covered", "") or ""),
        "bom_demand": float(row.get("BOM Demand", 0) or 0),
        "demand_to_fulfil": float(row.get("Demand to Fulfil", 0) or 0),
        "need_to_be_picked": float(row.get("Need to be Picked", 0) or 0),
        "pss_qty": int(row.get("PSS Qty", 0) or 0),
        "pss_boxes_to_pick": int(row.get("To be picked PSS count", 0) or 0),
        "pss_type": str(row.get("PSS Type", "") or ""),
        "w004_stock": float(row.get("W004 Stock", 0) or 0),
        "m002_stock": float(row.get("M002 Stock", 0) or 0),
        "cogi_qty": float(row.get("COGI Qty", 0) or 0),
        "open_sto_in_transit": float(row.get("Open STO In-Transit", 0) or 0),
        "open_po_numbers": str(row.get("Open PO Numbers", "") or ""),
        "total_m002_pipeline": float(row.get("Total M002 Pipeline", 0) or 0),
        "transfer_qty": float(row.get("Transfer Qty (W004 ➡️ M002)", row.get("Need to be Picked", 0)) or 0),
        "transfer_status": transfer_status,
        "starvation_mins": int(row.get("Starvation_Mins", 0) or 0),
        "priority": str(row.get("Priority", "") or ""),
        "picklist_status": status,
        "decision_code": code,
        "decision_reason": reason,
        "in_downloaded_picklist": bool(in_downloaded),
        "in_dispatch_window": bool(in_window),
        "exclusion_reason": exclusion_reason,
        "sequences_supplied": sequences_supplied,
    }


def _never_on_plan_row(
    part_id: str,
    *,
    walk_run_id: str,
    snapshot_id: str,
    filter_fingerprint: str,
    downloaded_at: str,
    sequences_supplied: str,
    description: str,
    pss_qty: int,
    pss_type: str,
) -> Dict[str, Any]:
    stub = {
        "Part ID": part_id,
        "Part Name": description,
        "Vehicle_SKU": "Excluded_Batch",
        "Sequence_Order": 0,
        "Sequences_Covered": "",
        "BOM Demand": 0,
        "Demand to Fulfil": 0,
        "Need to be Picked": 0,
        "PSS Qty": pss_qty,
        "To be picked PSS count": 0,
        "PSS Type": pss_type,
        "W004 Stock": 0,
        "M002 Stock": 0,
        "COGI Qty": 0,
        "Open STO In-Transit": 0,
        "Open PO Numbers": "",
        "Total M002 Pipeline": 0,
        "Transfer Qty (W004 ➡️ M002)": 0,
        "Transfer Status": "",
        "Starvation_Mins": 999,
        "Priority": "EXCLUDED",
    }
    return _row_from_master(
        stub,
        walk_run_id=walk_run_id,
        snapshot_id=snapshot_id,
        filter_fingerprint=filter_fingerprint,
        downloaded_at=downloaded_at,
        sequences_supplied=sequences_supplied,
        in_downloaded=False,
        in_window=False,
        exclusion_reason=EXCLUSION_NEVER_ON_PLAN,
        sto_taken=0.0,
    )


def classify_exclusion(
    *,
    part_key: str,
    downloaded_keys: Set[str],
    window_keys: Set[str],
    need_to_be_picked: float,
) -> Tuple[bool, bool, str]:
    """Returns (in_downloaded, in_window, exclusion_reason)."""
    in_downloaded = part_key in downloaded_keys
    in_window = part_key in window_keys
    if in_downloaded:
        return True, True, EXCLUSION_INCLUDED
    if in_window:
        if float(need_to_be_picked or 0) <= EPS:
            return False, True, EXCLUSION_NOT_PICKABLE
        return False, True, EXCLUSION_FILTERED_UI
    return False, False, EXCLUSION_OUTSIDE_WINDOW


def build_download_frame(
    *,
    walk_run_id: str,
    snapshot_id: str,
    filters: DownloadFilters,
    full_master: pd.DataFrame,
    window_master: pd.DataFrame,
    downloaded_df: pd.DataFrame,
    sto_taken_by_part: Optional[Mapping[str, float]] = None,
    bom_part_ids: Optional[Iterable[str]] = None,
    descriptions: Optional[Mapping[str, str]] = None,
    pss_lookup: Optional[Callable[[str], Tuple[int, str]]] = None,
    downloaded_at: Optional[str] = None,
) -> pd.DataFrame:
    """One consolidated row per part: downloaded + every exclusion bucket."""
    downloaded_at = downloaded_at or time.strftime("%Y-%m-%d %H:%M:%S")
    fingerprint = filters.fingerprint()
    sequences_supplied = _sequences_supplied(filters)
    sto_taken_by_part = sto_taken_by_part or {}
    descriptions = descriptions or {}

    downloaded_keys = _part_keys(downloaded_df)
    window_keys = _part_keys(window_master)
    walk_keys: Set[str] = set()
    records: List[Dict[str, Any]] = []

    source = full_master if full_master is not None and not full_master.empty else window_master
    if source is not None and not source.empty:
        for _, row in source.iterrows():
            part_key = _master_part_key(row)
            if not part_key or part_key in walk_keys:
                continue
            walk_keys.add(part_key)
            in_downloaded, in_window, reason = classify_exclusion(
                part_key=part_key,
                downloaded_keys=downloaded_keys,
                window_keys=window_keys,
                need_to_be_picked=float(row.get("Need to be Picked", 0) or 0),
            )
            records.append(
                _row_from_master(
                    row,
                    walk_run_id=walk_run_id,
                    snapshot_id=snapshot_id or "",
                    filter_fingerprint=fingerprint,
                    downloaded_at=downloaded_at,
                    sequences_supplied=sequences_supplied,
                    in_downloaded=in_downloaded,
                    in_window=in_window,
                    exclusion_reason=reason,
                    sto_taken=float(sto_taken_by_part.get(part_key, 0.0) or 0.0),
                )
            )

    if bom_part_ids:
        for raw_part in bom_part_ids:
            part_id = str(raw_part).strip()
            part_key = clean_sap_key(part_id)
            if len(part_key) <= 2 or part_key in walk_keys:
                continue
            walk_keys.add(part_key)
            pss_qty, pss_type = (0, "")
            if pss_lookup is not None:
                pss_qty, pss_type = pss_lookup(part_id)
            records.append(
                _never_on_plan_row(
                    part_id,
                    walk_run_id=walk_run_id,
                    snapshot_id=snapshot_id or "",
                    filter_fingerprint=fingerprint,
                    downloaded_at=downloaded_at,
                    sequences_supplied=sequences_supplied,
                    description=descriptions.get(part_key, descriptions.get(part_id, "Standard Part")),
                    pss_qty=int(pss_qty or 0),
                    pss_type=str(pss_type or ""),
                )
            )

    if not records:
        return pd.DataFrame(columns=DOWNLOAD_ROW_COLUMNS)
    return pd.DataFrame(records, columns=DOWNLOAD_ROW_COLUMNS)


def _row_counts(df: pd.DataFrame) -> Dict[str, int]:
    counts = {"total": int(len(df))}
    if df.empty or "exclusion_reason" not in df.columns:
        return counts
    for reason, group in df.groupby("exclusion_reason"):
        counts[str(reason)] = int(len(group))
    counts["included"] = int(df["in_downloaded_picklist"].sum()) if "in_downloaded_picklist" in df.columns else 0
    return counts


def _existing_artifacts(
    dest_dir: str, walk_run_id: str, fingerprint: str
) -> Tuple[Optional[str], Optional[str]]:
    prefix = f"{_safe_token(walk_run_id)}{FILENAME_SEP}{fingerprint}{FILENAME_SEP}"
    csv_matches = sorted(
        name for name in os.listdir(dest_dir)
        if name.startswith(prefix) and name.endswith(".csv")
    )
    if not csv_matches:
        return None, None
    csv_path = os.path.join(dest_dir, csv_matches[-1])
    meta_path = csv_path[:-4] + ".meta.json"
    if not os.path.isfile(meta_path):
        meta_path = None
    return csv_path, meta_path


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    return value


def _write_meta(meta_path: str, payload: Dict[str, Any]) -> None:
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(_json_ready(payload), handle, indent=2, default=str)


def _load_meta(meta_path: Optional[str]) -> Dict[str, Any]:
    if not meta_path or not os.path.isfile(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def record_master_picklist_download(
    *,
    audit_vault_dir: str,
    walk_run_id: Optional[str],
    snapshot_id: str,
    filters: DownloadFilters,
    full_master: pd.DataFrame,
    window_master: pd.DataFrame,
    downloaded_df: pd.DataFrame,
    sto_taken_by_part: Optional[Mapping[str, float]] = None,
    bom_part_ids: Optional[Iterable[str]] = None,
    descriptions: Optional[Mapping[str, str]] = None,
    pss_lookup: Optional[Callable[[str], Tuple[int, str]]] = None,
    client_ip: str = "",
) -> DownloadVaultResult:
    """Write (or refresh) the consolidated master download package under audit_vault/picklist/."""
    if not walk_run_id:
        return DownloadVaultResult(
            STATUS_SKIPPED,
            "No walk_run_id yet (preview / incomplete SAP). Picklist download vault skipped.",
        )

    try:
        dest_dir = picklist_vault_dir(audit_vault_dir)
        fingerprint = filters.fingerprint()
        downloaded_at = time.strftime("%Y-%m-%d %H:%M:%S")
        frame = build_download_frame(
            walk_run_id=walk_run_id,
            snapshot_id=snapshot_id,
            filters=filters,
            full_master=full_master,
            window_master=window_master,
            downloaded_df=downloaded_df,
            sto_taken_by_part=sto_taken_by_part,
            bom_part_ids=bom_part_ids,
            descriptions=descriptions,
            pss_lookup=pss_lookup,
            downloaded_at=downloaded_at,
        )

        existing_csv, existing_meta = _existing_artifacts(dest_dir, walk_run_id, fingerprint)
        counts = _row_counts(frame)
        meta_payload = {
            "walk_run_id": walk_run_id,
            "snapshot_id": snapshot_id,
            "filter_fingerprint": fingerprint,
            "downloaded_at": downloaded_at,
            "client_ip": client_ip,
            "download_kind": "consolidated_master",
            "filters": asdict(filters),
            "row_counts": counts,
            "columns": list(DOWNLOAD_ROW_COLUMNS),
        }

        if DEDUP_SAME_FILTER and existing_csv:
            prior = _load_meta(existing_meta)
            download_count = int(prior.get("download_count", 1) or 1) + 1
            meta_payload["download_count"] = download_count
            meta_payload["csv_path"] = existing_csv
            meta_payload["first_downloaded_at"] = prior.get("first_downloaded_at") or prior.get("downloaded_at")
            # Refresh CSV content in place so a same-filter re-download still mirrors current numbers.
            frame.to_csv(existing_csv, index=False)
            meta_path = existing_meta or (existing_csv[:-4] + ".meta.json")
            if WRITE_META_JSON:
                _write_meta(meta_path, meta_payload)
            return DownloadVaultResult(
                STATUS_REFRESHED,
                f"Refreshed picklist vault for same filters ({fingerprint}); download #{download_count}.",
                existing_csv,
                meta_path if WRITE_META_JSON else None,
                fingerprint,
                int(len(frame)),
                download_count,
            )

        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = (
            f"{_safe_token(walk_run_id)}{FILENAME_SEP}{fingerprint}"
            f"{FILENAME_SEP}{stamp}"
        )
        csv_path = os.path.join(dest_dir, f"{base}.csv")
        meta_path = os.path.join(dest_dir, f"{base}.meta.json")
        frame.to_csv(csv_path, index=False)
        meta_payload["download_count"] = 1
        meta_payload["first_downloaded_at"] = downloaded_at
        meta_payload["csv_path"] = csv_path
        if WRITE_META_JSON:
            _write_meta(meta_path, meta_payload)
        return DownloadVaultResult(
            STATUS_WRITTEN,
            f"Wrote picklist vault CSV ({len(frame)} rows) under {PICKLIST_SUBDIR}/.",
            csv_path,
            meta_path if WRITE_META_JSON else None,
            fingerprint,
            int(len(frame)),
            1,
        )
    except Exception as exc:
        return DownloadVaultResult(STATUS_ERROR, f"Picklist download vault failed: {exc}")


def bom_child_ids(bom_df: Optional[pd.DataFrame], child_col: Optional[str]) -> List[str]:
    """Small helper so the app can pass BOM exclusions without inventing column logic twice."""
    if bom_df is None or bom_df.empty or not child_col or child_col not in bom_df.columns:
        return []
    return (
        bom_df[child_col]
        .dropna()
        .astype(str)
        .str.strip()
        .loc[lambda s: s.str.len() > 2]
        .drop_duplicates()
        .tolist()
    )
