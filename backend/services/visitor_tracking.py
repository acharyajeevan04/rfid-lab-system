"""Visitor live tracking helpers.

Visitor tags are temporary assignments. The tag EPC may also exist in DB1 as a
reusable visitor badge, but the active person-to-tag relationship lives here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import models
from backend.services.zone_engine import zone_name


def generate_visitor_id(db: Session) -> str:
    last = db.query(models.Visitor).order_by(models.Visitor.id.desc()).first()
    if last and last.visitor_id and last.visitor_id.startswith("VIS-"):
        try:
            number = int(last.visitor_id.split("-")[1]) + 1
        except (IndexError, ValueError):
            number = 1
    else:
        number = 1
    return f"VIS-{number:03d}"


def generate_event_id(db: Session) -> str:
    last = db.query(models.VisitorLocationEvent).order_by(models.VisitorLocationEvent.id.desc()).first()
    if last and last.event_id and last.event_id.startswith("MOVE-"):
        try:
            number = int(last.event_id.split("-")[1]) + 1
        except (IndexError, ValueError):
            number = 1
    else:
        number = 1
    return f"MOVE-{number:04d}"


def active_visitor_for_epc(db: Session, epc: str) -> Optional[models.Visitor]:
    return (
        db.query(models.Visitor)
        .filter(models.Visitor.epc.ilike(epc.strip().upper()))
        .filter(models.Visitor.status == "checked_in")
        .first()
    )


def record_visitor_scan(db: Session, scan: models.IncomingScan) -> tuple[Optional[models.Visitor], Optional[models.VisitorLocationEvent]]:
    """Update a visitor's latest zone when an active visitor badge is scanned."""
    visitor = active_visitor_for_epc(db, scan.epc)
    if not visitor:
        return None, None

    now = datetime.now(timezone.utc)
    from_zone = visitor.current_zone
    to_zone = scan.scanned_zone
    moved = bool(to_zone and to_zone != from_zone)

    visitor.previous_zone = from_zone if moved else visitor.previous_zone
    visitor.current_zone = to_zone or visitor.current_zone
    visitor.current_zone_name = scan.assigned_zone or zone_name(to_zone)
    visitor.last_seen_at = now

    event = models.VisitorLocationEvent(
        event_id=generate_event_id(db),
        visitor_id=visitor.visitor_id,
        epc=visitor.epc,
        from_zone=from_zone,
        to_zone=to_zone,
        to_zone_name=scan.assigned_zone or zone_name(to_zone),
        antenna_id=scan.antenna_id,
        rssi_dbm=scan.rssi_dbm,
        read_count=scan.read_count or 1,
        zone_confidence=scan.zone_confidence,
        scan_id=scan.scan_id,
        reader_id=scan.reader_id,
        source=scan.source,
        created_at=now,
    )
    db.add(event)
    return visitor, event


def visitor_summary(db: Session) -> dict:
    checked_in = db.query(func.count(models.Visitor.id)).filter(models.Visitor.status == "checked_in").scalar() or 0
    checked_out = db.query(func.count(models.Visitor.id)).filter(models.Visitor.status == "checked_out").scalar() or 0
    zone_rows = (
        db.query(models.Visitor.current_zone, func.count(models.Visitor.id))
        .filter(models.Visitor.status == "checked_in")
        .group_by(models.Visitor.current_zone)
        .all()
    )
    return {
        "checked_in": checked_in,
        "checked_out": checked_out,
        "zones": {zone or "unknown": count for zone, count in zone_rows},
    }
