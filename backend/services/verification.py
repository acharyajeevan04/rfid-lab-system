"""EPC verification — matches DB2 incoming scans against DB1 master tags."""

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from backend import models
from backend.services.zone_engine import normalize_zone_code


def verify_epc(epc: str, scanned_zone: str | None, db: Session) -> dict:
    """Classify one scanned EPC against DB1.

    Checks, in order: does the EPC exist in the master table at all
    (UNKNOWN if not), has it already been matched/duplicated today
    (DUPLICATE), does its detected zone disagree with its expected zone
    (MISMATCH), otherwise MATCHED. Returns a dict with
    verification_status/matched_asset_name/matched_record_id/notes, ready
    to spread into an IncomingScan row.
    """
    epc_upper = epc.strip().upper()
    zone_code = normalize_zone_code(scanned_zone)

    asset = db.query(models.Asset).filter(models.Asset.epc.ilike(epc_upper)).first()
    if not asset:
        return {
            "verification_status": "UNKNOWN",
            "matched_asset_name": "— Not in Master Table —",
            "matched_record_id": "—",
            "notes": "EPC not in master database — review unmapped tag before demo/use",
        }

    # Duplicate check: same EPC already matched today.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dup = db.query(models.IncomingScan).filter(
        models.IncomingScan.epc.ilike(epc_upper),
        models.IncomingScan.scan_date == today,
        models.IncomingScan.verification_status.in_(["MATCHED", "DUPLICATE"]),
    ).first()
    if dup:
        return {
            "verification_status": "DUPLICATE",
            "matched_asset_name": asset.asset_name,
            "matched_record_id": asset.record_id,
            "notes": "Tag read multiple times today/session",
        }

    # Zone mismatch check. DB1 stores assigned/expected zone; DB2 stores detected zone.
    asset_zone = normalize_zone_code(asset.zone_code)
    if zone_code and asset_zone and zone_code != asset_zone:
        return {
            "verification_status": "MISMATCH",
            "matched_asset_name": asset.asset_name,
            "matched_record_id": asset.record_id,
            "notes": f"ALERT: Master DB Zone {asset_zone}, detected Zone {zone_code} — verify item location",
        }

    return {
        "verification_status": "MATCHED",
        "matched_asset_name": asset.asset_name,
        "matched_record_id": asset.record_id,
        "notes": "Tag verified — EPC exists in DB1 master database",
    }


def generate_scan_id(db: Session) -> str:
    """Return the next sequential SCAN-#### id based on the last row in DB2."""
    last = db.query(models.IncomingScan).order_by(models.IncomingScan.id.desc()).first()
    if last and last.scan_id and last.scan_id.startswith("SCAN-"):
        try:
            n = int(last.scan_id.split("-")[1]) + 1
        except (IndexError, ValueError):
            n = 1
    else:
        n = 1
    return f"SCAN-{n:04d}"
