"""
google_drive.py
Robust Google Drive sync for Zebra + Impinj scanner CSV files.

Fixes:
- Finds CSV files even when Google Drive MIME type is not text/csv.
- Handles Google Sheets by exporting as CSV.
- Reprocesses an existing Drive file if size or modifiedTime changed.
- Prevents duplicate scan rows when a file is reprocessed.
- Cleans folder IDs if a full Google Drive URL was pasted into .env.
"""

import io
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session
from backend.config import settings
from backend import models
from backend.services.verification import verify_epc, generate_scan_id
from backend.services.drive_parser import parse_auto
from backend.services.zone_engine import assign_zone_from_record, is_demo_e280
from backend.services.visitor_tracking import record_visitor_scan

logger = logging.getLogger(__name__)

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    GOOGLE_OK = True
except ImportError:
    GOOGLE_OK = False
    logger.warning("Google client packages missing. Install google-auth google-auth-oauthlib google-api-python-client")

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

CSV_MIME_TYPES = {
    "text/csv",
    "text/plain",
    "application/csv",
    "application/vnd.ms-excel",
    "application/octet-stream",
}
GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"


def _clean_folder_id(value: str | None) -> str:
    """Accept either a raw folder ID or a full Drive folder URL."""
    v = (value or "").strip()
    if not v:
        return ""
    m = re.search(r"/folders/([A-Za-z0-9_-]+)", v)
    if m:
        return m.group(1)
    # Remove common query string if someone pasted ID?usp=sharing
    if "?" in v:
        v = v.split("?", 1)[0]
    return v.strip()


def _sync_marker(file_info: dict) -> str:
    return f"modifiedTime={file_info.get('modifiedTime') or ''}"


class GoogleDriveService:
    def __init__(self):
        self._svc = None
        self._connected = False
        self._last_sync: Optional[str] = None

    def authenticate(self) -> bool:
        if not GOOGLE_OK:
            return False

        creds = None
        token_path = Path(settings.GOOGLE_TOKEN_FILE)
        credentials_path = Path(settings.GOOGLE_CREDENTIALS_FILE)

        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    logger.error(f"Token refresh failed: {e}")
                    creds = None

            if not creds:
                if not credentials_path.exists():
                    logger.error("credentials.json not found in project root.")
                    return False
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
                creds = flow.run_local_server(port=0)

            token_path.write_text(creds.to_json())

        try:
            self._svc = build("drive", "v3", credentials=creds)
            self._connected = True
            logger.info("Google Drive authenticated successfully.")
            return True
        except Exception as e:
            logger.error(f"Drive build failed: {e}")
            self._connected = False
            return False

    @property
    def connected(self) -> bool:
        return self._connected and self._svc is not None

    @property
    def last_sync(self) -> Optional[str]:
        return self._last_sync

    def _list_csvs(self, folder_id: str) -> list[dict]:
        """List CSV-like files in the configured folder.

        Scanner files do not always appear as text/csv in Drive, so we list all
        non-trashed files and then filter by filename/MIME locally.
        """
        if not self.connected:
            return []

        folder_id = _clean_folder_id(folder_id)
        if not folder_id:
            return []

        files: list[dict] = []
        page_token = None

        try:
            while True:
                q = f"'{folder_id}' in parents and trashed=false"
                response = self._svc.files().list(
                    q=q,
                    fields="nextPageToken, files(id,name,size,modifiedTime,mimeType,createdTime)",
                    orderBy="modifiedTime desc",
                    pageSize=1000,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                ).execute()

                for f in response.get("files", []):
                    name = (f.get("name") or "").lower()
                    mime = (f.get("mimeType") or "").lower()

                    is_csv_like = (
                        name.endswith(".csv")
                        or "rfid" in name
                        or "impinj" in name
                        or "item_test" in name
                        or "csv" in name
                        or mime in CSV_MIME_TYPES
                        or mime == GOOGLE_SHEET_MIME
                    )

                    # Do not try to download folders or random Google Docs.
                    if is_csv_like and mime != "application/vnd.google-apps.folder":
                        files.append(f)

                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            logger.info(f"Drive folder {folder_id}: found {len(files)} CSV-like file(s).")
            return files

        except Exception as e:
            logger.exception(f"Drive list error for folder {folder_id}: {e}")
            return []

    def _download(self, file_info: dict) -> Optional[str]:
        if not self.connected:
            return None

        file_id = file_info.get("id")
        file_name = file_info.get("name", "")
        mime = (file_info.get("mimeType") or "").lower()

        try:
            if mime == GOOGLE_SHEET_MIME:
                request = self._svc.files().export_media(
                    fileId=file_id,
                    mimeType="text/csv",
                )
            else:
                request = self._svc.files().get_media(fileId=file_id)

            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            buffer.seek(0)
            return buffer.read().decode("utf-8-sig", errors="replace")

        except Exception as e:
            logger.exception(f"Drive download failed for {file_name} ({file_id}): {e}")
            return None

    def sync(self, db: Session) -> dict:
        result = {
            "success": False,
            "files_checked": 0,
            "new_files": 0,
            "scans_imported": 0,
            "zebra_files": 0,
            "impinj_files": 0,
            "errors": [],
        }

        if not self.connected and not self.authenticate():
            result["errors"].append("Google Drive authentication failed. Check credentials.json and token.json.")
            return result

        folders = []
        zebra_id = _clean_folder_id(settings.GOOGLE_DRIVE_ZEBRA_FOLDER_ID)
        impinj_id = _clean_folder_id(settings.GOOGLE_DRIVE_IMPINJ_FOLDER_ID)

        if zebra_id:
            folders.append(("zebra", zebra_id))
        if impinj_id:
            folders.append(("impinj", impinj_id))

        if not folders:
            result["errors"].append("No Drive folder IDs configured in .env.")
            return result

        for folder_label, folder_id in folders:
            files = self._list_csvs(folder_id)
            result["files_checked"] += len(files)

            for file_info in files:
                fid = file_info.get("id")
                fname = file_info.get("name", "")
                current_size = _safe_int(file_info.get("size"))
                current_marker = _sync_marker(file_info)

                existing = db.query(models.DriveSync).filter_by(drive_file_id=fid).first()

                # Skip only when both size and modifiedTime match the previous import.
                # This fixes the issue where a scanner updates the same CSV file in Drive.
                if (
                    existing
                    and existing.status == "imported"
                    and existing.file_size == current_size
                    and (existing.error_message or "") == current_marker
                ):
                    continue

                result["new_files"] += 1
                text = self._download(file_info)
                if not text:
                    error = f"Download failed: {fname}"
                    result["errors"].append(error)
                    _upsert_sync(db, file_info, "error", 0, error)
                    continue

                try:
                    fmt, records = parse_auto(text, fname)
                except Exception as e:
                    error = f"Parse error {fname}: {e}"
                    result["errors"].append(error)
                    _upsert_sync(db, file_info, "error", 0, error)
                    continue

                if fmt == "zebra":
                    result["zebra_files"] += 1
                elif fmt == "impinj":
                    result["impinj_files"] += 1
                else:
                    logger.warning(f"Skipped unknown CSV format: {fname}")

                imported = _import(records, db)
                result["scans_imported"] += imported
                _upsert_sync(db, file_info, "imported", imported, current_marker)
                logger.info(f"Drive sync: imported {imported} new scan(s) from {fname} ({fmt})")

        self._last_sync = datetime.now(timezone.utc).isoformat()
        result["success"] = True
        logger.info(
            "Drive sync complete: checked=%s processed=%s imported=%s zebra=%s impinj=%s errors=%s",
            result["files_checked"], result["new_files"], result["scans_imported"],
            result["zebra_files"], result["impinj_files"], len(result["errors"]),
        )
        return result


def _import(records: list[dict], db: Session) -> int:
    count = 0

    for rec in records:
        epc = (rec.get("epc") or "").strip().upper()
        if not epc:
            continue

        try:
            zone = assign_zone_from_record(rec)
            scan_date = rec.get("scan_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            scan_time = rec.get("scan_time") or datetime.now(timezone.utc).strftime("%H:%M:%S")
            reader_id = rec.get("reader_id", "unknown")
            source = rec.get("source", "drive")

            # Duplicate guard: safe to reprocess Drive files without duplicating old scans.
            duplicate = (
                db.query(models.IncomingScan)
                .filter(models.IncomingScan.epc == epc)
                .filter(models.IncomingScan.scan_date == scan_date)
                .filter(models.IncomingScan.scan_time == scan_time)
                .filter(models.IncomingScan.reader_id == reader_id)
                .filter(models.IncomingScan.source == source)
                .first()
            )
            if duplicate:
                continue

            vr = verify_epc(epc, zone.get("scanned_zone"), db)
            asset = db.query(models.Asset).filter(models.Asset.epc.ilike(epc)).first()
            now = datetime.now(timezone.utc)

            scan = models.IncomingScan(
                scan_id=generate_scan_id(db),
                epc=epc,
                scanned_zone=zone.get("scanned_zone"),
                assigned_zone=zone.get("assigned_zone"),
                antenna_id=zone.get("antenna_id"),
                read_count=zone.get("read_count") or rec.get("count") or 1,
                zone_confidence=zone.get("zone_confidence"),
                zone_reason=zone.get("zone_reason"),
                rssi_dbm=rec.get("rssi"),
                reader_id=reader_id,
                source=source,
                scan_date=scan_date,
                scan_time=scan_time,
                sku=getattr(asset, "sku", None) if asset else None,
                gtin=getattr(asset, "gtin", None) if asset else None,
                tag_classification="registered" if asset else ("demo-e280" if is_demo_e280(epc) else "unknown"),
                **vr,
            )
            db.add(scan)
            db.flush()
            record_visitor_scan(db, scan)
            count += 1

        except Exception as e:
            db.rollback()
            logger.exception(f"Import skipped for EPC {epc}: {e}")

    db.commit()
    return count


def _upsert_sync(db: Session, file_info: dict, status: str, scans: int = 0, marker_or_error: str = ""):
    now = datetime.now(timezone.utc)
    fid = file_info.get("id")
    existing = db.query(models.DriveSync).filter_by(drive_file_id=fid).first()

    if existing:
        existing.file_name = file_info.get("name", existing.file_name)
        existing.file_size = _safe_int(file_info.get("size"))
        existing.status = status
        existing.scans_imported = scans
        existing.error_message = marker_or_error or None
        if status == "imported":
            existing.imported_at = now
    else:
        db.add(models.DriveSync(
            drive_file_id=fid,
            file_name=file_info.get("name", ""),
            file_size=_safe_int(file_info.get("size")),
            scans_imported=scans,
            status=status,
            error_message=marker_or_error or None,
            imported_at=now if status == "imported" else None,
        ))
    db.commit()


def _safe_int(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


drive_service = GoogleDriveService()
