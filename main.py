"""
main.py — SEARLab RFID Lab Management System
Run: uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.config import settings
from backend.database import create_tables, SessionLocal
from backend.seed_data import seed_database
from backend.routes.assets import router as assets_router
from backend.routes.scans import router as scans_router
from backend.routes.visitors import router as visitors_router
from backend.routes.api import sessions_router, dashboard_router, drive_router, zone_router
from backend.services.websocket_manager import ws_manager
from backend.services.scheduler import start_scheduler, stop_scheduler
from backend.services.google_drive import drive_service

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────
    logger.info("=== SEARLab RFID Lab System starting ===")
    create_tables()

    db = SessionLocal()
    try:
        seed_database(db)
    finally:
        db.close()

    # Google Drive (optional — needs credentials.json). Never let a Drive
    # problem (expired token, no network, no browser for the OAuth prompt)
    # take down the whole app — the dashboard, scans, and assets API are
    # useful on their own even with Drive sync disabled.
    if Path(settings.GOOGLE_CREDENTIALS_FILE).exists():
        try:
            logger.info("Authenticating Google Drive...")
            drive_service.authenticate()
        except Exception as e:
            logger.error(f"Google Drive authentication failed — Drive sync disabled: {e}")
    else:
        logger.warning(
            f"{settings.GOOGLE_CREDENTIALS_FILE} not found — Drive sync disabled.\n"
            "Download from Google Cloud Console → APIs & Services → Credentials."
        )

    # MQTT service (set MQTT_ENABLED=true in .env)
    if settings.MQTT_ENABLED:
        try:
            from backend.services.mqtt_service import mqtt_service
            mqtt_service.configure(SessionLocal, ws_manager)
            mqtt_service.start()
            logger.info("MQTT service started.")
        except Exception as e:
            logger.error(f"MQTT service failed: {e}")

    # Impinj reader service (set RFID_READER_ENABLED=true in .env)
    if settings.RFID_READER_ENABLED:
        try:
            from rfid_reader_service import ImpinjReaderService
            svc = ImpinjReaderService()
            svc.start()
            app.state.rfid_service = svc
            logger.info("Impinj reader service started.")
        except Exception as e:
            logger.error(f"Impinj reader service failed: {e}")

    start_scheduler()
    logger.info(f"Server ready → http://{settings.APP_HOST}:{settings.APP_PORT}")
    logger.info(f"API docs  → http://{settings.APP_HOST}:{settings.APP_PORT}/docs")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    stop_scheduler()
    if settings.MQTT_ENABLED:
        try:
            from backend.services.mqtt_service import mqtt_service
            mqtt_service.stop()
        except Exception:
            pass
    if settings.RFID_READER_ENABLED and hasattr(app.state, "rfid_service"):
        app.state.rfid_service.stop()
    logger.info("Server shut down.")


app = FastAPI(
    title="SEARLab RFID Lab Management System",
    description="RFID Asset Tracking — Impinj Octane SDK + MQTT + Google Drive (Zebra + Impinj) + WebSocket",
    version="2025.3",
    lifespan=lifespan,
)

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.include_router(assets_router)
app.include_router(scans_router)
app.include_router(visitors_router)
app.include_router(sessions_router)
app.include_router(dashboard_router)
app.include_router(drive_router)
app.include_router(zone_router)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "lab": settings.LAB_NAME,
        "version": settings.SYSTEM_VERSION,
        "drive_connected": drive_service.connected,
        "mqtt_enabled": settings.MQTT_ENABLED,
        "rfid_reader_enabled": settings.RFID_READER_ENABLED,
        "ws_clients": len(ws_manager.active),
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Live event feed — new_scan, drive_sync, bulk_import, alert, heartbeat"""
    await ws_manager.connect(ws)
    try:
        await ws_manager.send_to(ws, "connected", {
            "message": "Connected to SEARLab RFID live feed",
            "clients": len(ws_manager.active),
        })
        while True:
            await ws.receive_text()   # keep alive; client messages ignored
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


# Serve frontend
frontend_dir = Path(__file__).parent / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

    @app.get("/", include_in_schema=False)
    def serve_frontend():
        return FileResponse(str(frontend_dir / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.APP_HOST, port=settings.APP_PORT,
                reload=(settings.APP_ENV == "development"))

# -------------------------------------------------------------------
# Live Lab Map API
# Used by frontend/live_lab_map_embed.html
# Returns latest scanned items with item name, EPC, zone, RSSI, reader,
# status, and last scanned time.
# -------------------------------------------------------------------
@app.get("/api/live-map-data")
def live_map_data(limit: int = 100):
    import sqlite3
    from datetime import datetime
    from pathlib import Path

    db_path = Path("rfid_lab.db").resolve()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    def table_exists(table_name):
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        return cur.fetchone() is not None

    def cols(table_name):
        cur.execute(f"PRAGMA table_info({table_name})")
        return {row[1] for row in cur.fetchall()}

    def first_col(col_set, names):
        for n in names:
            if n in col_set:
                return n
        return None

    def val(row, names, default=""):
        for n in names:
            if n in row.keys() and row[n] not in (None, ""):
                return row[n]
        return default

    scan_table = None
    for t in ["db2_incoming", "incoming_scans", "scans", "rfid_events"]:
        if table_exists(t):
            scan_table = t
            break

    master_table = None
    for t in ["db1_master", "assets"]:
        if table_exists(t):
            master_table = t
            break

    if not scan_table:
        conn.close()
        return {
            "items": [],
            "summary": {
                "mapped_items": 0,
                "latest_scans": 0,
                "matched": 0,
                "unknown": 0
            },
            "message": "No scan table found"
        }

    scan_cols = cols(scan_table)
    master_cols = cols(master_table) if master_table else set()

    epc_col = first_col(scan_cols, ["epc", "EPC", "tag", "tag_epc"])
    if not epc_col:
        conn.close()
        return {
            "items": [],
            "summary": {
                "mapped_items": 0,
                "latest_scans": 0,
                "matched": 0,
                "unknown": 0
            },
            "message": "No EPC column found"
        }

    order_col = first_col(scan_cols, ["id", "scan_id", "created_at", "timestamp"])
    order_sql = f'ORDER BY s."{order_col}" DESC' if order_col else ""

    if master_table and "epc" in master_cols:
        query = f"""
        SELECT
            s.*,
            m.asset_name AS master_asset_name,
            m.sku AS master_sku,
            m.gtin AS master_gtin,
            m.zone_code AS master_zone_code,
            m.zone_name AS master_zone_name,
            m.category AS master_category,
            m.status AS master_status
        FROM {scan_table} s
        LEFT JOIN {master_table} m
          ON UPPER(TRIM(s."{epc_col}")) = UPPER(TRIM(m.epc))
        {order_sql}
        LIMIT ?
        """
    else:
        query = f"""
        SELECT s.*
        FROM {scan_table} s
        {order_sql}
        LIMIT ?
        """

    cur.execute(query, (limit,))
    rows = cur.fetchall()

    latest_by_epc = {}

    for r in rows:
        epc = str(val(r, [epc_col, "epc", "EPC"], "")).strip()
        if not epc:
            continue

        epc_key = epc.upper()
        if epc_key in latest_by_epc:
            continue

        item_name = val(
            r,
            ["matched_asset_name", "asset_name", "master_asset_name"],
            "— Not in Master Table —"
        )

        zone = val(
            r,
            ["scanned_zone", "zone", "assigned_zone", "zone_code", "master_zone_code", "master_zone_name"],
            "Unknown"
        )

        status = val(r, ["verification_status", "status"], "UNKNOWN")

        latest_by_epc[epc_key] = {
            "item_name": item_name,
            "epc": epc,
            "sku": val(r, ["sku", "master_sku"], ""),
            "gtin": val(r, ["gtin", "master_gtin"], ""),
            "zone": zone,
            "rssi": val(r, ["rssi_dbm", "rssi", "RSSI"], ""),
            "reader": val(r, ["reader_id", "reader", "source"], ""),
            "status": status,
            "zone_confidence": val(r, ["zone_confidence", "confidence"], ""),
            "zone_confidence_label": val(r, ["zone_confidence_label", "confidence_label"], ""),
            "zone_reason": val(r, ["zone_reason", "reason"], ""),
            "antenna_id": val(r, ["antenna_id", "antenna", "ant"], ""),
            "read_count": val(r, ["read_count", "reads", "count"], ""),
            "last_time": val(r, ["scan_time", "time", "timestamp", "created_at", "last_scanned_iso"], "")
        }

    items = list(latest_by_epc.values())

    matched = sum(1 for x in items if str(x.get("status", "")).upper() == "MATCHED")
    unknown = sum(1 for x in items if str(x.get("status", "")).upper() != "MATCHED")
    mapped = sum(1 for x in items if x.get("item_name") and "Not in Master" not in x.get("item_name"))

    conn.close()

    return {
        "items": items,
        "summary": {
            "mapped_items": mapped,
            "latest_scans": len(items),
            "matched": matched,
            "unknown": unknown
        },
        "generated_at": datetime.now().isoformat()
    }
