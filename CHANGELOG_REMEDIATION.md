# Remediation pass — Aug 2026

Fixes applied and verified (fresh venv, live server, live packaged exe test):

1. **Missing `backend/services/mqtt_service.py`** — `main.py` imported this
   module but it didn't exist, so MQTT never actually worked even though the
   README documented it. Written from scratch: subscribes to the reader's
   MQTT topic, runs each read through the same zone-assignment/verification
   logic as a manual scan, writes DB2, broadcasts to the live dashboard.

2. **App crashed on startup if Google Drive auth failed** — no try/except
   around `drive_service.authenticate()`. Any expired token / no network / no
   browser took the *whole app* down, not just Drive sync. Now fails soft:
   logs an error, everything else (dashboard, scans, assets, MQTT) still runs.

3. **`antenna_id` type mismatch → 500 on every scan** — `models.py` and
   `schemas.py` typed `antenna_id` as `Integer`/`int`, but `zone_engine.py`
   always returns normalized string IDs like `"ANT-02"`. Every `POST
   /api/scans` that resolved an antenna threw a 500 on response
   serialization. Fixed: DB2/`visitor_location_events` columns → `String`,
   response schemas → `Optional[str]`, request schema (`ScanCreate`) accepts
   `Union[int, str]` since real scanners send plain ints.

4. **`APP_ENV=development` caused an auto-reload loop** — uvicorn's
   `--reload` file-watcher was watching the whole project, including
   `rfid_lab.db`. Every scan/seed write to the db counted as a "file change"
   and restarted the server. Changed default to `APP_ENV=production`
   (`launcher.py` already hardcoded `reload=False` for the packaged app, so
   this only affected running via `python main.py` directly).

5. **Plaintext Google account password in README.md**, plus
   `credentials.json`/`token.json` (real OAuth secrets) bundled directly into
   the PyInstaller build. Removed the password from README, stripped both
   files from `SEARLab_RFID_App.spec`'s bundled `datas`.

6. **25 `*_BACKUP_*` files** (old `.py`/`.html`/`.db` versions kept in the
   working tree instead of relying on git history) — deleted.

7. **Docstring gaps** — added to `verification.py`, `websocket_manager.py`,
   `routes/visitors.py`, and the new `mqtt_service.py`.

8. **Scope note added to README** clarifying that this repo implements the
   Impinj/Clearstream-replacement half of the lab's RFID goals, not the
   open-source UWB/RTLS positioning or digital-twin piece — that's a
   separate, later-phase project.

Not fixed / out of scope for this pass: the zone-assignment logic itself is
presence-based (antenna heard the tag), not spatial positioning — see the
scope note above.
