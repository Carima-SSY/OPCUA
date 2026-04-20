"""
WebSocket 연결 관리자 (manager.py)

연결된 모든 브라우저 클라이언트를 관리하고 OPC UA 데이터 변경을 브로드캐스트한다.
"""

import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """활성 WebSocket 연결 목록을 관리하고 브로드캐스트를 처리한다."""

    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.info("[WS] 클라이언트 연결 — 현재 %d 명", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)
        logger.info("[WS] 클라이언트 해제 — 현재 %d 명", len(self._connections))

    async def broadcast(self, data: dict[str, Any]) -> None:
        """연결된 모든 클라이언트에 JSON 메시지를 전송한다."""
        if not self._connections:
            return
        message = json.dumps(data, ensure_ascii=False, default=str)
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def client_count(self) -> int:
        return len(self._connections)


# 앱 전역 싱글턴
manager = ConnectionManager()
