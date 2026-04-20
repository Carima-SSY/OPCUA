"""
OPC UA 클라이언트 패키지 (opc/)

DM400 OPC UA 서버와의 연결 · 인증 · 노드 탐색 · 구독 기능을 제공한다.

패키지 구성:
    config.py  — 서버 접속 상수 및 인증/보안 Enum (AuthMode, SecurityMode)
    handler.py — asyncua 구독 콜백 핸들러 (SubscriptionHandler)
    client.py  — 서버 연결·탐색·구독을 캡슐화한 핵심 클래스 (OPCClient)
    cli.py     — 터미널 대화형 설정 및 비동기 실행 로직

공개 인터페이스 (외부에서 from opc import ... 로 사용):
    OPCClient           — 서버 연결·탐색·구독 래퍼
    SubscriptionHandler — 데이터 변경 알림 기본 콜백
    AuthMode            — 사용자 인증 방식 Enum
    SecurityMode        — 채널 암호화 정책 Enum
    SERVER_ENDPOINT     — 기본 서버 엔드포인트 URL

사용 예:
    from opc import OPCClient, AuthMode, SecurityMode

    async with OPCClient(
        auth_mode=AuthMode.USERNAME,
        security_mode=SecurityMode.SIGN_ENCRYPT,
        username="admin",
        password="admin123",
    ) as client:
        await client.browse_and_subscribe()
        await client.run_forever()
"""

from opc.client import OPCClient
from opc.config import AuthMode, SecurityMode, SERVER_ENDPOINT
from opc.handler import SubscriptionHandler

__all__ = [
    "OPCClient",
    "SubscriptionHandler",
    "AuthMode",
    "SecurityMode",
    "SERVER_ENDPOINT",
]
