import csv
import io
import logging
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Header, UploadFile, File, BackgroundTasks
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import func
from backend.database import get_db
from backend import models, schemas
from backend.config import settings
from backend.services.verification import verify_epc, generate_scan_id
from backend.services.websocket_manager import ws_manager
from backend.services.drive_parser import parse_auto
from backend.services.zone_engine import assign_zone_from_record, is_demo_e280, confidence_label
from backend.services.visitor_tracking import record_visitor_scan

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scans", tags=["scans"])

VALID_SCAN_SORT = {
    "scan_id": models.IncomingScan.scan_id,
    "epc": models.IncomingScan.epc,
    "zone": models.IncomingScan.scanned_zone,
    "date": models.IncomingScan.scan_date,
    "time": models.IncomingScan.scan_time,
    "reader": models.IncomingScan.reader_id,
    "status": models.IncomingScan.verification_status,
    "asset": models.IncomingScan.matched_asset_name,
    "source": models.IncomingScan.source,
    "rssi": models.IncomingScan.rssi_dbm,
    "confidence": models.IncomingScan.zone_confidence,
    "created_at": models.IncomingScan.created_at,
}


def _asset_for_epc(db: Session, epc: str):
    return db.query(models.Asset).filter(models.Asset.epc.ilike(epc)).first()


def _scan_from_epc_record(db: Session, epc: str, record: dict, source_default: str) -> models.IncomingScan:
    now = datetime.now(timezone.utc)
    zone = assign_zone_from_record(record)
    vr = verify_epc(epc, zone.get("scanned_zone"), db)
    asset = _asset_for_epc(db, epc)
    classification = "demo-e280" if is_demo_e280(epc) and not asset else ("registered" if asset else "unknown")
    scan = models.IncomingScan(
        scan_id=generate_scan_id(db),
        epc=epc,
        scanned_zone=zone.get("scanned_zone"),
        assigned_zone=zone.get("assigned_zone"),
        zone_confidence=zone.get("zone_confidence"),
        zone_reason=zone.get("zone_reason"),
        antenna_id=zone.get("antenna_id"),
        read_count=zone.get("read_count") or 1,
        rssi_dbm=record.get("rssi_dbm", record.get("rssi")),
        reader_id=record.get("reader_id") or settings.MC3300R_READER_ID,
        source=record.get("source", source_default),
        scan_date=record.get("scan_date") or now.strftime("%Y-%m-%d"),
        scan_time=record.get("scan_time") or now.strftime("%H:%M:%S"),
        sku=getattr(asset, "sku", None) if asset else None,
        gtin=getattr(asset, "gtin", None) if asset else None,
        tag_classification=classification,
        **vr,
    )
    return scan


@router.get("/export/csv")
def export_scans_csv(q: Optional[str] = None, status: Optional[str] = None,
                     zone: Optional[str] = None, source: Optional[str] = None,
                     db: Session = Depends(get_db)):
    """Export DB2 incoming scan records as CSV."""
    query = db.query(models.IncomingScan)
    if status:
        query = query.filter(models.IncomingScan.verification_status == status.upper())
    if zone:
        query = query.filter(models.IncomingScan.scanned_zone == zone.upper())
    if source:
        query = query.filter(models.IncomingScan.source == source)
    if q:
        like = f"%{q}%"
        query = query.filter(
            models.IncomingScan.epc.ilike(like) |
            models.IncomingScan.matched_asset_name.ilike(like) |
            models.IncomingScan.scan_id.ilike(like) |
            models.IncomingScan.sku.ilike(like) |
            models.IncomingScan.gtin.ilike(like)
        )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "scan_id", "epc", "sku", "gtin", "scanned_zone", "assigned_zone", "antenna_id",
        "read_count", "zone_confidence", "scan_date", "scan_time", "rssi_dbm",
        "reader_id", "verification_status", "matched_asset_name", "matched_record_id",
        "source", "tag_classification", "zone_reason", "notes", "created_at"
    ])
    for r in query.order_by(models.IncomingScan.id.desc()).all():
        writer.writerow([
            r.scan_id, r.epc, r.sku, r.gtin, r.scanned_zone, r.assigned_zone, r.antenna_id,
            r.read_count, r.zone_confidence, r.scan_date, r.scan_time, r.rssi_dbm,
            r.reader_id, r.verification_status, r.matched_asset_name, r.matched_record_id,
            r.source, r.tag_classification, r.zone_reason, r.notes,
            r.created_at.isoformat() if r.created_at else ""
        ])

    filename = f"db2_scans_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("", response_model=List[schemas.ScanOut])
def list_scans(q: Optional[str] = None, status: Optional[str] = None,
               zone: Optional[str] = None, source: Optional[str] = None,
               sort_by: str = "created_at", sort_dir: str = "desc",
               skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    query = db.query(models.IncomingScan)
    if status:
        query = query.filter(models.IncomingScan.verification_status == status.upper())
    if zone:
        query = query.filter(models.IncomingScan.scanned_zone == zone.upper())
    if source:
        query = query.filter(models.IncomingScan.source == source)
    if q:
        like = f"%{q}%"
        query = query.filter(
            models.IncomingScan.epc.ilike(like) |
            models.IncomingScan.matched_asset_name.ilike(like) |
            models.IncomingScan.scan_id.ilike(like) |
            models.IncomingScan.sku.ilike(like) |
            models.IncomingScan.gtin.ilike(like)
        )
    sort_col = VALID_SCAN_SORT.get(sort_by, models.IncomingScan.created_at)
    query = query.order_by(sort_col.asc() if sort_dir.lower() == "asc" else sort_col.desc())
    return query.offset(skip).limit(limit).all()


@router.get("/stats")
def scan_stats(db: Session = Depends(get_db)):
    rows = db.query(models.IncomingScan.verification_status, func.count()) \
             .group_by(models.IncomingScan.verification_status).all()
    return {r[0]: r[1] for r in rows}


@router.post("", response_model=schemas.ScanOut, status_code=201)
async def ingest_scan(scan_in: schemas.ScanCreate, background_tasks: BackgroundTasks,
                      x_scanner_key: Optional[str] = Header(None),
                      db: Session = Depends(get_db)):
    """Single scan push from MC3300R, Impinj reader, or demo scanner."""
    if settings.RFID_SCANNER_PUSH_ENABLED:
        if x_scanner_key != settings.RFID_SCANNER_API_KEY:
            raise HTTPException(401, "Invalid scanner API key")

    epc = scan_in.epc.strip().upper()
    record = scan_in.model_dump()
    record["rssi"] = scan_in.rssi_dbm
    record["antenna"] = scan_in.antenna_id
    record["count"] = scan_in.read_count or 1
    scan = _scan_from_epc_record(db, epc, record, "scanner")
    db.add(scan)
    db.flush()
    visitor, movement = record_visitor_scan(db, scan)
    db.commit()
    db.refresh(scan)
    if visitor:
        db.refresh(visitor)

    background_tasks.add_task(ws_manager.broadcast, "new_scan", {
        "scan_id": scan.scan_id, "epc": scan.epc, "zone": scan.scanned_zone,
        "assigned_zone": scan.assigned_zone, "zone_confidence": scan.zone_confidence,
        "confidence_label": confidence_label(scan.zone_confidence),
        "antenna_id": scan.antenna_id, "read_count": scan.read_count,
        "asset": scan.matched_asset_name, "status": scan.verification_status,
        "time": scan.scan_time, "date": scan.scan_date,
        "notes": scan.notes, "reader": scan.reader_id, "source": scan.source,
    })

    if scan.verification_status in ("MISMATCH", "UNKNOWN"):
        background_tasks.add_task(ws_manager.broadcast, "alert", {
            "scan_id": scan.scan_id, "epc": scan.epc,
            "status": scan.verification_status, "message": scan.notes,
            "zone": scan.scanned_zone,
        })
    if visitor and movement:
        background_tasks.add_task(ws_manager.broadcast, "visitor_moved", {
            "visitor_id": visitor.visitor_id,
            "display_name": visitor.display_name,
            "badge_label": visitor.badge_label,
            "epc": visitor.epc,
            "from_zone": movement.from_zone,
            "to_zone": movement.to_zone,
            "to_zone_name": movement.to_zone_name,
            "zone_confidence": movement.zone_confidence,
            "confidence_label": confidence_label(movement.zone_confidence),
            "antenna_id": movement.antenna_id,
            "rssi_dbm": movement.rssi_dbm,
            "scan_id": movement.scan_id,
            "reader": movement.reader_id,
            "last_seen_at": movement.created_at.isoformat() if movement.created_at else None,
        })
    return scan


@router.post("/bulk", status_code=201)
async def bulk_upload(background_tasks: BackgroundTasks,
                      file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload CSV file — auto-detects Zebra or Impinj format."""
    content = await file.read()
    text = content.decode("utf-8", errors="replace")

    try:
        fmt, records = parse_auto(text, file.filename or "")
    except Exception as e:
        raise HTTPException(400, f"CSV parse error: {e}")

    imported = 0
    errors = []

    for rec in records:
        epc = rec.get("epc", "").strip().upper()
        if not epc:
            continue
        try:
            scan = _scan_from_epc_record(db, epc, rec, "csv_upload")
            db.add(scan)
            db.flush()
            record_visitor_scan(db, scan)
            imported += 1
        except Exception as e:
            db.rollback()
            errors.append(str(e)[:120])

    db.commit()

    background_tasks.add_task(ws_manager.broadcast, "bulk_import", {
        "count": imported, "source": fmt, "filename": file.filename,
    })

    return {
        "imported": imported,
        "format": fmt,
        "errors": errors[:5],
        "message": f"Imported {imported} scans from {file.filename} ({fmt} format)",
    }
