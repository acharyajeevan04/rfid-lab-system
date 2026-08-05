from typing import List
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func
from backend.database import get_db
from backend import models, schemas
from backend.services.google_drive import drive_service
from backend.services.websocket_manager import ws_manager
from backend.config import settings
from backend.services.zone_engine import ANTENNA_ZONE_MAP, confidence_label

# ── Sessions ──────────────────────────────────────────────────────────────────
sessions_router = APIRouter(prefix="/api/sessions", tags=["sessions"])

@sessions_router.get("", response_model=List[schemas.SessionOut])
def list_sessions(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    return db.query(models.ScanSession).order_by(models.ScanSession.id.desc()).offset(skip).limit(limit).all()

@sessions_router.post("", response_model=schemas.SessionOut, status_code=201)
def create_session(s_in: schemas.SessionCreate, db: Session = Depends(get_db)):
    if not s_in.session_id:
        count = db.query(func.count(models.ScanSession.id)).scalar() + 1
        session_id = f"SESS-{count:03d}"
    else:
        session_id = s_in.session_id
    s = models.ScanSession(session_id=session_id, **s_in.model_dump(exclude={"session_id"}))
    db.add(s); db.commit(); db.refresh(s)
    return s


# ── Dashboard ─────────────────────────────────────────────────────────────────
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

@dashboard_router.get("", response_model=schemas.DashboardStats)
def get_dashboard(db: Session = Depends(get_db)):
    total_assets = db.query(func.count(models.Asset.id)).scalar()
    active_assets = db.query(func.count(models.Asset.id)).filter(models.Asset.status == "Active").scalar()
    total_scans = db.query(func.count(models.IncomingScan.id)).scalar()
    total_sessions = db.query(func.count(models.ScanSession.id)).scalar()
    total_reads = db.query(func.sum(models.ScanSession.total_reads)).scalar() or 0
    alerts = db.query(func.count(models.IncomingScan.id)).filter(
        models.IncomingScan.verification_status.in_(["MISMATCH", "UNKNOWN"])).scalar()

    zone_rows = db.query(models.Asset.zone_code, func.count()).group_by(models.Asset.zone_code).all()
    zone_names = {"A": "Front RFID Area", "B": "Storage Area", "C": "Workbench Area", "D": "Entrance / Overflow"}
    zone_breakdown = [
        schemas.ZoneStat(zone_code=z, zone_name=zone_names.get(z, z), count=c,
                         pct=round(c / total_assets * 100, 1) if total_assets else 0)
        for z, c in zone_rows
    ]

    cat_rows = db.query(models.Asset.category, func.count()).group_by(models.Asset.category).all()
    st_rows = db.query(models.Asset.status, func.count()).group_by(models.Asset.status).all()
    sv_rows = db.query(models.IncomingScan.verification_status, func.count()).group_by(models.IncomingScan.verification_status).all()

    last_ds = db.query(models.DriveSync).filter(models.DriveSync.status == "imported") \
                .order_by(models.DriveSync.imported_at.desc()).first()

    return schemas.DashboardStats(
        total_assets=total_assets, active_assets=active_assets,
        total_scans=total_scans, total_sessions=total_sessions,
        total_reads=int(total_reads), alerts=alerts,
        zone_breakdown=zone_breakdown,
        category_breakdown={c: n for c, n in cat_rows},
        status_breakdown={s: n for s, n in st_rows},
        scan_status_breakdown={s: n for s, n in sv_rows},
        last_sync=last_ds.imported_at.isoformat() if last_ds and last_ds.imported_at else None,
        drive_connected=drive_service.connected,
    )



# ── Zone Map / Confidence ───────────────────────────────────────────────────
zone_router = APIRouter(prefix="/api/zones", tags=["zones"])

@zone_router.get("/current")
def current_zone_map(db: Session = Depends(get_db)):
    """Latest known location per EPC with antenna/read-rate/RSSI confidence."""
    latest_ids = (
        db.query(func.max(models.IncomingScan.id).label("id"))
        .group_by(models.IncomingScan.epc)
        .subquery()
    )
    rows = (
        db.query(models.IncomingScan)
        .join(latest_ids, models.IncomingScan.id == latest_ids.c.id)
        .order_by(models.IncomingScan.created_at.desc())
        .limit(250)
        .all()
    )
    zones = {meta["zone_code"]: {**meta, "antenna_id": ant, "items": []} for ant, meta in ANTENNA_ZONE_MAP.items()}
    unknown = []
    for r in rows:
        item = {
            "epc": r.epc,
            "asset_name": r.matched_asset_name,
            "status": r.verification_status,
            "scanned_zone": r.scanned_zone,
            "assigned_zone": r.assigned_zone,
            "antenna_id": r.antenna_id,
            "read_count": r.read_count or 1,
            "rssi_dbm": r.rssi_dbm,
            "zone_confidence": r.zone_confidence,
            "confidence_label": confidence_label(r.zone_confidence),
            "last_seen": r.created_at.isoformat() if r.created_at else None,
        }
        if r.scanned_zone in zones:
            zones[r.scanned_zone]["items"].append(item)
        else:
            unknown.append(item)
    return {
        "antenna_zone_map": ANTENNA_ZONE_MAP,
        "zones": list(zones.values()),
        "unknown": unknown,
        "summary": {
            "tracked_epcs": len(rows),
            "unknown_zone_count": len(unknown),
        }
    }

# ── Drive ─────────────────────────────────────────────────────────────────────
drive_router = APIRouter(prefix="/api/drive", tags=["drive"])

@drive_router.get("/status", response_model=schemas.DriveStatus)
def drive_status(db: Session = Depends(get_db)):
    total_files = db.query(func.count(models.DriveSync.id)).filter(models.DriveSync.status == "imported").scalar()
    total_scans = db.query(func.sum(models.DriveSync.scans_imported)).scalar() or 0
    last = db.query(models.DriveSync).filter(models.DriveSync.status == "imported") \
             .order_by(models.DriveSync.imported_at.desc()).first()
    return schemas.DriveStatus(
        connected=drive_service.connected,
        account=settings.GOOGLE_DRIVE_ACCOUNT,
        zebra_folder_id=settings.GOOGLE_DRIVE_ZEBRA_FOLDER_ID or "not configured",
        impinj_folder_id=settings.GOOGLE_DRIVE_IMPINJ_FOLDER_ID or "not configured",
        last_sync=last.imported_at.isoformat() if last and last.imported_at else drive_service.last_sync,
        total_files_synced=total_files,
        total_scans_imported=int(total_scans),
    )

@drive_router.post("/sync", response_model=schemas.DriveSyncResult)
async def trigger_sync(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    result = drive_service.sync(db)
    if result["scans_imported"] > 0:
        background_tasks.add_task(ws_manager.broadcast, "drive_sync", {
            "scans_imported": result["scans_imported"],
            "new_files": result["new_files"],
            "zebra_files": result["zebra_files"],
            "impinj_files": result["impinj_files"],
            "message": f"Drive sync: {result['scans_imported']} new scans from {result['new_files']} files",
        })
    if result["success"]:
        msg = (
            f"Sync complete — imported {result['scans_imported']} new scans. "
            f"Checked {result['files_checked']} Drive file(s); processed "
            f"{result['new_files']} new/updated file(s). "
            f"Formats processed: {result['zebra_files']} Zebra + {result['impinj_files']} Impinj."
        )
        if result.get("errors"):
            msg += " Errors: " + "; ".join(result["errors"][:2])
    else:
        msg = "Sync failed: " + "; ".join(result["errors"][:2])

    return schemas.DriveSyncResult(
        success=result["success"],
        files_checked=result["files_checked"],
        new_files=result["new_files"],
        scans_imported=result["scans_imported"],
        zebra_files=result["zebra_files"],
        impinj_files=result["impinj_files"],
        errors=result["errors"],
        message=msg,
    )

@drive_router.post("/connect")
def connect_drive():
    ok = drive_service.authenticate()
    return {
        "connected": ok,
        "message": "Connected to Google Drive successfully." if ok
                   else "Authentication failed. Ensure credentials.json is in project root and Google Drive API is enabled.",
    }
