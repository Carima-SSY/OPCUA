"""
REST API 엔드포인트 (routes.py)

/api/connect    POST  — OPC UA 서버 연결
/api/disconnect POST  — 연결 해제
/api/status     GET   — 연결 상태 조회
/api/nodes      GET   — 노드 트리 반환
/api/values     GET   — 현재 노드 값 전체 반환
/api/defaults   GET   — 기본 설정값 반환 (프론트엔드 폼 초기화용)
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
    """OPC UA 서버에 연결하고 노드 트리를 반환한다."""
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
    if req.username:
        config["username"] = req.username
    if req.password:
        config["password"] = req.password
    if req.client_cert:
        config["client_cert"] = Path(req.client_cert)
    if req.client_key:
        config["client_key"] = Path(req.client_key)
    if req.server_cert:
        config["server_cert"] = Path(req.server_cert)

    try:
        result = await opc_state.connect(config)
        return {"status": "connected", **result}
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("[API /connect] 오류")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/disconnect")
async def disconnect():
    """OPC UA 연결을 해제한다."""
    await opc_state.disconnect()
    return {"status": "disconnected"}


@router.get("/status")
async def status():
    """현재 연결 상태와 구독 노드 수를 반환한다."""
    return {
        "connected":  opc_state.connected,
        "node_count": len(opc_state.node_values),
    }


@router.get("/nodes")
async def nodes():
    """노드 트리를 반환한다 (연결 후에만 유효)."""
    if not opc_state.connected:
        raise HTTPException(status_code=400, detail="서버에 연결되어 있지 않습니다.")
    return {"tree": opc_state.node_tree}


@router.get("/values")
async def values():
    """구독 중인 모든 Variable 노드의 현재 값을 반환한다."""
    if not opc_state.connected:
        raise HTTPException(status_code=400, detail="서버에 연결되어 있지 않습니다.")
    return opc_state.node_values


@router.get("/defaults")
async def defaults():
    """프론트엔드 폼 초기화에 사용할 기본값을 반환한다."""
    return {
        "endpoint":    SERVER_ENDPOINT,
        "client_cert": str(DEFAULT_CLIENT_CERT),
        "client_key":  str(DEFAULT_CLIENT_KEY),
        "server_cert": str(DEFAULT_SERVER_CERT),
    }
