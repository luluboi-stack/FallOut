"""Append one new walk's audit JSON into the Drive for desktop picklist folder.

Drive for desktop syncs the folder. This module does not call the Google Drive API.
`drive_export.py` still holds that API path; the walk does not call it, because doing
both would upload `picklist.csv` twice.
"""
import csv
import json
import os
import shutil
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence

ENV_PICKLIST_DRIVE_DIR = "PICKLIST_DRIVE_DIR"
DEFAULT_PICKLIST_DRIVE_DIR = r"G:\My Drive\Picklist"
PICKLIST_CSV_NAME = "picklist.csv"

PICKLIST_APPENDED = "appended"
PICKLIST_SKIPPED = "skipped"
PICKLIST_ERROR = "error"


@dataclass(frozen=True)
class PicklistCopyResult:
    status: str
    message: str
    path: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == PICKLIST_APPENDED


def picklist_drive_dir(environ: Optional[Mapping[str, str]] = None) -> str:
    environ = os.environ if environ is None else environ
    configured = (environ.get(ENV_PICKLIST_DRIVE_DIR) or "").strip()
    return configured or DEFAULT_PICKLIST_DRIVE_DIR


def _columns(records: Sequence[Mapping[str, Any]]) -> List[str]:
    columns: List[str] = []
    seen = set()
    for record in records:
        for key in record:
            if key not in seen:
                seen.add(key)
                columns.append(str(key))
    return columns


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    return value


def _existing_header(csv_path: str) -> Optional[List[str]]:
    if not os.path.isfile(csv_path) or os.path.getsize(csv_path) == 0:
        return None
    with open(csv_path, "r", newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle), None)
    if not header:
        return None
    return header


def _append_records(csv_path: str, records: Sequence[Mapping[str, Any]]) -> int:
    """Create the CSV with a header, or append rows under the header already on disk.

    An existing file is only opened for append. It is never replaced or truncated.
    """
    header = _existing_header(csv_path)
    write_header = header is None
    fieldnames = header if header is not None else _columns(records)
    if not fieldnames:
        return 0
    with open(csv_path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, extrasaction="ignore", restval="",
        )
        if write_header:
            writer.writeheader()
        for record in records:
            writer.writerow({key: _cell(record.get(key, "")) for key in fieldnames})
    return len(records)


def append_run_json(
    json_path: str,
    environ: Optional[Mapping[str, str]] = None,
) -> PicklistCopyResult:
    """Read this walk's JSON and append every record, including trigger 0.

    A missing folder or a write error is returned, never raised, so the walk still stands.
    """
    dest_dir = picklist_drive_dir(environ)
    try:
        if not os.path.isdir(dest_dir):
            return PicklistCopyResult(
                PICKLIST_SKIPPED,
                f"Picklist folder not found ({dest_dir}). Skipped.",
                dest_dir,
            )
        with open(json_path, "r", encoding="utf-8") as handle:
            records = json.load(handle)
        if not isinstance(records, list):
            return PicklistCopyResult(
                PICKLIST_ERROR,
                f"Picklist folder write failed: {json_path} is not a list of records.",
                dest_dir,
            )
        csv_path = os.path.join(dest_dir, PICKLIST_CSV_NAME)
        count = _append_records(csv_path, records)
        dest_json = os.path.join(dest_dir, os.path.basename(json_path))
        shutil.copyfile(json_path, dest_json)
        return PicklistCopyResult(
            PICKLIST_APPENDED,
            f"Appended {count} rows to {csv_path}.",
            csv_path,
        )
    except Exception as exc:
        return PicklistCopyResult(
            PICKLIST_ERROR,
            f"Picklist folder write failed: {exc}",
            dest_dir,
        )
