"""
OPC UA 클라이언트 진입점 (opc_client.py)

실행:
    python opc_client.py

패키지 구조:
    opc/
      config.py  — 서버 접속 상수 + AuthMode/SecurityMode Enum
      handler.py — OPC UA 구독 이벤트 핸들러 (SubscriptionHandler)
      client.py  — OPC UA 연결·탐색·구독 핵심 클래스 (OPCClient)
      cli.py     — 터미널 대화형 설정 및 실행 로직 (prompt_config, main)
"""

import logging

from opc.cli import main

# ── 로깅 설정 (진입점에서 한 번만 설정) ──────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("asyncua").setLevel(logging.WARNING)   # asyncua 내부 로그 억제

if __name__ == "__main__":
    main()
