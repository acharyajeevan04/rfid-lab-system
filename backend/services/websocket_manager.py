"""In-memory WebSocket fan-out used by the live dashboard.

Every connected browser tab gets every event (new_scan, alert, drive_sync,
bulk_import, visitor_*, heartbeat — see README for the full event table).
There's a single module-level instance (`ws_manager`) shared across routes
and services so any part of the app can push a live update."""

from datetime import datetime, timezone
from typing import Dict, Any, List
from fastapi import WebSocket

class WebSocketManager:
    """Tracks connected clients and broadcasts JSON events to all of them."""

    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """Accept a new client connection and start tracking it."""
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket):
        """Stop tracking a client (called on disconnect or failed send)."""
        if websocket in self.active:
            self.active.remove(websocket)

    async def broadcast(self, event_type: str, payload: Dict[str, Any] | None = None):
        """Send one event to every connected client; drops clients that fail."""
        message = {
            "event_type": event_type,
            "payload": payload or {},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        disconnected = []
        for websocket in list(self.active):
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.append(websocket)

        for websocket in disconnected:
            self.disconnect(websocket)

ws_manager = WebSocketManager()
