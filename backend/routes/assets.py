from typing import Optional, List
import csv
import io
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import func
from backend.database import get_db
from backend import models, schemas
from backend.services.zone_engine import is_demo_e280

router = APIRouter(prefix="/api/assets", tags=["assets"])

VALID_ASSET_SORT = {
    "record_id": models.Asset.record_id,
    "asset_name": models.Asset.asset_name,
    "epc": models.Asset.epc,
    "tag_id": models.Asset.tag_id,
    "sku": models.Asset.sku,
    "gtin": models.Asset.gtin,
    "zone": models.Asset.zone_code,
    "category": models.Asset.category,
    "status": models.Asset.status,
    "last_scanned": models.Asset.last_scanned_iso,
    "updated_at": models.Asset.updated_at,
}

@router.get("", response_model=List[schemas.AssetOut])
def list_assets(q: Optional[str] = None, zone: Optional[str] = None,
                category: Optional[str] = None, status: Optional[str] = None,
                sort_by: str = "record_id", sort_dir: str = "asc",
                skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    query = db.query(models.Asset)
    if zone:
        query = query.filter(models.Asset.zone_code == zone.upper())
    if category:
        query = query.filter(models.Asset.category == category)
    if status:
        query = query.filter(models.Asset.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(
            models.Asset.asset_name.ilike(like) | models.Asset.epc.ilike(like) |
            models.Asset.tag_id.ilike(like) | models.Asset.component_location.ilike(like) |
            models.Asset.sku.ilike(like) | models.Asset.gtin.ilike(like)
        )
    sort_col = VALID_ASSET_SORT.get(sort_by, models.Asset.record_id)
    query = query.order_by(sort_col.desc() if sort_dir.lower() == "desc" else sort_col.asc())
    return query.offset(skip).limit(limit).all()

@router.get("/count")
def count_assets(zone: Optional[str] = None, category: Optional[str] = None,
                 status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(func.count(models.Asset.id))
    if zone: query = query.filter(models.Asset.zone_code == zone.upper())
    if category: query = query.filter(models.Asset.category == category)
    if status: query = query.filter(models.Asset.status == status)
    return {"count": query.scalar()}

@router.get("/epc/{epc}", response_model=schemas.VerifyResult)
def verify_epc_endpoint(epc: str, scanned_zone: Optional[str] = None, db: Session = Depends(get_db)):
    from backend.services.verification import verify_epc
    epc_upper = epc.strip().upper()
    asset = db.query(models.Asset).filter(models.Asset.epc.ilike(epc_upper)).first()
    vr = verify_epc(epc_upper, scanned_zone, db)
    last_scan = (db.query(models.IncomingScan)
                 .filter(models.IncomingScan.epc.ilike(epc_upper))
                 .order_by(models.IncomingScan.id.desc()).first())
    status = vr["verification_status"]
    msgs = {
        "MATCHED": f"Tag verified. Asset: {vr['matched_asset_name']}",
        "MISMATCH": f"Zone mismatch — {vr['notes']}",
        "DUPLICATE": f"Duplicate read — {vr['matched_asset_name']}",
        "UNKNOWN": "EPC not found in master database",
    }
    return schemas.VerifyResult(
        epc=epc_upper, found=asset is not None,
        verification_status=status, asset=asset, last_scan=last_scan,
        message=msgs.get(status, ""),
    )

@router.get("/unmapped-tags", response_model=List[schemas.UnmappedTagOut])
def list_unmapped_tags(prefix: Optional[str] = None, limit: int = 100, db: Session = Depends(get_db)):
    """Review tags in DB2 that are not registered in DB1 master.

    Use this for the new cleanup step: identify real tags, decode if needed, then
    register real tags in DB1 or remove/destroy unused demo tags.
    """
    asset_epcs = db.query(models.Asset.epc).subquery()
    q = (db.query(
            models.IncomingScan.epc,
            func.count(models.IncomingScan.id).label("read_events"),
            func.min(models.IncomingScan.created_at).label("first_seen"),
            func.max(models.IncomingScan.created_at).label("last_seen"),
            func.max(models.IncomingScan.rssi_dbm).label("strongest_rssi"),
        )
        .filter(~models.IncomingScan.epc.in_(asset_epcs))
        .group_by(models.IncomingScan.epc)
        .order_by(func.count(models.IncomingScan.id).desc()))
    if prefix:
        q = q.filter(models.IncomingScan.epc.ilike(f"{prefix.upper()}%"))
    rows = q.limit(limit).all()
    out = []
    for epc, read_events, first_seen, last_seen, strongest_rssi in rows:
        demo = is_demo_e280(epc)
        recommendation = "Likely local/demo E280 tag — verify physically; destroy/remove if not attached to real item" if demo else "Review with EPC decoder and map to SKU/GTIN if real"
        out.append(schemas.UnmappedTagOut(
            epc=epc,
            read_events=read_events,
            first_seen=first_seen.isoformat() if first_seen else None,
            last_seen=last_seen.isoformat() if last_seen else None,
            strongest_rssi=strongest_rssi,
            likely_demo_e280=demo,
            recommendation=recommendation,
        ))
    return out

@router.get("/unmapped-tags/export/csv")
def export_unmapped_tags(db: Session = Depends(get_db)):
    rows = list_unmapped_tags(limit=10000, db=db)
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["epc", "read_events", "first_seen", "last_seen", "strongest_rssi", "likely_demo_e280", "recommendation"])
    for r in rows:
        writer.writerow([r.epc, r.read_events, r.first_seen, r.last_seen, r.strongest_rssi, r.likely_demo_e280, r.recommendation])
    filename = f"unmapped_tags_review_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

@router.post("/naloxone-demo")
def seed_naloxone_demo(db: Session = Depends(get_db)):
    """Create demo naloxone kit records in DB1."""
    zones = [
        ("A", "Front RFID Area", "Wall Cabinet A", "Emergency Response Cabinet A", 1),
        ("B", "Storage Area", "Storage Shelf B", "Back Storage Naloxone Shelf", 2),
        ("C", "Workbench Area", "Front Desk Kit Box", "Front Desk Access Point", 3),
        ("D", "Entrance / Overflow", "Entry Hallway Station", "Public Access Response Station", 4),
    ]
    statuses = ["Available", "Available", "Active", "Need to Stock Up"]
    inserted = 0; skipped = 0
    for i in range(1, 13):
        zone_code, zone_name, sub_zone, location, ant = zones[(i - 1) % len(zones)]
        status = statuses[(i - 1) % len(statuses)]
        exp_month = ((i - 1) % 12) + 1
        rec = {
            "record_id": f"NAL-{i:03d}",
            "asset_name": f"Naloxone Kit {i:03d}",
            "epc": f"E280117000000000000{1000 + i:06d}",
            "tag_id": f"NALOXONE-RFID-{i:03d}",
            "sku": f"NALOXONE-KIT-{i:03d}",
            "gtin": f"0030000000{i:04d}",
            "component_location": location,
            "zone_code": zone_code,
            "zone_name": zone_name,
            "sub_zone": sub_zone,
            "category": "naloxone-kit",
            "manufacturer": "Demo Medical Supply",
            "model": "Naloxone Nasal Spray 4mg",
            "unit": "kit",
            "qty": 1,
            "status": status,
            "reader_id": "mc3300r-lab01",
            "reader_type": "Zebra MC3300R Handheld",
            "verification_method": "RFID tag registration",
            "manual_check": "Verified",
            "expected_antenna_id": ant,
            "tag_source": "demo",
            "item_type": "naloxone-kit",
            "comments": f"Demo naloxone rescue kit. Expiration: 2027-{exp_month:02d}-28. Replace after use or expiration.",
        }
        exists = db.query(models.Asset).filter((models.Asset.epc == rec["epc"]) | (models.Asset.record_id == rec["record_id"])).first()
        if exists:
            # Backfill new SKU/GTIN fields if old demo records already exist.
            for k in ["sku", "gtin", "expected_antenna_id", "tag_source", "item_type", "zone_name"]:
                if getattr(exists, k, None) in (None, ""):
                    setattr(exists, k, rec[k])
            skipped += 1
            continue
        db.add(models.Asset(**rec)); inserted += 1
    db.commit()
    return {"success": True, "inserted": inserted, "skipped": skipped, "message": f"Naloxone demo kit setup complete: {inserted} inserted, {skipped} already existed/backfilled."}

@router.post("/key-demo-items")
def seed_key_demo_items(db: Session = Depends(get_db)):
    """Create clean tagged items for demo, especially ITEM Battery 1 with SKU/GTIN."""
    items = [
        dict(record_id="ITEM-BAT-001", asset_name="ITEM Battery 1", epc="E2801191A503006540B94811", tag_id="BATTERY-ITEM-RFID-001", sku="BATTERY-001", gtin="00071600000001", zone_code="B", zone_name="Storage Area", sub_zone="Back storage shelf", component_location="Storage Area", category="battery-cell", manufacturer="Demo Battery", model="Li-ion Demo Cell", unit="item", qty=1, status="Active", expected_antenna_id=2, tag_source="real-or-demo", item_type="battery"),
        dict(record_id="ITEM-NAL-001", asset_name="ITEM Naloxone Training Kit", epc="E280117000000000000001001", tag_id="NALOXONE-RFID-001", sku="NALOXONE-TRAINING-001", gtin="00300000001001", zone_code="A", zone_name="Front RFID Area", sub_zone="Demo table", component_location="Demo Area", category="naloxone-kit", manufacturer="Demo Medical Supply", model="Training Kit", unit="kit", qty=1, status="Available", expected_antenna_id=1, tag_source="demo", item_type="naloxone-kit"),
    ]
    inserted = 0; updated = 0
    for rec in items:
        existing = db.query(models.Asset).filter((models.Asset.record_id == rec["record_id"]) | (models.Asset.epc == rec["epc"])).first()
        if existing:
            for k, v in rec.items():
                setattr(existing, k, v)
            updated += 1
        else:
            rec["reader_id"] = "impinj-r700-lab01"; rec["reader_type"] = "Impinj R700 Fixed Reader"; rec["verification_method"] = "Master database mapping"; rec["manual_check"] = "Needs physical confirmation"; rec["comments"] = "Clean key item for demo; verify physical tag before final demo."
            db.add(models.Asset(**rec)); inserted += 1
    db.commit()
    return {"success": True, "inserted": inserted, "updated": updated, "message": f"Key demo items ready: {inserted} inserted, {updated} updated. ITEM Battery 1 is mapped to SKU BATTERY-001."}

@router.get("/export/csv")
def export_assets_csv(q: Optional[str] = None, zone: Optional[str] = None,
                      category: Optional[str] = None, status: Optional[str] = None,
                      db: Session = Depends(get_db)):
    """Export DB1 master asset records as CSV. DB1 contains unique tags only, not scan events."""
    query = db.query(models.Asset)
    if zone:
        query = query.filter(models.Asset.zone_code == zone.upper())
    if category:
        query = query.filter(models.Asset.category == category)
    if status:
        query = query.filter(models.Asset.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(
            models.Asset.asset_name.ilike(like) | models.Asset.epc.ilike(like) |
            models.Asset.tag_id.ilike(like) | models.Asset.component_location.ilike(like) |
            models.Asset.sku.ilike(like) | models.Asset.gtin.ilike(like)
        )

    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow([
        "record_id", "asset_name", "epc", "tag_id", "sku", "gtin", "category", "status",
        "zone_code", "zone_name", "sub_zone", "component_location", "expected_antenna_id",
        "manufacturer", "model", "unit", "qty", "tag_source", "item_type", "reader_id",
        "reader_type", "scan_date", "scan_time", "last_scanned_iso", "comments"
    ])
    for a in query.order_by(models.Asset.record_id).all():
        writer.writerow([
            a.record_id, a.asset_name, a.epc, a.tag_id, a.sku, a.gtin, a.category, a.status,
            a.zone_code, a.zone_name, a.sub_zone, a.component_location, a.expected_antenna_id,
            a.manufacturer, a.model, a.unit, a.qty, a.tag_source, a.item_type, a.reader_id,
            a.reader_type, a.scan_date, a.scan_time, a.last_scanned_iso, a.comments
        ])
    filename = f"db1_master_assets_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

@router.get("/{record_id}", response_model=schemas.AssetOut)
def get_asset(record_id: str, db: Session = Depends(get_db)):
    a = db.query(models.Asset).filter(models.Asset.record_id == record_id).first()
    if not a:
        raise HTTPException(404, f"Asset {record_id} not found")
    return a

@router.put("/{record_id}", response_model=schemas.AssetOut)
def update_asset(record_id: str, upd: schemas.AssetUpdate, db: Session = Depends(get_db)):
    a = db.query(models.Asset).filter(models.Asset.record_id == record_id).first()
    if not a:
        raise HTTPException(404, f"Asset {record_id} not found")
    for k, v in upd.model_dump(exclude_none=True).items():
        setattr(a, k, v)
    db.commit(); db.refresh(a)
    return a

@router.post("", response_model=schemas.AssetOut, status_code=201)
def create_asset(asset_in: schemas.AssetCreate, db: Session = Depends(get_db)):
    if db.query(models.Asset).filter(models.Asset.epc == asset_in.epc).first():
        raise HTTPException(409, "EPC already exists in DB1")
    a = models.Asset(**asset_in.model_dump())
    db.add(a); db.commit(); db.refresh(a)
    return a
