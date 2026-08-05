from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text
from backend.database import Base

class Asset(Base):
    """DB1 Master — 87 verified reference records."""
    __tablename__ = "db1_master"
    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(String(20), unique=True, index=True, nullable=False)
    asset_name = Column(String(120), nullable=False)
    epc = Column(String(64), index=True, nullable=False)
    tag_id = Column(String(80), nullable=False)
    component_location = Column(String(120))
    zone_code = Column(String(4), index=True)
    zone_name = Column(String(60))
    sub_zone = Column(String(100))
    category = Column(String(40), index=True)
    manufacturer = Column(String(80))
    model = Column(String(100))
    unit = Column(String(30))
    qty = Column(Integer, default=1)
    status = Column(String(40), index=True)
    reader_id = Column(String(60))
    reader_type = Column(String(60))
    verification_method = Column(String(80))
    manual_check = Column(String(30))
    rssi_dbm = Column(Integer)
    signal_quality = Column(String(20))
    scan_date = Column(String(20))
    scan_time = Column(String(20))
    last_scanned_iso = Column(String(30))
    sku = Column(String(80), index=True, nullable=True)
    gtin = Column(String(40), index=True, nullable=True)
    tag_source = Column(String(50), nullable=True)
    expected_antenna_id = Column(Integer, nullable=True)
    item_type = Column(String(60), nullable=True)
    comments = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class IncomingScan(Base):
    """DB2 Incoming — every RFID read event from any source."""
    __tablename__ = "db2_incoming"
    id = Column(Integer, primary_key=True, index=True)
    scan_id = Column(String(30), unique=True, index=True)
    epc = Column(String(64), index=True, nullable=False)
    scanned_zone = Column(String(4))
    scan_date = Column(String(20))
    scan_time = Column(String(20))
    rssi_dbm = Column(Integer, nullable=True)
    antenna_id = Column(String(20), nullable=True)
    read_count = Column(Integer, default=1)
    assigned_zone = Column(String(60), nullable=True)
    zone_confidence = Column(Float, nullable=True)
    zone_reason = Column(Text, nullable=True)
    sku = Column(String(80), nullable=True)
    gtin = Column(String(40), nullable=True)
    tag_classification = Column(String(40), nullable=True)
    reader_id = Column(String(60))
    verification_status = Column(String(20), index=True, default="PENDING")
    matched_asset_name = Column(String(120), nullable=True)
    matched_record_id = Column(String(20), nullable=True)
    notes = Column(Text, nullable=True)
    source = Column(String(30), default="scanner")
    created_at = Column(DateTime, default=datetime.utcnow)

class ScanSession(Base):
    """Named scan session (full inventory, zone audit, etc.)."""
    __tablename__ = "scan_sessions"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(20), unique=True, index=True)
    scan_date = Column(String(20))
    scan_time = Column(String(20))
    reader_id = Column(String(60))
    reader_type = Column(String(60))
    zone_covered = Column(String(60))
    unique_tags = Column(Integer, default=0)
    total_reads = Column(Integer, default=0)
    duration_sec = Column(Integer, default=0)
    session_type = Column(String(60))
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class DriveSync(Base):
    """Tracks which Drive files have been imported."""
    __tablename__ = "drive_sync"
    id = Column(Integer, primary_key=True, index=True)
    drive_file_id = Column(String(100), unique=True, index=True)
    file_name = Column(String(200))
    file_size = Column(Integer, nullable=True)
    scans_imported = Column(Integer, default=0)
    status = Column(String(20), default="pending")
    error_message = Column(Text, nullable=True)
    imported_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Visitor(Base):
    """Temporary visitor assignment for live zone tracking."""
    __tablename__ = "visitors"
    id = Column(Integer, primary_key=True, index=True)
    visitor_id = Column(String(20), unique=True, index=True, nullable=False)
    display_name = Column(String(120), nullable=False)
    badge_label = Column(String(80), nullable=True)
    epc = Column(String(64), unique=True, index=True, nullable=False)
    host = Column(String(120), nullable=True)
    purpose = Column(String(160), nullable=True)
    status = Column(String(20), index=True, default="checked_in")
    current_zone = Column(String(4), index=True, nullable=True)
    current_zone_name = Column(String(60), nullable=True)
    previous_zone = Column(String(4), nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    checked_in_at = Column(DateTime, default=datetime.utcnow)
    checked_out_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class VisitorLocationEvent(Base):
    """Zone movement history for a checked-in visitor tag."""
    __tablename__ = "visitor_location_events"
    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(30), unique=True, index=True, nullable=False)
    visitor_id = Column(String(20), index=True, nullable=False)
    epc = Column(String(64), index=True, nullable=False)
    from_zone = Column(String(4), nullable=True)
    to_zone = Column(String(4), index=True, nullable=True)
    to_zone_name = Column(String(60), nullable=True)
    antenna_id = Column(String(20), nullable=True)
    rssi_dbm = Column(Integer, nullable=True)
    read_count = Column(Integer, default=1)
    zone_confidence = Column(Float, nullable=True)
    scan_id = Column(String(30), index=True, nullable=True)
    reader_id = Column(String(60), nullable=True)
    source = Column(String(30), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
