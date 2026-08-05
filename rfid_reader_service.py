"""
rfid_reader_service.py
Phase 1: Impinj Octane SDK — connects to Impinj R700, subscribes to TagReport events
Phase 2: MQTT Publishing — publishes to Eclipse Mosquitto
         Topic: lab/rfid/{reader_id}/{antenna_id}/read

Run standalone:  python rfid_reader_service.py
Or set RFID_READER_ENABLED=true in .env to auto-start with main.py
"""

import json
import logging
import os
import threading
import time
import re
from datetime import datetime, timezone
from typing import Optional

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Config (reads from .env or environment) ───────────────────────────────────
IMPINJ_READER_HOST  = os.getenv("IMPINJ_READER_HOST", "192.168.1.100")
IMPINJ_READER_PORT  = int(os.getenv("IMPINJ_READER_PORT", "5084"))
IMPINJ_READER_ID    = os.getenv("IMPINJ_READER_ID", "impinj-r700-lab01")
RSSI_THRESHOLD      = int(os.getenv("RSSI_THRESHOLD", "-75"))      # dBm filter
MQTT_BROKER_HOST    = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT    = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_TOPIC_PREFIX   = os.getenv("MQTT_TOPIC_PREFIX", "lab/rfid")
BACKEND_SCAN_URL    = os.getenv("BACKEND_SCAN_URL", "http://localhost:8000/api/scans")
SCANNER_API_KEY     = os.getenv("RFID_SCANNER_API_KEY", "rfid-scanner-secret-key")

# Antenna port → Zone mapping. Edit to match your physical setup.
ANTENNA_ZONE_MAP = {
    1: "A",   # Antenna 1 → Zone A (Battery Storage)
    2: "B",   # Antenna 2 → Zone B (Workbench)
    3: "C",   # Antenna 3 → Zone C (Storage East)
    4: "D",   # Antenna 4 → Zone D (Entrance/Testing)
}

# ── Optional imports ──────────────────────────────────────────────────────────
try:
    import paho.mqtt.client as mqtt
    MQTT_OK = True
except ImportError:
    MQTT_OK = False
    logger.warning("paho-mqtt not installed. pip install paho-mqtt")

try:
    from octane_sdk import ImpinjReader
    OCTANE_OK = True
except ImportError:
    OCTANE_OK = False
    logger.warning("Octane SDK not installed. pip install octane-sdk-python")

try:
    import httpx
    HTTPX_OK = True
except ImportError:
    HTTPX_OK = False
    logger.warning("httpx not installed. pip install httpx")


# ── MQTT Publisher ────────────────────────────────────────────────────────────
class MQTTPublisher:
    def __init__(self):
        self._client = None
        self._connected = False

    def connect(self) -> bool:
        if not MQTT_OK:
            return False
        try:
            self._client = mqtt.Client(client_id="rfid-reader-service", clean_session=True)
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=60)
            self._client.loop_start()
            time.sleep(0.5)
            return self._connected
        except Exception as e:
            logger.error(f"MQTT connect failed: {e}")
            return False

    def _on_connect(self, client, userdata, flags, rc):
        self._connected = (rc == 0)
        if rc == 0:
            logger.info(f"MQTT connected to {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
        else:
            logger.error(f"MQTT refused, rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        logger.warning(f"MQTT disconnected rc={rc}")

    def publish(self, reader_id: str, antenna: int, payload: dict):
        if not self._connected or not self._client:
            return
        topic = f"{MQTT_TOPIC_PREFIX}/{reader_id}/{antenna}/read"
        try:
            self._client.publish(topic, json.dumps(payload), qos=1)
            logger.debug(f"MQTT → {topic}")
        except Exception as e:
            logger.error(f"MQTT publish: {e}")

    def stop(self):
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()


# ── Backend HTTP Pusher ───────────────────────────────────────────────────────
class BackendPusher:
    def __init__(self):
        self._headers = {
            "Content-Type": "application/json",
            "X-Scanner-Key": SCANNER_API_KEY,
        }

    def push(self, epc: str, zone: Optional[str], rssi: int, reader_id: str):
        if not HTTPX_OK:
            return
        now = datetime.now(timezone.utc)
        payload = {
            "epc": epc,
            "scanned_zone": zone,
            "rssi_dbm": rssi,
            "reader_id": reader_id,
            "source": "scanner",
            "scan_date": now.strftime("%Y-%m-%d"),
            "scan_time": now.strftime("%H:%M:%S"),
        }
        try:
            import httpx
            with httpx.Client(timeout=3.0) as client:
                r = client.post(BACKEND_SCAN_URL, json=payload, headers=self._headers)
            if r.status_code not in (200, 201):
                logger.warning(f"Backend push {r.status_code}: {r.text[:60]}")
        except Exception as e:
            logger.warning(f"Backend push failed: {e}")


# ── Asset Registry cache ──────────────────────────────────────────────────────
class AssetRegistry:
    def __init__(self):
        self._cache: dict[str, Optional[str]] = {}

    def get_asset_id(self, epc: str) -> str:
        if epc in self._cache:
            return self._cache[epc] or epc
        if not HTTPX_OK:
            return epc
        try:
            import httpx
            with httpx.Client(timeout=2.0) as c:
                r = c.get(f"http://localhost:8000/api/assets/epc/{epc}")
            if r.status_code == 200:
                d = r.json()
                tag_id = d.get("asset", {}).get("tag_id") or epc
                self._cache[epc] = tag_id
                return tag_id
        except Exception:
            pass
        self._cache[epc] = None
        return epc


# ── Impinj Reader Service ─────────────────────────────────────────────────────
class ImpinjReaderService:
    """
    Phase 1 + 2 + 3:
    - Connects to Impinj R700 via Octane SDK (LLRP protocol)
    - Filters reads by RSSI threshold (removes reflections/ghost reads)
    - Maps antenna port → lab zone
    - Publishes to MQTT broker (Phase 2)
    - Pushes to backend API → triggers WebSocket live update (Phase 3)
    """

    def __init__(self):
        self._mqtt = MQTTPublisher()
        self._pusher = BackendPusher()
        self._registry = AssetRegistry()
        self._running = False
        self._readers = []

    def start(self):
        logger.info("Starting Impinj reader service...")
        self._mqtt.connect()
        self._running = True

        if OCTANE_OK:
            t = threading.Thread(target=self._connect_reader, daemon=True, name="impinj-reader")
            t.start()
        else:
            logger.warning(
                "Octane SDK not available. Install: pip install octane-sdk-python\n"
                "Scanner push endpoint is active at POST /api/scans for direct HTTP push."
            )

    def _connect_reader(self):
        while self._running:
            try:
                logger.info(f"Connecting to {IMPINJ_READER_ID} at {IMPINJ_READER_HOST}...")
                reader = ImpinjReader()
                reader.connect(IMPINJ_READER_HOST)

                settings = reader.query_default_settings()
                settings.report.include_antenna_port_number = True
                settings.report.include_peak_rssi = True
                settings.report.include_timestamp = True
                reader.apply_settings(settings)

                reader.tag_reports += lambda sender, report: self._on_tags(report)
                reader.start()
                self._readers.append(reader)
                logger.info(f"Reader {IMPINJ_READER_ID} active.")

                while self._running and reader.is_connected:
                    time.sleep(1)

            except Exception as e:
                logger.error(f"Reader error: {e}. Retrying in 10s...")
                time.sleep(10)

    def _on_tags(self, report):
        for tag in report.tags:
            epc = tag.epc.epc_string.upper()
            rssi = int(tag.peak_rssi_in_dbm) if tag.peak_rssi_in_dbm else -99
            ant = int(tag.antenna_port_number) if tag.antenna_port_number else 1
            ts = (tag.last_seen_time.utc_timestamp.isoformat()
                  if tag.last_seen_time else datetime.now(timezone.utc).isoformat())

            # RSSI filter — reject reflections/ghost reads
            if rssi < RSSI_THRESHOLD:
                logger.debug(f"Filtered: {epc} RSSI={rssi}")
                continue

            zone = ANTENNA_ZONE_MAP.get(ant, "A")
            asset_id = self._registry.get_asset_id(epc)

            # Phase 2: MQTT publish
            self._mqtt.publish(IMPINJ_READER_ID, ant, {
                "epc": epc, "rssi": rssi, "timestamp": ts,
                "asset_id": asset_id, "antenna": ant, "reader": IMPINJ_READER_ID,
            })

            # Phase 1 + 3: backend push → triggers live WebSocket update
            self._pusher.push(epc, zone, rssi, IMPINJ_READER_ID)
            logger.info(f"Read: {epc} | asset={asset_id} | RSSI={rssi} dBm | ant={ant} | zone={zone}")

    def stop(self):
        self._running = False
        for r in self._readers:
            try:
                r.stop(); r.disconnect()
            except Exception:
                pass
        self._mqtt.stop()
        logger.info("Reader service stopped.")


# ── MQTT Bridge (optional — for multi-machine setup) ─────────────────────────
class MQTTBridge:
    """Subscribes to MQTT and forwards reads to backend. Use if reader runs on separate machine."""

    def __init__(self):
        self._pusher = BackendPusher()

    def start(self):
        if not MQTT_OK:
            logger.error("paho-mqtt not installed.")
            return
        import paho.mqtt.client as mqtt
        client = mqtt.Client(client_id="rfid-bridge")
        client.on_connect = lambda c, u, f, rc: c.subscribe(f"{MQTT_TOPIC_PREFIX}/#")
        client.on_message = self._on_message
        client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT)
        logger.info("MQTT bridge started.")
        client.loop_forever()

    def _on_message(self, client, userdata, msg):
        try:
            p = json.loads(msg.payload.decode())
            parts = msg.topic.split("/")
            reader_id = parts[2] if len(parts) > 2 else "unknown"
            self._pusher.push(p.get("epc", ""), None, p.get("rssi", -60), reader_id)
        except Exception as e:
            logger.error(f"Bridge error: {e}")


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SEARLab RFID Reader Service")
    parser.add_argument("--mode", choices=["reader", "bridge"], default="reader",
                        help="reader = Octane SDK direct | bridge = MQTT subscriber only")
    args = parser.parse_args()

    if args.mode == "bridge":
        MQTTBridge().start()
    else:
        svc = ImpinjReaderService()
        try:
            svc.start()
            logger.info("Reader running. Press Ctrl+C to stop.")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            svc.stop()
