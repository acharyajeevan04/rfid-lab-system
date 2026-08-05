RFID inventory database import patch
====================================

Purpose:
- Loads RFID_System_FINAL_Lab_2026.xlsx into DB1 master database.
- DB1 becomes the master database with unique EPC-to-item mappings.
- DB2 remains the scan database with all scan events.
- Existing DB2 scans are backfilled so the dashboard shows item name + SKU + GTIN instead of only EPC.
- Exports exports/unmapped_tags_review.csv for unknown/E280 cleanup.

How to apply:
1) Copy the scripts folder into your project root:
   ~/Desktop/rfid_final/rfid_final/scripts/import_lab_inventory_excel.py

2) Copy the data folder into your project root:
   ~/Desktop/rfid_final/rfid_final/data/RFID_System_FINAL_Lab_2026.xlsx

3) In VS Code terminal:
   cd ~/Desktop/rfid_final/rfid_final
   pkill -f uvicorn || true
   source .venv/bin/activate
   python -m pip install openpyxl
   cp rfid_lab.db rfid_lab_BACKUP_BEFORE_INVENTORY_IMPORT.db
   python scripts/import_lab_inventory_excel.py data/RFID_System_FINAL_Lab_2026.xlsx

4) Restart app:
   uvicorn main:app --reload --host 0.0.0.0 --port 8000

5) Open:
   http://127.0.0.1:8000

What to verify in UI:
- Asset Table: search SKU like SKU-BC-NMC-21700-01 or item name NMC 21700 Cell.
- Incoming Scans: matching EPCs should show asset names instead of only EPC.
- Export Scans CSV and Export Assets CSV should include item names/SKU/GTIN.
- exports/unmapped_tags_review.csv shows EPCs not in master database.
