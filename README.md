# SEARLab RFID Lab Management System
**SEARLab UTA · 2025/26 · Python FastAPI + SQLite + Google Drive + WebSocket + MQTT + Impinj Octane SDK**

---

## Project structure

```
rfid_final/
├── main.py                          ← FastAPI entry point (run this)
├── rfid_reader_service.py           ← Impinj Octane SDK + MQTT publisher
├── requirements.txt
├── .env.example                     ← Copy to .env and fill in values
├── Dockerfile
├── docker-compose.yml               ← Full stack: backend + Mosquitto + reader
├── docker/mosquitto/mosquitto.conf
├── frontend/
│   └── index.html                   ← Complete SPA (served at http://localhost:8000)
└── backend/
    ├── config.py                    ← All settings (reads .env)
    ├── database.py                  ← SQLAlchemy engine
    ├── models.py                    ← DB1_master, DB2_incoming, scan_sessions, drive_sync
    ├── schemas.py                   ← Pydantic request/response schemas
    ├── seed_data.py                 ← Seeds 87 assets + 15 sessions on first run
    ├── routes/
    │   ├── assets.py                ← GET/POST/PUT /api/assets + /api/assets/epc/{epc}
    │   ├── scans.py                 ← POST /api/scans (push) + /api/scans/bulk (CSV)
    │   └── api.py                   ← /api/dashboard, /api/sessions, /api/drive
    └── services/
        ├── verification.py          ← EPC → DB1 lookup, zone mismatch, duplicate check
        ├── drive_parser.py          ← Exact parsers for Zebra + Impinj CSV formats
        ├── google_drive.py          ← OAuth2 + dual folder sync (Zebra + ItemTest)
        ├── websocket_manager.py     ← WebSocket broadcast to all browser clients
        ├── mqtt_service.py          ← MQTT subscriber → DB2 + WebSocket
        └── scheduler.py            ← APScheduler: Drive poll every 30s + heartbeat
```

---

## Quick start (3 steps)

### 1. Install dependencies
```bash
cd rfid_final
pip install -r requirements.txt
```

### 2. Configure
```bash
cp .env.example .env
# Minimum required: DATABASE_URL is already set to SQLite (no changes needed to start)
# Add GOOGLE_DRIVE_ZEBRA_FOLDER_ID and GOOGLE_DRIVE_IMPINJ_FOLDER_ID for Drive sync
```

### 3. Run
```bash
python main.py
```

Open: **http://localhost:8000**

The server automatically:
- Creates the SQLite database (`rfid_lab.db`)
- Seeds all 87 assets + 15 scan sessions
- Starts polling Google Drive every 30 seconds
- Serves the frontend dashboard

---

## Google Drive setup

Your Drive already has the two folders. You just need the folder IDs and credentials.

### Step 1 — Get Drive folder IDs

From your Google Drive:
- Open **Zebra Scans** folder → copy the ID from the URL:
  `drive.google.com/drive/folders/`**`THIS_PART_IS_THE_ID`**
- Open **ItemTest Scans** folder → copy its ID

Add to `.env`:
```env
GOOGLE_DRIVE_ZEBRA_FOLDER_ID=paste_zebra_scans_folder_id_here
GOOGLE_DRIVE_IMPINJ_FOLDER_ID=paste_itemtest_scans_folder_id_here
GOOGLE_DRIVE_ACCOUNT=searlabuta@gmail.com
```

### Step 2 — Get credentials.json

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create project → Enable **Google Drive API**
3. APIs & Services → Credentials → **Create OAuth 2.0 Client ID** → Desktop app
4. Download JSON → rename to `credentials.json` → place in project root

### Step 3 — First-time authentication

```bash
python main.py
# Browser opens automatically for Google OAuth consent
# After approval, token.json is saved — Drive syncs automatically from then on
```

### CSV formats supported

**Zebra MC3300R** (`RFID_*.csv`) — from Zebra Scans folder:
```
INVENTORY SUMMARY
UNIQUE COUNT:,1
TOTAL COUNT:,286
READ TIME:,00:00:00

none,TAG,COUNT,RSSI
A02A061028A201551A022002,A02A061028A201551A022002,286,-35
```

**Impinj R700** (`Impinj_Item_Test_*.csv`) — from ItemTest Scans folder:
```
// 4/9/2026 2:15:32 PM
// ReaderName=impinj-16-12-D5.local, AntennaIDs=1,...
// Timestamp, EPC, TID, Antenna, RSSI, Frequency, Hostname, ...
2026-04-09T14:24:20.255-05:00,300833B2DDD9014000000000,,1,-65.5,927.25,...
```

Format is **auto-detected** — no configuration needed.

---

## Scanner push endpoint (MC3300R direct HTTP)

The Zebra MC3300R can push scans directly via HTTP POST:

```
POST http://<server-ip>:8000/api/scans
Header: X-Scanner-Key: rfid-scanner-secret-key
Content-Type: application/json

{
  "epc": "A02A061028A201551A022002",
  "scanned_zone": "A",
  "rssi_dbm": -35,
  "reader_id": "mc3300r-lab01",
  "source": "scanner"
}
```

Each push:
1. Verifies EPC against DB1 (MATCHED / MISMATCH / UNKNOWN / DUPLICATE)
2. Saves to DB2
3. Broadcasts to all browser clients via WebSocket (live dashboard updates instantly)

---

## Impinj Octane SDK reader service (Phase 1 + 2)

### Install
```bash
pip install octane-sdk-python paho-mqtt
```

### Configure .env
```env
RFID_READER_ENABLED=true
IMPINJ_READER_HOST=192.168.1.100    # your Impinj R700 IP address
RSSI_THRESHOLD=-75                  # filter reads weaker than this (dBm)
```

### Antenna → Zone mapping

Edit `rfid_reader_service.py`:
```python
ANTENNA_ZONE_MAP = {
    1: "A",   # Antenna 1 → Zone A (Battery Storage)
    2: "B",   # Antenna 2 → Zone B (Workbench)
    3: "C",   # Antenna 3 → Zone C (Storage East)
    4: "D",   # Antenna 4 → Zone D (Entrance/Testing)
}
```

### Run standalone
```bash
python rfid_reader_service.py --mode reader
```

---

## MQTT setup (Phase 2)

### Start Mosquitto broker
```bash
# Docker (easiest)
docker run -d -p 1883:1883 -p 9001:9001 eclipse-mosquitto:2.0

# Or via docker-compose
docker-compose up -d mosquitto
```

### Enable in .env
```env
MQTT_ENABLED=true
MQTT_BROKER_HOST=localhost
MQTT_BROKER_PORT=1883
```

### MQTT topic schema
```
Topic:   lab/rfid/{reader_id}/{antenna_id}/read
Payload: {
  "epc":       "E2003412...",
  "rssi":      -62,
  "timestamp": "2026-04-09T14:22:01Z",
  "asset_id":  "BAT-CELL-042",
  "antenna":   1,
  "reader":    "impinj-r700-lab01"
}
```

Test subscription:
```bash
mosquitto_sub -t "lab/rfid/#" -v
```

---

## Full Docker stack

```bash
# Start everything: backend + Mosquitto broker + reader service
docker-compose up -d

# Logs
docker-compose logs -f rfid-backend
docker-compose logs -f mosquitto
docker-compose logs -f rfid-reader
```

---

## API reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Frontend dashboard |
| GET | `/api/health` | Health check + status |
| GET | `/api/dashboard` | KPI stats, zone/category/status breakdown |
| GET | `/api/assets` | List DB1 (filter: zone, category, status, q) |
| GET | `/api/assets/count` | Asset count |
| GET | `/api/assets/epc/{epc}` | Verify EPC against DB1 |
| GET | `/api/assets/{record_id}` | Get single asset |
| PUT | `/api/assets/{record_id}` | Update asset |
| POST | `/api/assets` | Add new asset to DB1 |
| GET | `/api/scans` | List DB2 (filter: status, zone, source, q) |
| GET | `/api/scans/stats` | Verification status counts |
| POST | `/api/scans` | Ingest single scan (scanner push) |
| POST | `/api/scans/bulk` | Upload CSV (Zebra or Impinj auto-detected) |
| GET | `/api/sessions` | List scan sessions |
| POST | `/api/sessions` | Create session |
| GET | `/api/drive/status` | Drive connection + sync stats |
| POST | `/api/drive/sync` | Trigger manual Drive sync |
| POST | `/api/drive/connect` | Start OAuth2 flow |
| WS | `/ws` | WebSocket live event feed |
| GET | `/docs` | Interactive API docs (Swagger UI) |

---

## WebSocket events

Connect to `ws://localhost:8000/ws`:

| Event | Payload | When |
|-------|---------|------|
| `connected` | `{message, clients}` | On WS connect |
| `new_scan` | `{scan_id, epc, zone, asset, status, time, date, reader, source}` | Every tag read |
| `alert` | `{scan_id, epc, status, message, zone}` | MISMATCH or UNKNOWN |
| `drive_sync` | `{scans_imported, new_files, zebra_files, impinj_files}` | Drive import complete |
| `bulk_import` | `{count, source, filename}` | CSV upload complete |
| `heartbeat` | `{ts, clients}` | Every 10 seconds |

---

## Database tables

| Table | Contents |
|-------|----------|
| `db1_master` | 87 verified asset records (seeded from Excel) |
| `db2_incoming` | All incoming scans from any source |
| `scan_sessions` | 15 historical sessions + new ones |
| `drive_sync` | Drive file import tracking |

Default: SQLite (`rfid_lab.db`) — zero setup.
Production: `DATABASE_URL=postgresql://user:pass@host:5432/rfid_lab`

---

## Building SEARLab_RFID_App.exe (for your professor)

PyInstaller builds for whatever OS it runs on — it can't cross-compile a
Windows `.exe` from Linux/Mac. This project was built and tested in a Linux
sandbox (proving the `.spec` file, hidden imports, and app all work), but the
real `.exe` has to be built **on a Windows machine** (yours, or the lab PC).

Steps on Windows:
```
cd rfid_final
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
pyinstaller SEARLab_RFID_App.spec --noconfirm
```
The finished app is in `dist\SEARLab_RFID_App\` — hand your professor that
whole folder (not just the `.exe`; it needs the files next to it). Double-
clicking `SEARLab_RFID_App.exe` opens a browser to the dashboard automatically.

Notes:
- `credentials.json`/`token.json` are **not** bundled into the build anymore
  (they used to be — that shipped live OAuth secrets inside the binary). If
  you want Google Drive sync to work on the professor's machine, copy your
  own `credentials.json` into the `dist\SEARLab_RFID_App\` folder after
  building; otherwise the app runs fine with Drive sync simply disabled.
- The database seeds fresh on first run, so every install starts from the
  same clean demo data.
- `.env` ships with `APP_ENV=production`, so the packaged app never runs
  uvicorn's `--reload` file-watcher (that was actually causing a restart
  loop in dev mode — see below).

---

## Credentials

Google Drive account: `searlabuta@gmail.com` (Drive folders: Zebra Scans, ItemTest Scans).

Do **not** store the account password in this file or in any shared copy of the
project. Auth is handled via OAuth2 (`credentials.json` + `token.json`, both
already git-ignored) — see "Google Drive setup" above. If a plaintext password
for this account was ever written down anywhere, rotate it.

---

## Scope note: RTLS / digital twin

This repo covers the "replace Clearstream" half of the lab's RFID goals: an
Impinj Octane SDK → MQTT → FastAPI/WebSocket pipeline, Zebra handheld sync via
Google Drive, EPC verification against the master asset table, and a live
zone dashboard. Zone assignment here is presence-based (which antenna heard
the tag, weighted by RSSI/read-rate — see `backend/services/zone_engine.py`),
not true spatial positioning.

The open-source UWB/RTLS positioning piece (e.g. replacing Ubisense SmartSpace
with a Decawave/Qorvo-based stack) and a full digital-twin visualization are
**not implemented here** — they're a separate, later-phase project.
