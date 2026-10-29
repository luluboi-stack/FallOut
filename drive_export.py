"""Replace one Google Drive file, picklist.csv, with the latest picklist's audit JSON as CSV.

Auth is a service account key at GOOGLE_APPLICATION_CREDENTIALS. The target folder is
GOOGLE_DRIVE_FOLDER_ID. The Drive file id is kept in <vault>/drive_file_id.txt so every later
picklist updates the same file and the share link never changes.
"""
import io
import json
import os
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional

import pandas as pd

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseUpload
except ImportError:
    service_account = None
    build = None
    HttpError = None
    MediaIoBaseUpload = None

DRIVE_FILE_NAME = "picklist.csv"
DRIVE_FILE_ID_NAME = "drive_file_id.txt"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
ENV_FOLDER_ID = "GOOGLE_DRIVE_FOLDER_ID"
ENV_CREDENTIALS = "GOOGLE_APPLICATION_CREDENTIALS"

DRIVE_CREATED = "created"
DRIVE_UPDATED = "updated"
DRIVE_NOT_CONFIGURED = "not_configured"
DRIVE_ERROR = "error"


@dataclass(frozen=True)
class DriveResult:
    status: str
    message: str
    file_id: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status in (DRIVE_CREATED, DRIVE_UPDATED)


def drive_settings_missing(environ: Optional[Mapping[str, str]] = None) -> List[str]:
    environ = os.environ if environ is None else environ
    return [name for name in (ENV_FOLDER_ID, ENV_CREDENTIALS) if not (environ.get(name) or "").strip()]


def run_json_to_csv_bytes(json_path: str) -> bytes:
    """Every key of the audit records becomes a column, raw_json included."""
    with open(json_path, "r") as f:
        records = json.load(f)
    return pd.DataFrame(records).to_csv(index=False).encode("utf-8")


def build_drive_service(credentials_path: str):
    creds = service_account.Credentials.from_service_account_file(credentials_path, scopes=DRIVE_SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _read_file_id(id_path: str) -> Optional[str]:
    if not os.path.exists(id_path):
        return None
    with open(id_path, "r") as f:
        return f.read().strip() or None


def _write_file_id(id_path: str, file_id: str):
    with open(id_path, "w") as f:
        f.write(file_id)


def _media(csv_bytes: bytes):
    return MediaIoBaseUpload(io.BytesIO(csv_bytes), mimetype="text/csv", resumable=False)


def _find_existing(service, folder_id: str) -> Optional[str]:
    """Adopts a picklist.csv already in the folder, e.g. one a user pre-created in a My Drive folder."""
    q = f"name = '{DRIVE_FILE_NAME}' and '{folder_id}' in parents and trashed = false"
    resp = service.files().list(
        q=q, fields="files(id)", pageSize=1,
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def export_run_json(
    json_path: str,
    vault_dir: str,
    environ: Optional[Mapping[str, str]] = None,
    service: Any = None,
) -> DriveResult:
    """Never raises: a missing setting or a Drive error comes back as the result."""
    environ = os.environ if environ is None else environ
    missing = drive_settings_missing(environ)
    if missing:
        return DriveResult(DRIVE_NOT_CONFIGURED, f"Drive not configured (missing {', '.join(missing)}).")
    if MediaIoBaseUpload is None:
        return DriveResult(
            DRIVE_NOT_CONFIGURED,
            "Drive not configured (pip install google-api-python-client google-auth).",
        )

    folder_id = environ[ENV_FOLDER_ID].strip()
    credentials_path = environ[ENV_CREDENTIALS].strip()
    id_path = os.path.join(vault_dir, DRIVE_FILE_ID_NAME)
    try:
        csv_bytes = run_json_to_csv_bytes(json_path)
        if service is None:
            service = build_drive_service(credentials_path)

        file_id = _read_file_id(id_path) or _find_existing(service, folder_id)
        if file_id:
            try:
                service.files().update(
                    fileId=file_id, media_body=_media(csv_bytes), supportsAllDrives=True,
                ).execute()
                _write_file_id(id_path, file_id)
                return DriveResult(DRIVE_UPDATED, f"Drive {DRIVE_FILE_NAME} updated.", file_id)
            except Exception as exc:
                if not (HttpError is not None and isinstance(exc, HttpError) and exc.resp.status == 404):
                    raise

        created = service.files().create(
            body={"name": DRIVE_FILE_NAME, "parents": [folder_id], "mimeType": "text/csv"},
            media_body=_media(csv_bytes), fields="id", supportsAllDrives=True,
        ).execute()
        file_id = created["id"]
        _write_file_id(id_path, file_id)
        return DriveResult(DRIVE_CREATED, f"Drive {DRIVE_FILE_NAME} created.", file_id)
    except Exception as exc:
        return DriveResult(DRIVE_ERROR, f"Drive upload failed: {exc}")
