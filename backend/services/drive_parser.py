"""
drive_parser.py
Flexible parsers for Zebra MC3300R and Impinj R700 CSV outputs.
"""

import csv
import logging
import re
from datetime import datetime, timezone
from io import StringIO

logger = logging.getLogger(__name__)


def detect_format(text: str, filename: str = "") -> str:
    name = (filename or "").lower()
    low = (text or "").lstrip("\ufeff").lower()[:2000]

    if "inventory summary" in low or "none,tag,count,rssi" in low or name.startswith("rfid_"):
        return "zebra"

    if (
        name.startswith("impinj")
        or "readername=" in low
        or "// timestamp" in low
        or "timestamp, epc" in low
        or "timestamp,epc" in low
        or ("antenna" in low and "rssi" in low and "epc" in low)
    ):
        return "impinj"

    return "unknown"


def parse_zebra(csv_text: str, filename: str = "") -> list[dict]:
    records = []
    total_count = 0
    date_str, time_str = _dt_from_filename(filename)

    lines = [ln.strip() for ln in csv_text.splitlines() if ln.strip()]

    for line in lines:
        if line.upper().startswith("TOTAL COUNT"):
            try:
                total_count = int(line.split(",")[1])
            except Exception:
                pass

    header_idx = None
    headers = []
    for i, line in enumerate(lines):
        row = next(csv.reader([line]))
        normalized = [c.strip().upper() for c in row]
        if "TAG" in normalized and "COUNT" in normalized:
            header_idx = i
            headers = normalized
            break

    if header_idx is None:
        logger.warning(f"Zebra parser could not find TAG/COUNT/RSSI header in {filename}")
        return records

    tag_i = headers.index("TAG") if "TAG" in headers else 1
    count_i = headers.index("COUNT") if "COUNT" in headers else 2
    rssi_i = headers.index("RSSI") if "RSSI" in headers else 3

    for line in lines[header_idx + 1:]:
        try:
            row = next(csv.reader([line]))
        except Exception:
            continue

        if len(row) <= tag_i:
            continue

        epc = row[tag_i].strip().upper()
        if not epc or len(epc) < 8 or epc in {"TAG", "NONE"}:
            continue

        try:
            count = int(float(row[count_i].strip())) if len(row) > count_i and row[count_i].strip() else 1
        except Exception:
            count = 1

        try:
            rssi = int(float(row[rssi_i].strip())) if len(row) > rssi_i and row[rssi_i].strip() else -60
        except Exception:
            rssi = -60

        records.append({
            "epc": epc,
            "rssi": rssi,
            "count": count,
            "read_count": count,
            "reader_id": "mc3300r-lab01",
            "antenna": None,
            "source": "zebra_drive",
            "scan_date": date_str,
            "scan_time": time_str,
            "filename": filename,
            "total_reads_session": total_count,
        })

    logger.info(f"Zebra parse '{filename}': {len(records)} unique tag(s), total reads={total_count}")
    return records


def parse_impinj(csv_text: str, filename: str = "") -> list[dict]:
    records = []
    reader_name = "impinj-r700-lab01"
    data_lines = []
    header = None

    for raw in csv_text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue

        if line.startswith("//"):
            content = line[2:].strip()
            if "ReaderName=" in content:
                m = re.search(r"ReaderName=([^,\s]+)", content)
                if m:
                    reader_name = m.group(1).strip()
            # Header is often commented: // Timestamp, EPC, TID, Antenna, RSSI, ...
            if "timestamp" in content.lower() and "epc" in content.lower():
                try:
                    header = [c.strip().lower() for c in next(csv.reader([content]))]
                except Exception:
                    header = None
            continue

        # Header may also be uncommented.
        if "timestamp" in line.lower() and "epc" in line.lower() and not re.match(r"^\d{4}-\d{2}-\d{2}", line):
            try:
                header = [c.strip().lower() for c in next(csv.reader([line]))]
            except Exception:
                header = None
            continue

        data_lines.append(line)

    def idx(name: str, default: int) -> int:
        if header and name in header:
            return header.index(name)
        return default

    ts_i = idx("timestamp", 0)
    epc_i = idx("epc", 1)
    ant_i = idx("antenna", 3)
    rssi_i = idx("rssi", 4)

    for line in data_lines:
        try:
            row = next(csv.reader([line]))
        except Exception:
            continue

        if len(row) <= epc_i:
            continue

        epc = row[epc_i].strip().upper()
        if not epc or len(epc) < 8 or epc == "EPC":
            continue

        try:
            rssi = int(float(row[rssi_i].strip())) if len(row) > rssi_i and row[rssi_i].strip() else -60
        except Exception:
            rssi = -60

        try:
            antenna = int(float(row[ant_i].strip())) if len(row) > ant_i and row[ant_i].strip() else 1
        except Exception:
            antenna = 1

        scan_date, scan_time = _parse_timestamp(row[ts_i].strip() if len(row) > ts_i else "", filename)

        records.append({
            "epc": epc,
            "rssi": rssi,
            "antenna": antenna,
            "reader_id": reader_name,
            "source": "impinj_drive",
            "scan_date": scan_date,
            "scan_time": scan_time,
            "filename": filename,
        })

    logger.info(f"Impinj parse '{filename}': {len(records)} raw read(s)")
    return records


def deduplicate_impinj(raw: list[dict], threshold: int = -100) -> list[dict]:
    """Aggregate Impinj raw reads by EPC.

    We use a permissive RSSI threshold so real demo scans are not dropped.
    Zone confidence later uses RSSI/read count to decide location quality.
    """
    best: dict[str, dict] = {}

    for r in raw:
        if r.get("rssi", -999) < threshold:
            continue

        epc = r["epc"]
        if epc not in best:
            best[epc] = dict(r)
            best[epc]["count"] = 0
            best[epc]["read_count"] = 0
            best[epc]["antenna_reads"] = {}

        best[epc]["count"] += 1
        best[epc]["read_count"] += 1

        ant = str(r.get("antenna") or "?")
        best[epc]["antenna_reads"][ant] = best[epc]["antenna_reads"].get(ant, 0) + 1

        # Keep strongest RSSI as representative read.
        if r.get("rssi", -999) > best[epc].get("rssi", -999):
            keep_count = best[epc]["count"]
            keep_read_count = best[epc]["read_count"]
            keep_antenna_reads = best[epc]["antenna_reads"]
            best[epc].update(r)
            best[epc]["count"] = keep_count
            best[epc]["read_count"] = keep_read_count
            best[epc]["antenna_reads"] = keep_antenna_reads

    result = list(best.values())
    logger.info(f"Impinj aggregate: {len(raw)} raw read(s) → {len(result)} unique EPC(s)")
    return result


def parse_auto(csv_text: str, filename: str = "") -> tuple[str, list[dict]]:
    fmt = detect_format(csv_text, filename)

    if fmt == "zebra":
        return "zebra", parse_zebra(csv_text, filename)

    if fmt == "impinj":
        raw = parse_impinj(csv_text, filename)
        return "impinj", deduplicate_impinj(raw)

    logger.warning(f"Unknown CSV format: {filename}")
    return "unknown", []


def _parse_timestamp(ts_raw: str, filename: str) -> tuple[str, str]:
    ts_raw = (ts_raw or "").strip()
    if not ts_raw:
        return _dt_from_filename(filename)

    # Example: 2026-04-09T14:24:20.2551820-05:00
    try:
        clean = re.sub(r"\.\d+", "", ts_raw)
        clean = re.sub(r"[+-]\d{2}:\d{2}$", "", clean)
        dt = datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S")
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
    except Exception:
        pass

    # Common CSV date/time fallback.
    for fmt in ["%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
        try:
            dt = datetime.strptime(ts_raw, fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
        except Exception:
            continue

    return _dt_from_filename(filename)


def _dt_from_filename(filename: str) -> tuple[str, str]:
    m = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})", filename or "")
    if m:
        return m.group(1), f"{m.group(2)}:{m.group(3)}:{m.group(4)}"

    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")
