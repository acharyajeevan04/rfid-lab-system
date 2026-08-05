from datetime import datetime
from typing import Optional, List, Union
from pydantic import BaseModel, ConfigDict

class AssetBase(BaseModel):
    record_id: str
    asset_name: str
    epc: str
    tag_id: str
    component_location: Optional[str] = None
    zone_code: Optional[str] = None
    zone_name: Optional[str] = None
    sub_zone: Optional[str] = None
    category: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    unit: Optional[str] = None
    qty: int = 1
    status: Optional[str] = None
    reader_id: Optional[str] = None
    reader_type: Optional[str] = None
    verification_method: Optional[str] = None
    manual_check: Optional[str] = None
    rssi_dbm: Optional[int] = None
    signal_quality: Optional[str] = None
    scan_date: Optional[str] = None
    scan_time: Optional[str] = None
    last_scanned_iso: Optional[str] = None
    sku: Optional[str] = None
    gtin: Optional[str] = None
    tag_source: Optional[str] = None
    expected_antenna_id: Optional[int] = None
    item_type: Optional[str] = None
    comments: Optional[str] = None

class AssetCreate(AssetBase):
    pass

class AssetUpdate(BaseModel):
    asset_name: Optional[str] = None
    status: Optional[str] = None
    component_location: Optional[str] = None
    zone_code: Optional[str] = None
    zone_name: Optional[str] = None
    sub_zone: Optional[str] = None
    rssi_dbm: Optional[int] = None
    signal_quality: Optional[str] = None
    scan_date: Optional[str] = None
    scan_time: Optional[str] = None
    last_scanned_iso: Optional[str] = None
    sku: Optional[str] = None
    gtin: Optional[str] = None
    tag_source: Optional[str] = None
    expected_antenna_id: Optional[int] = None
    item_type: Optional[str] = None
    comments: Optional[str] = None

class AssetOut(AssetBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime

class ScanCreate(BaseModel):
    epc: str
    scanned_zone: Optional[str] = None
    scan_date: Optional[str] = None
    scan_time: Optional[str] = None
    rssi_dbm: Optional[int] = None
    antenna_id: Optional[Union[int, str]] = None
    read_count: Optional[int] = 1
    reader_id: Optional[str] = None
    source: str = "scanner"

class ScanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    scan_id: str
    epc: str
    scanned_zone: Optional[str]
    scan_date: Optional[str]
    scan_time: Optional[str]
    rssi_dbm: Optional[int]
    antenna_id: Optional[str] = None
    read_count: Optional[int] = 1
    assigned_zone: Optional[str] = None
    zone_confidence: Optional[float] = None
    zone_reason: Optional[str] = None
    sku: Optional[str] = None
    gtin: Optional[str] = None
    tag_classification: Optional[str] = None
    reader_id: Optional[str]
    verification_status: str
    matched_asset_name: Optional[str]
    matched_record_id: Optional[str]
    notes: Optional[str]
    source: str
    created_at: datetime

class VerifyResult(BaseModel):
    epc: str
    found: bool
    verification_status: str
    asset: Optional[AssetOut] = None
    last_scan: Optional[ScanOut] = None
    message: str

class SessionCreate(BaseModel):
    session_id: Optional[str] = None
    scan_date: Optional[str] = None
    scan_time: Optional[str] = None
    reader_id: Optional[str] = None
    reader_type: Optional[str] = None
    zone_covered: Optional[str] = None
    unique_tags: int = 0
    total_reads: int = 0
    duration_sec: int = 0
    session_type: Optional[str] = None
    notes: Optional[str] = None

class SessionOut(SessionCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime

class ZoneStat(BaseModel):
    zone_code: str
    zone_name: str
    count: int
    pct: float

class DashboardStats(BaseModel):
    total_assets: int
    active_assets: int
    total_scans: int
    total_sessions: int
    total_reads: int
    alerts: int
    zone_breakdown: List[ZoneStat]
    category_breakdown: dict
    status_breakdown: dict
    scan_status_breakdown: dict
    last_sync: Optional[str]
    drive_connected: bool

class DriveSyncResult(BaseModel):
    success: bool
    files_checked: int
    new_files: int
    scans_imported: int
    zebra_files: int
    impinj_files: int
    errors: List[str]
    message: str

class DriveStatus(BaseModel):
    connected: bool
    account: str
    zebra_folder_id: str
    impinj_folder_id: str
    last_sync: Optional[str]
    total_files_synced: int
    total_scans_imported: int


class ZoneAssignmentOut(BaseModel):
    epc: str
    assigned_zone: Optional[str] = None
    scanned_zone: Optional[str] = None
    antenna_id: Optional[str] = None
    read_count: int = 1
    rssi_dbm: Optional[int] = None
    zone_confidence: Optional[float] = None
    confidence_label: Optional[str] = None
    asset_name: Optional[str] = None
    status: Optional[str] = None
    last_seen: Optional[str] = None

class UnmappedTagOut(BaseModel):
    epc: str
    read_events: int
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    strongest_rssi: Optional[int] = None
    likely_demo_e280: bool = False
    recommendation: str

class VisitorCheckIn(BaseModel):
    display_name: str
    epc: str
    badge_label: Optional[str] = None
    host: Optional[str] = None
    purpose: Optional[str] = None
    notes: Optional[str] = None

class VisitorUpdate(BaseModel):
    display_name: Optional[str] = None
    badge_label: Optional[str] = None
    host: Optional[str] = None
    purpose: Optional[str] = None
    notes: Optional[str] = None

class VisitorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    visitor_id: str
    display_name: str
    badge_label: Optional[str] = None
    epc: str
    host: Optional[str] = None
    purpose: Optional[str] = None
    status: str
    current_zone: Optional[str] = None
    current_zone_name: Optional[str] = None
    previous_zone: Optional[str] = None
    last_seen_at: Optional[datetime] = None
    checked_in_at: datetime
    checked_out_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

class VisitorLocationEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_id: str
    visitor_id: str
    epc: str
    from_zone: Optional[str] = None
    to_zone: Optional[str] = None
    to_zone_name: Optional[str] = None
    antenna_id: Optional[str] = None
    rssi_dbm: Optional[int] = None
    read_count: int = 1
    zone_confidence: Optional[float] = None
    scan_id: Optional[str] = None
    reader_id: Optional[str] = None
    source: Optional[str] = None
    created_at: datetime
