"""
FastAPI 웹 애플리케이션 진입점 (app.py)

실행:
  cd client
  uvicorn web.app:app --host 0.0.0.0 --port 8000 --reload

또는:
  python -m web.app
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from web.api.routes import router as api_router
from web.opc_state import opc_state
from web.ws.manager import manager as ws_manager

# ── 로깅 설정 ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("asyncua").setLevel(logging.WARNING)

# ── 경로 상수 ─────────────────────────────────────────────────────────────────

_STATIC_DIR = Path(__file__).parent / "static"


# ── 앱 수명주기 ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    # 앱 종료 시 모든 OPC UA 연결 정리
    await opc_state.disconnect_all()


# ── FastAPI 앱 ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="OPC UA 모니터링 대시보드",
    description="DM400 OPC UA 서버 실시간 모니터링",
    version="1.0.0",
    lifespan=lifespan,
)

# REST API 라우터
app.include_router(api_router)

# 정적 파일 서빙 (/static/css/style.css, /static/js/app.js 등)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ── HTTP 엔드포인트 ───────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    """메인 대시보드 HTML 서빙."""
    return FileResponse(str(_STATIC_DIR / "index.html"))


# ── WebSocket 엔드포인트 ──────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    브라우저와 WebSocket 을 유지한다.

    OPC UA 데이터 변경(DataChange) 이 발생하면 WebSubscriptionHandler 가
    ws_manager.broadcast() 를 통해 모든 연결된 클라이언트로 푸시한다.
    """
    await ws_manager.connect(websocket)
    try:
        while True:
            # 클라이언트의 keep-alive 메시지 수신 (ping 처리)
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ── 직접 실행 ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("web.app:app", host="0.0.0.0", port=8000, reload=False)
