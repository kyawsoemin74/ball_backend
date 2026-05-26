from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.services.socket_service import manager

router = APIRouter()


@router.websocket("/ws/live")
async def websocket_live_score(websocket: WebSocket, match_id: Optional[int] = Query(None)):
    await manager.connect(websocket, match_id=match_id)
    try:
        while True:
            message = await websocket.receive_text()
            if message.lower() == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        await manager.disconnect(websocket)
