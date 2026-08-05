"""
scheduler.py
Automatic Google Drive polling scheduler.

Runs one sync shortly after startup and then checks Drive every
DRIVE_POLL_INTERVAL seconds.
"""

import asyncio
import logging

from backend.config import settings
from backend.database import SessionLocal
from backend.services.google_drive import drive_service
from backend.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)

_scheduler_task = None
_scheduler_running = False


async def _run_drive_sync(reason: str):
    db = SessionLocal()
    try:
        result = drive_service.sync(db)
        logger.info(
            "[AUTO DRIVE SYNC] reason=%s success=%s checked=%s processed=%s imported=%s zebra=%s impinj=%s errors=%s",
            reason,
            result.get("success"),
            result.get("files_checked"),
            result.get("new_files"),
            result.get("scans_imported"),
            result.get("zebra_files"),
            result.get("impinj_files"),
            result.get("errors"),
        )

        if result.get("scans_imported", 0) > 0:
            await ws_manager.broadcast("drive_sync", {
                "scans_imported": result.get("scans_imported", 0),
                "new_files": result.get("new_files", 0),
                "zebra_files": result.get("zebra_files", 0),
                "impinj_files": result.get("impinj_files", 0),
                "message": f"Auto Drive sync imported {result.get('scans_imported', 0)} scan(s).",
            })
    except Exception as e:
        logger.exception(f"Automatic Google Drive sync failed: {e}")
    finally:
        db.close()


async def _scheduler_loop():
    global _scheduler_running

    interval = int(getattr(settings, "DRIVE_POLL_INTERVAL", 30) or 30)
    if interval < 10:
        interval = 10

    # Initial startup sync.
    await asyncio.sleep(5)

    while _scheduler_running:
        await _run_drive_sync("scheduled_poll")
        await asyncio.sleep(interval)


def start_scheduler():
    global _scheduler_task, _scheduler_running

    if _scheduler_task and not _scheduler_task.done():
        logger.info("Scheduler already running")
        return

    _scheduler_running = True
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("Google Drive auto-sync scheduler started")


def stop_scheduler():
    global _scheduler_task, _scheduler_running

    _scheduler_running = False
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
    _scheduler_task = None
    logger.info("Google Drive auto-sync scheduler stopped")
