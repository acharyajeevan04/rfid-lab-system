"""Visitor badge endpoints — check-in/out temporary RFID badges and track
their live zone as their tag gets read, reusing the same zone-assignment
pipeline as regular asset scans (see backend/services/visitor_tracking.py)."""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from backend import models, schemas
from backend.database import get_db
from backend.services.visitor_tracking import generate_visitor_id, visitor_summary
from backend.services.websocket_manager import ws_manager

router = APIRouter(prefix="/api/visitors", tags=["visitors"])


@router.get("", response_model=List[schemas.VisitorOut])
def list_visitors(status: Optional[str] = "checked_in", db: Session = Depends(get_db)):
    """List visitor badges, optionally filtered by status (default: currently checked in)."""
    query = db.query(models.Visitor)
    if status:
        query = query.filter(models.Visitor.status == status)
    return query.order_by(models.Visitor.checked_in_at.desc()).all()


@router.get("/summary")
def get_visitor_summary(db: Session = Depends(get_db)):
    """Counts of visitors by status, for the dashboard summary cards."""
    return visitor_summary(db)


@router.get("/live", response_model=List[schemas.VisitorOut])
def live_visitors(db: Session = Depends(get_db)):
    """Currently checked-in visitors, most recently seen first — powers the live map."""
    return (
        db.query(models.Visitor)
        .filter(models.Visitor.status == "checked_in")
        .order_by(models.Visitor.last_seen_at.desc().nullslast(), models.Visitor.checked_in_at.desc())
        .all()
    )


@router.post("/check-in", response_model=schemas.VisitorOut, status_code=201)
async def check_in_visitor(
    visitor_in: schemas.VisitorCheckIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Assign a temporary badge (EPC) to a visitor and mark them checked in."""
    epc = visitor_in.epc.strip().upper()
    active = (
        db.query(models.Visitor)
        .filter(models.Visitor.epc.ilike(epc), models.Visitor.status == "checked_in")
        .first()
    )
    if active:
        raise HTTPException(409, f"Tag is already checked out to {active.display_name}")

    visitor = models.Visitor(
        visitor_id=generate_visitor_id(db),
        display_name=visitor_in.display_name.strip(),
        badge_label=visitor_in.badge_label,
        epc=epc,
        host=visitor_in.host,
        purpose=visitor_in.purpose,
        notes=visitor_in.notes,
        status="checked_in",
        checked_in_at=datetime.now(timezone.utc),
    )
    db.add(visitor)
    db.commit()
    db.refresh(visitor)

    background_tasks.add_task(ws_manager.broadcast, "visitor_checked_in", _visitor_payload(visitor))
    return visitor


@router.put("/{visitor_id}", response_model=schemas.VisitorOut)
def update_visitor(visitor_id: str, update: schemas.VisitorUpdate, db: Session = Depends(get_db)):
    """Patch visitor fields (host, purpose, notes, etc.)."""
    visitor = db.query(models.Visitor).filter(models.Visitor.visitor_id == visitor_id).first()
    if not visitor:
        raise HTTPException(404, f"Visitor {visitor_id} not found")
    for key, value in update.model_dump(exclude_none=True).items():
        setattr(visitor, key, value)
    db.commit()
    db.refresh(visitor)
    return visitor


@router.post("/{visitor_id}/check-out", response_model=schemas.VisitorOut)
async def check_out_visitor(
    visitor_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Check a visitor out and freeze their record (badge should be returned/deactivated)."""
    visitor = db.query(models.Visitor).filter(models.Visitor.visitor_id == visitor_id).first()
    if not visitor:
        raise HTTPException(404, f"Visitor {visitor_id} not found")
    visitor.status = "checked_out"
    visitor.checked_out_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(visitor)
    background_tasks.add_task(ws_manager.broadcast, "visitor_checked_out", _visitor_payload(visitor))
    return visitor


@router.get("/{visitor_id}/path", response_model=List[schemas.VisitorLocationEventOut])
def visitor_path(visitor_id: str, limit: int = 100, db: Session = Depends(get_db)):
    """Zone-by-zone movement history for one visitor, most recent first."""
    return (
        db.query(models.VisitorLocationEvent)
        .filter(models.VisitorLocationEvent.visitor_id == visitor_id)
        .order_by(models.VisitorLocationEvent.created_at.desc())
        .limit(limit)
        .all()
    )


def _visitor_payload(visitor: models.Visitor) -> dict:
    return {
        "visitor_id": visitor.visitor_id,
        "display_name": visitor.display_name,
        "badge_label": visitor.badge_label,
        "epc": visitor.epc,
        "status": visitor.status,
        "current_zone": visitor.current_zone,
        "current_zone_name": visitor.current_zone_name,
        "last_seen_at": visitor.last_seen_at.isoformat() if visitor.last_seen_at else None,
    }
