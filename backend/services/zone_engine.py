import math
import json
import sqlite3
from pathlib import Path
from typing import Any, Optional


DB_PATH = Path("rfid_lab.db")


# Fallback mapping if DB table is not loaded yet
FALLBACK_ANTENNA_ZONE_MAP = {
    "ANT-01": {"zone_code": "A", "zone_name": "Door", "mac": "16:12:D5", "reader": "Radar 1", "port": 1},
    "ANT-02": {"zone_code": "B", "zone_name": "Conveyor", "mac": "13:A6:DF", "reader": "Radar 1", "port": 2},
    "ANT-03": {"zone_code": "C", "zone_name": "Front North", "mac": "13:A6:48", "reader": "Radar 2", "port": 1},
    "ANT-04": {"zone_code": "D", "zone_name": "Back South / Storage", "mac": "13:A6:48", "reader": "Radar 2", "port": 2},
}

ZONE_NAME_BY_CODE = {
    "A": "Door",
    "B": "Conveyor",
    "C": "Front North",
    "D": "Back South / Storage",
}


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 1) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def normalize_text(v: Any) -> str:
    return str(v or "").strip()


def normalize_zone_code(zone: Optional[str]) -> Optional[str]:
    z = normalize_text(zone)
    if not z:
        return None

    u = z.upper()

    if u in {"A", "B", "C", "D"}:
        return u
    if "ZONE A" in u or "DOOR" in u:
        return "A"
    if "ZONE B" in u or "CONVEYOR" in u:
        return "B"
    if "ZONE C" in u or "FRONT" in u:
        return "C"
    if "ZONE D" in u or "BACK" in u or "STORAGE" in u:
        return "D"

    return None


def zone_name(zone_code: Optional[str]) -> Optional[str]:
    if not zone_code:
        return None
    return ZONE_NAME_BY_CODE.get(str(zone_code).upper(), str(zone_code))


def normalize_antenna_id(value: Any) -> Optional[str]:
    text = normalize_text(value).upper()

    if not text:
        return None

    if text.startswith("ANT-"):
        digits = "".join(ch for ch in text if ch.isdigit())
        if digits:
            return f"ANT-{int(digits):02d}"

    digits = "".join(ch for ch in text if ch.isdigit())
    if digits:
        return f"ANT-{int(digits):02d}"

    return None


def load_antenna_map_from_db() -> dict[str, dict[str, Any]]:
    if not DB_PATH.exists():
        return FALLBACK_ANTENNA_ZONE_MAP

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rfid_antennas'")
        if not cur.fetchone():
            conn.close()
            return FALLBACK_ANTENNA_ZONE_MAP

        cur.execute("""
        SELECT
            antenna_id,
            mac_address,
            reader,
            reader_id,
            zone_id,
            port,
            position_x_m,
            position_y_m,
            tx_power_dbm,
            polarization,
            ip_address
        FROM rfid_antennas
        """)

        data = {}

        for r in cur.fetchall():
            ant_id = normalize_antenna_id(r["antenna_id"])
            if not ant_id:
                continue

            z = normalize_zone_code(r["zone_id"])
            data[ant_id] = {
                "zone_code": z,
                "zone_name": zone_name(z),
                "mac": normalize_text(r["mac_address"]),
                "reader": normalize_text(r["reader"]),
                "reader_id": normalize_text(r["reader_id"]),
                "port": safe_int(r["port"], 0),
                "position_x_m": safe_float(r["position_x_m"], None),
                "position_y_m": safe_float(r["position_y_m"], None),
                "tx_power_dbm": safe_float(r["tx_power_dbm"], None),
                "polarization": normalize_text(r["polarization"]),
                "ip_address": normalize_text(r["ip_address"]),
            }

        conn.close()

        return data or FALLBACK_ANTENNA_ZONE_MAP

    except Exception:
        return FALLBACK_ANTENNA_ZONE_MAP


def find_antenna(record: dict[str, Any]) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    ant_map = load_antenna_map_from_db()

    raw_ant = (
        record.get("antenna_id")
        or record.get("antenna")
        or record.get("ant")
        or record.get("Antenna")
    )

    ant_id = normalize_antenna_id(raw_ant)

    if ant_id and ant_id in ant_map:
        return ant_id, ant_map[ant_id]

    raw_mac = normalize_text(
        record.get("mac_address")
        or record.get("mac")
        or record.get("MAC")
    ).upper()

    raw_reader = normalize_text(
        record.get("reader")
        or record.get("reader_id")
        or record.get("source")
    ).upper()

    raw_port = safe_int(
        record.get("port")
        or record.get("antenna_port")
        or record.get("antenna")
        or record.get("Antenna"),
        0
    )

    # Best match: MAC + port
    if raw_mac and raw_port:
        for aid, meta in ant_map.items():
            if normalize_text(meta.get("mac")).upper() == raw_mac and safe_int(meta.get("port"), 0) == raw_port:
                return aid, meta

    # Next match: reader + port
    if raw_reader and raw_port:
        for aid, meta in ant_map.items():
            reader_values = {
                normalize_text(meta.get("reader")).upper(),
                normalize_text(meta.get("reader_id")).upper(),
            }
            if raw_reader in reader_values and safe_int(meta.get("port"), 0) == raw_port:
                return aid, meta

    # Port alone is ambiguous because Reader 1 and Reader 2 can both have port 1/2.
    # Only use port alone if it uniquely identifies one antenna.
    if raw_port:
        matches = [
            (aid, meta)
            for aid, meta in ant_map.items()
            if safe_int(meta.get("port"), 0) == raw_port
        ]
        if len(matches) == 1:
            return matches[0]

    return None, None


def antenna_to_zone(antenna_id: Any):
    ant_map = load_antenna_map_from_db()
    ant_id = normalize_antenna_id(antenna_id)

    if ant_id and ant_id in ant_map:
        meta = ant_map[ant_id]
        return meta.get("zone_code"), meta.get("zone_name")

    return None, None


def rssi_score(rssi: Any) -> float:
    val = safe_float(rssi, None)
    if val is None:
        return 0.40

    # Typical RFID range: -90 weak to -35 strong
    val = max(-90.0, min(-35.0, val))
    return round((val + 90.0) / 55.0, 4)


def read_rate_score(read_count: Any, total_reads: int = 1) -> float:
    reads = max(1, safe_int(read_count, 1))
    total = max(1, safe_int(total_reads, reads))
    return round(min(1.0, reads / total), 4)


def confidence_label(score: Optional[float]) -> str:
    if score is None:
        return "Unknown"
    if score >= 0.80:
        return "High"
    if score >= 0.60:
        return "Medium"
    return "Low"


def is_demo_e280(epc: str | None) -> bool:
    return bool(epc and str(epc).upper().startswith("E280"))


def extract_read_record(record: dict[str, Any]) -> dict[str, Any]:
    antenna_id, antenna_meta = find_antenna(record)

    rssi = (
        record.get("rssi_dbm")
        or record.get("rssi")
        or record.get("RSSI")
    )

    read_count = (
        record.get("read_count")
        or record.get("reads")
        or record.get("count")
        or record.get("COUNT")
        or 1
    )

    explicit_zone = (
        record.get("scanned_zone")
        or record.get("assigned_zone")
        or record.get("zone")
        or record.get("zone_code")
    )

    zone_code = antenna_meta.get("zone_code") if antenna_meta else None
    zname = antenna_meta.get("zone_name") if antenna_meta else None

    explicit_zone_code = normalize_zone_code(explicit_zone)

    # If the scanner already sends a zone, respect it.
    # Otherwise use antenna mapping.
    if explicit_zone_code:
        zone_code = explicit_zone_code
        zname = zone_name(explicit_zone_code)

    return {
        "antenna_id": antenna_id,
        "antenna_meta": antenna_meta or {},
        "rssi": safe_float(rssi, None),
        "read_count": max(1, safe_int(read_count, 1)),
        "zone_code": zone_code,
        "zone_name": zname,
        "raw": record,
    }


def assign_zone_from_reads(records: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Zone tracking algorithm:
    zone_score = 0.65 * RSSI score + 0.35 * read-rate score

    If same EPC is read by multiple antennas, highest zone_score wins.
    """

    cleaned = [extract_read_record(r) for r in records if r]
    cleaned = [r for r in cleaned if r.get("zone_code")]

    if not cleaned:
        return {
            "scanned_zone": None,
            "assigned_zone": None,
            "zone_confidence": 0.0,
            "zone_confidence_label": "Unknown",
            "zone_reason": "No valid antenna/zone mapping found. Need antenna ID, MAC+port, or reader+port.",
            "zone_candidates": [],
            "zone_candidates_json": "[]",
            "antenna_id": None,
            "read_count": 1,
        }

    total_reads = sum(max(1, r["read_count"]) for r in cleaned)
    zone_groups: dict[str, list[dict[str, Any]]] = {}

    for r in cleaned:
        zone_groups.setdefault(r["zone_code"], []).append(r)

    candidates = []

    for zone_code, reads in zone_groups.items():
        zone_read_count = sum(max(1, r["read_count"]) for r in reads)

        weighted_rssi_score = sum(
            rssi_score(r["rssi"]) * max(1, r["read_count"])
            for r in reads
        ) / max(1, zone_read_count)

        read_component = read_rate_score(zone_read_count, total_reads)
        final_score = round((0.65 * weighted_rssi_score) + (0.35 * read_component), 4)

        best_read = sorted(
            reads,
            key=lambda x: (rssi_score(x["rssi"]), max(1, x["read_count"])),
            reverse=True
        )[0]

        candidates.append({
            "zone_code": zone_code,
            "zone_name": zone_name(zone_code),
            "antenna_id": best_read.get("antenna_id"),
            "zone_read_count": zone_read_count,
            "avg_rssi_score": round(weighted_rssi_score, 4),
            "read_rate_score": round(read_component, 4),
            "zone_score": final_score,
            "best_rssi": best_read.get("rssi"),
            "mac": best_read.get("antenna_meta", {}).get("mac", ""),
            "reader": best_read.get("antenna_meta", {}).get("reader", ""),
            "port": best_read.get("antenna_meta", {}).get("port", ""),
        })

    candidates = sorted(candidates, key=lambda x: x["zone_score"], reverse=True)

    winner = candidates[0]
    second_score = candidates[1]["zone_score"] if len(candidates) > 1 else 0.0
    margin = round(winner["zone_score"] - second_score, 4)

    final_confidence = round(
        (0.75 * winner["zone_score"]) + (0.25 * min(1.0, margin * 3)),
        4
    )

    reason = (
        f"Assigned to Zone {winner['zone_code']} / {winner['zone_name']}; "
        f"score={winner['zone_score']}; "
        f"confidence={final_confidence} ({confidence_label(final_confidence)}); "
        f"read_count={winner['zone_read_count']}/{total_reads}; "
        f"RSSI score={winner['avg_rssi_score']}; "
        f"read-rate score={winner['read_rate_score']}; "
        f"antenna={winner.get('antenna_id')}; "
        f"MAC={winner.get('mac')}; "
        f"reader={winner.get('reader')}; "
        f"port={winner.get('port')}"
    )

    if len(candidates) > 1:
        runner = candidates[1]
        reason += f"; runner-up Zone {runner['zone_code']} score={runner['zone_score']}"

    return {
        "scanned_zone": winner["zone_code"],
        "assigned_zone": winner["zone_name"],
        "zone_confidence": final_confidence,
        "zone_confidence_label": confidence_label(final_confidence),
        "zone_reason": reason,
        "zone_candidates": candidates,
        "zone_candidates_json": json.dumps(candidates),
        "antenna_id": winner.get("antenna_id"),
        "read_count": winner.get("zone_read_count", 1),
    }


def assign_zone_from_record(record: dict[str, Any]) -> dict[str, Any]:
    return assign_zone_from_reads([record])


def confidence_score(rssi: Any, read_count: Any, total_reads: int = 1) -> float:
    return round(
        (0.65 * rssi_score(rssi)) + (0.35 * read_rate_score(read_count, total_reads)),
        4
    )

# -------------------------------------------------------------------
# Backward compatibility for existing api.py imports
# Existing routes expect ANTENNA_ZONE_MAP to exist.
# The new zone engine uses DB-backed antenna mapping, but this keeps
# older routes working without changing api.py.
# -------------------------------------------------------------------
ANTENNA_ZONE_MAP = {
    1: {
        "zone_code": "A",
        "zone_name": "Door",
        "description": "Door / entry area",
        "mac": "16:12:D5",
        "reader": "Radar 1",
        "port": 1
    },
    2: {
        "zone_code": "B",
        "zone_name": "Conveyor",
        "description": "Conveyor / center read area",
        "mac": "13:A6:DF",
        "reader": "Radar 1",
        "port": 2
    },
    3: {
        "zone_code": "C",
        "zone_name": "Front North",
        "description": "Front North area",
        "mac": "13:A6:48",
        "reader": "Radar 2",
        "port": 1
    },
    4: {
        "zone_code": "D",
        "zone_name": "Back South / Storage",
        "description": "Back South storage area",
        "mac": "13:A6:48",
        "reader": "Radar 2",
        "port": 2
    },
}
