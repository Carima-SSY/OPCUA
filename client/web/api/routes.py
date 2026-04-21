"""
REST API 엔드포인트 (routes.py)

/api/connect             POST  — OPC UA 서버 연결 (server_id 반환)
/api/disconnect/{sid}    POST  — 특정 서버 연결 해제
/api/servers             GET   — 연결된 서버 목록
/api/values              GET   — 전체 서버 노드 값 (server_id 별 그룹)
/api/values/{sid}        GET   — 특정 서버 노드 값
/api/defaults            GET   — 기본 설정값 반환
"""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from opc.config import (
    SERVER_ENDPOINT,
    DEFAULT_CLIENT_CERT, DEFAULT_CLIENT_KEY, DEFAULT_SERVER_CERT,
    AuthMode, SecurityMode,
)
from web.opc_state import opc_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["OPC UA"])


# ── 요청 스키마 ───────────────────────────────────────────────────────────────

class ConnectRequest(BaseModel):
    endpoint:      str        = SERVER_ENDPOINT
    auth_mode:     str        = "anonymous"
    security_mode: str        = "none"
    username:      str | None = None
    password:      str | None = None
    client_cert:   str | None = None
    client_key:    str | None = None
    server_cert:   str | None = None


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.post("/connect")
async def connect(req: ConnectRequest):
    """OPC UA 서버에 연결하고 server_id 와 노드 트리를 반환한다."""
    try:
        auth_mode     = AuthMode(req.auth_mode)
        security_mode = SecurityMode(req.security_mode)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    config: dict = {
        "endpoint":      req.endpoint,
        "auth_mode":     auth_mode,
        "security_mode": security_mode,
    }
    if req.username:    config["username"]    = req.username
    if req.password:    config["password"]    = req.password
    if req.client_cert: config["client_cert"] = Path(req.client_cert)
    if req.client_key:  config["client_key"]  = Path(req.client_key)
    if req.server_cert: config["server_cert"] = Path(req.server_cert)

    try:
        result = await opc_state.connect(config)
        return {"status": "connected", **result}
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("[API /connect] 오류")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/disconnect/{server_id}")
async def disconnect(server_id: str):
    """특정 서버 연결을 해제한다."""
    if server_id not in opc_state.sessions:
        raise HTTPException(status_code=404, detail="서버를 찾을 수 없습니다.")
    await opc_state.disconnect(server_id)
    return {"status": "disconnected", "server_id": server_id}


@router.get("/servers")
async def servers():
    """연결된 모든 서버 목록을 반환한다."""
    return {
        "servers": [
            {
                "server_id":  sid,
                "endpoint":   s.endpoint,
                "node_count": len(s.node_values),
            }
            for sid, s in opc_state.sessions.items()
        ]
    }


@router.get("/values")
async def values():
    """모든 서버의 현재 노드 값을 server_id 로 그룹화하여 반환한다."""
    return {
        sid: s.node_values
        for sid, s in opc_state.sessions.items()
    }


@router.get("/values/{server_id}")
async def values_by_server(server_id: str):
    """특정 서버의 현재 노드 값을 반환한다."""
    session = opc_state.sessions.get(server_id)
    if not session:
        raise HTTPException(status_code=404, detail="서버를 찾을 수 없습니다.")
    return session.node_values


@router.get("/defaults")
async def defaults():
    """프론트엔드 폼 초기화에 사용할 기본값을 반환한다."""
    return {
        "endpoint":    SERVER_ENDPOINT,
        "client_cert": str(DEFAULT_CLIENT_CERT),
        "client_key":  str(DEFAULT_CLIENT_KEY),
        "server_cert": str(DEFAULT_SERVER_CERT),
    }
