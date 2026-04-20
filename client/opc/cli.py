"""
CLI 대화형 설정 및 실행 로직 (opc/cli.py)

역할:
    터미널에서 대화형으로 연결 설정을 입력받고 OPCClient 를 실행한다.
    opc_client.py (루트 진입점) 에서 main() 을 import 하여 사용한다.

실행 흐름:
    1. prompt_config()   — 대화형으로 endpoint / 인증 방식 / 보안 정책 입력 수집
    2. main()            — 설정 출력 후 asyncio.run(_run(config)) 호출
    3. _run(config)      — OPCClient 생성 → 연결 → 탐색/구독 → 수신 대기 → 해제
"""

import asyncio
import getpass
import logging
from pathlib import Path

from opc.client import OPCClient
from opc.config import (
    CERTS_DIR,
    DEFAULT_CLIENT_CERT,
    DEFAULT_CLIENT_KEY,
    DEFAULT_SERVER_CERT,
    SERVER_ENDPOINT,
    AuthMode,
    SecurityMode,
)

logger = logging.getLogger(__name__)

_SEP = "─" * 72


# ── 출력 헬퍼 ─────────────────────────────────────────────────────────────────

def _section(title: str) -> None:
    """섹션 구분선과 제목을 출력한다."""
    print(f"\n{_SEP}\n  {title}\n{_SEP}")


# ── 대화형 설정 수집 ──────────────────────────────────────────────────────────

def prompt_config() -> dict:
    """
    터미널 대화형 입력으로 OPCClient 연결 설정을 수집하여 dict 로 반환한다.

    반환값은 OPCClient(**config) 에 직접 전달할 수 있는 키워드 인자 형식이다.

    수집 항목:
        - 서버 엔드포인트 URL
        - 인증 방식 (Anonymous / Username+Password / X.509 Certificate)
        - 채널 보안 정책 (NoSecurity / Basic256Sha256_SignAndEncrypt)
        - Username 인증 시: 사용자 이름, 비밀번호
        - 인증서 필요 시: 클라이언트 인증서, 개인키, 서버 인증서 경로
    """
    _section("DM400 OPC UA 클라이언트 연결 설정")

    # ── 서버 엔드포인트 ───────────────────────────────────────────────────────
    raw      = input(f"\n서버 엔드포인트 [{SERVER_ENDPOINT}]: ").strip()
    endpoint = raw or SERVER_ENDPOINT

    # ── 인증 방식 ─────────────────────────────────────────────────────────────
    print("\n[인증 방식 선택]")
    print("  1) Anonymous            (익명 — 이 서버에서 거부됩니다)")
    print("  2) Username / Password  (기본 계정: admin/admin123, operator/operator123)")
    print("  3) X.509 Certificate    (인증서 기반 사용자 토큰)")
    auth_choice = input("선택 [1]: ").strip() or "1"
    auth_mode = {
        "1": AuthMode.ANONYMOUS,
        "2": AuthMode.USERNAME,
        "3": AuthMode.CERTIFICATE,
    }.get(auth_choice, AuthMode.ANONYMOUS)

    # ── 채널 보안 정책 ────────────────────────────────────────────────────────
    print("\n[채널 보안 정책 선택]")
    print("  1) NoSecurity                    (암호화 없음 — 개발/테스트용)")
    print("  2) Basic256Sha256/SignAndEncrypt  (AES-256 암호화 — 운영 권장)")
    # X.509 인증은 암호화 채널 위에서 사용하는 것이 표준 권장사항
    default_sec = "2" if auth_mode == AuthMode.CERTIFICATE else "1"
    sec_choice  = input(f"선택 [{default_sec}]: ").strip() or default_sec
    security_mode = {
        "1": SecurityMode.NONE,
        "2": SecurityMode.SIGN_ENCRYPT,
    }.get(sec_choice, SecurityMode.NONE)

    config: dict = {
        "endpoint":      endpoint,
        "auth_mode":     auth_mode,
        "security_mode": security_mode,
    }

    # ── Username/Password 자격증명 ────────────────────────────────────────────
    if auth_mode == AuthMode.USERNAME:
        config["username"] = input("\n사용자 이름 [admin]: ").strip() or "admin"
        # getpass 는 에코 없이 입력받는다 (비밀번호 화면 노출 방지)
        config["password"] = getpass.getpass("비밀번호: ")

    # ── 인증서 경로 (암호화 채널 또는 X.509 인증 선택 시 필요) ──────────────
    needs_certs = (
        auth_mode     == AuthMode.CERTIFICATE
        or security_mode == SecurityMode.SIGN_ENCRYPT
    )
    if needs_certs:
        print(f"\n[인증서 경로 설정]  Enter = 기본값 사용")
        print(f"  기본 위치: {CERTS_DIR}")
        cc = input(f"  클라이언트 인증서 PEM [{DEFAULT_CLIENT_CERT.name}]: ").strip()
        ck = input(f"  클라이언트 개인키  PEM [{DEFAULT_CLIENT_KEY.name}]: ").strip()
        sc = input(f"  서버 인증서        PEM [{DEFAULT_SERVER_CERT.name}]: ").strip()
        config["client_cert"] = Path(cc) if cc else DEFAULT_CLIENT_CERT
        config["client_key"]  = Path(ck) if ck else DEFAULT_CLIENT_KEY
        config["server_cert"] = Path(sc) if sc else DEFAULT_SERVER_CERT

    return config


# ── 비동기 실행 ───────────────────────────────────────────────────────────────

async def _run(config: dict) -> None:
    """OPCClient 를 생성하고 연결 → 탐색/구독 → 수신 대기 → 해제 순서로 실행한다."""
    async with OPCClient(**config) as client:
        await client.browse_and_subscribe()
        await client.run_forever()


# ── CLI 진입점 ────────────────────────────────────────────────────────────────

def main() -> None:
    """
    대화형으로 설정을 수집하고 OPC UA 클라이언트를 실행한다.
    opc_client.py 에서 호출된다.
    """
    config = prompt_config()

    _section("연결 시도")
    print(f"  엔드포인트 : {config['endpoint']}")
    print(f"  인증 방식  : {config['auth_mode'].value}")
    print(f"  보안 정책  : {config['security_mode'].value}")
    print(_SEP)

    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        print("\n[종료됨]")
