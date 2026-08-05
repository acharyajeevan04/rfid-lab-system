"""
mqtt_service.py — MQTT subscriber for the Impinj reader -> MQTT -> dashboard pipeline.

rfid_reader_service.py (run with --mode reader) publishes each Impinj tag read to:
    {MQTT_TOPIC_PREFIX}/{reader_id}/{antenna_id}/read
with a JSON payload of the form:
    {"epc": "...", "rssi": -62, "timestamp": "...", "asset_id": "...",
     "antenna": 1, "reader": "impinj-r700-lab01"}

This module subscribes to that topic tree, runs each read through the same
zone-assignment and EPC-verification logic used by a manual scanner push
(backend/routes/scans.py), writes a DB2 IncomingScan row, and broadcasts
new_scan / alert events over the same WebSocket contract — so MQTT reads
show up on the live dashboard identically to any other scan source.

Enable with MQTT_ENABLED=true in .env. If paho-mqtt isn't installed, or the
broker can't be reached, this fails soft: it logs a warning and the rest of
the app (Drive sync, manual scans, dashboard) keeps working normally.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from backend.config import settings
from backend import models
from backend.services.verification import verify_epc, generate_scan_id
from backend.services.zone_engine import assign_zone_from_record, confidence_label
from backend.services.visitor_tracking import record_visitor_scan

logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt
    MQTT_OK = True
except ImportError:
    MQTT_OK = False
    logger.warning("paho-mqtt not installed. Run: pip install paho-mqtt")


class MQTTService:
    """Subscribes to the reader's MQTT topic and turns tag reads into DB2 scans."""

    def __init__(self):
        self._client = None
        self._session_factory = None
        self._ws_manager = None
        self._loop = None
        self._running = False

    def configure(self, session_factory, ws_manager_instance):
        """Wire in the DB session factory and WebSocket manager from main.py's
        startup, and capture the running asyncio loop so the MQTT client's
        background thread can safely schedule broadcasts back onto it."""
        self._session_factory = session_factory
        self._ws_manager = ws_manager_instance
        self._loop = asyncio.get_event_loop()

    def start(self):
        """Connect to the broker and start the network loop in a background thread."""
        if not MQTT_OK:
            logger.warning("MQTT service not started — paho-mqtt not installed.")
            return
        if not settings.MQTT_ENABLED:
            return

        self._client = mqtt.Client(client_id="rfid-backend-subscriber", clean_session=True)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        try:
            self._client.connect(settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT, keepalive=60)
            self._client.loop_start()
            self._running = True
            logger.info(
                f"MQTT service connecting to {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}"
            )
        except Exception as e:
            logger.error(f"MQTT connect failed: {e}")

    def stop(self):
        """Disconnect cleanly on app shutdown."""
        if self._client and self._running:
            self._client.loop_stop()
            self._client.disconnect()
            self._running = False
            logger.info("MQTT service stopped.")

    # ── paho-mqtt callbacks (run on the client's own thread) ────────────────

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            topic = f"{settings.MQTT_TOPIC_PREFIX}/#"
            client.subscribe(topic)
            logger.info(f"MQTT connected — subscribed to {topic}")
        else:
            logger.error(f"MQTT connect refused, rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        logger.warning(f"MQTT disconnected, rc={rc}")

    def _on_message(self, client, userdata, msg):
        """Keep this fast: parse JSON, hand off to the DB handler, never raise."""
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.warning(f"MQTT message on {msg.topic} was not valid JSON: {e}")
            return

        try:
            self._handle_read(msg.topic, payload)
        except Exception:
            logger.exception(f"Failed to process MQTT message on {msg.topic}")

    # ── DB + broadcast handling ──────────────────────────────────────────

    def _handle_read(self, topic: str, payload: dict):
        """Verify one tag read against DB1, write a DB2 row, and broadcast it,
        using the same logic as a manual scanner push."""
        epc = str(payload.get("epc", "")).strip().upper()
        if not epc:
            logger.warning(f"MQTT message on {topic} missing epc: {payload}")
            return

        # Topic shape: {prefix}/{reader_id}/{antenna_id}/read — used as a fallback
        # if the payload itself doesn't carry reader/antenna fields.
        parts = topic.split("/")
        topic_reader = parts[-3] if len(parts) >= 3 else None
        topic_antenna = parts[-2] if len(parts) >= 2 else None

        record = {
            "rssi_dbm": payload.get("rssi"),
            "antenna": payload.get("antenna", topic_antenna),
            "reader_id": payload.get("reader", topic_reader),
            "count": 1,
        }

        if self._session_factory is None:
            logger.warning("MQTT service received a read before configure() was called; dropping it.")
            return

        db = self._session_factory()
        try:
            now = datetime.now(timezone.utc)
            zone = assign_zone_from_record(record)
            vr = verify_epc(epc, zone.get("scanned_zone"), db)
            asset = db.query(models.Asset).filter(models.Asset.epc.ilike(epc)).first()

            scan = models.IncomingScan(
                scan_id=generate_scan_id(db),
                epc=epc,
                scanned_zone=zone.get("scanned_zone"),
                assigned_zone=zone.get("assigned_zone"),
                zone_confidence=zone.get("zone_confidence"),
                zone_reason=zone.get("zone_reason"),
                antenna_id=zone.get("antenna_id"),
                read_count=zone.get("read_count") or 1,
                rssi_dbm=payload.get("rssi"),
                reader_id=record["reader_id"] or settings.IMPINJ_READER_ID,
                source="mqtt",
                scan_date=now.strftime("%Y-%m-%d"),
                scan_time=now.strftime("%H:%M:%S"),
                sku=getattr(asset, "sku", None) if asset else None,
                gtin=getattr(asset, "gtin", None) if asset else None,
                tag_classification="registered" if asset else "unknown",
                **vr,
            )
            db.add(scan)
            db.flush()
            visitor, movement = record_visitor_scan(db, scan)
            db.commit()
            db.refresh(scan)

            self._broadcast("new_scan", {
                "scan_id": scan.scan_id, "epc": scan.epc, "zone": scan.scanned_zone,
                "assigned_zone": scan.assigned_zone, "zone_confidence": scan.zone_confidence,
                "confidence_label": confidence_label(scan.zone_confidence),
                "antenna_id": scan.antenna_id, "read_count": scan.read_count,
                "asset": scan.matched_asset_name, "status": scan.verification_status,
                "time": scan.scan_time, "date": scan.scan_date,
                "notes": scan.notes, "reader": scan.reader_id, "source": scan.source,
            })

            if scan.verification_status in ("MISMATCH", "UNKNOWN"):
                self._broadcast("alert", {
                    "scan_id": scan.scan_id, "epc": scan.epc,
                    "status": scan.verification_status, "message": scan.notes,
                    "zone": scan.scanned_zone,
                })

            if visitor and movement:
                self._broadcast("visitor_moved", {
                    "visitor_id": visitor.visitor_id,
                    "display_name": visitor.display_name,
                    "epc": visitor.epc,
                    "from_zone": movement.from_zone,
                    "to_zone": movement.to_zone,
                    "to_zone_name": movement.to_zone_name,
                    "antenna_id": movement.antenna_id,
                })
        finally:
            db.close()

    def _broadcast(self, event_type: str, payload: dict):
        """Schedule an async WebSocket broadcast from this synchronous MQTT thread."""
        if self._loop is None or self._ws_manager is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._ws_manager.broadcast(event_type, payload), self._loop
        )


mqtt_service = MQTTService()
