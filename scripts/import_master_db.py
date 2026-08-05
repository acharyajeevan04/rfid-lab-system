from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal, create_tables
from backend.services.master_import import import_master_from_excel


def main() -> int:
    parser = argparse.ArgumentParser(description="Import DB1 master unique RFID tags from Excel")
    parser.add_argument("workbook", type=Path, help="Path to RFID_System_FINAL_Lab_2026.xlsx")
    parser.add_argument("--sheet", default="DB1_MASTER", help="Worksheet name to import")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace DB1 master with workbook records. DB2 incoming scans are not touched.",
    )
    args = parser.parse_args()

    create_tables()
    db = SessionLocal()
    try:
        report = import_master_from_excel(db, args.workbook, replace=args.replace, sheet_name=args.sheet)
    finally:
        db.close()

    print(json.dumps(asdict(report), indent=2))
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
