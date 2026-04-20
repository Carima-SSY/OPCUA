"""
연결 상수 및 인증/보안 Enum (opc/config.py)

역할:
    - OPC UA 서버 접속에 필요한 상수값을 중앙화하여 관리한다.
    - AuthMode / SecurityMode Enum 으로 타입 안전한 설정을 제공한다.
    - 인증서 기본 경로를 server/certs/ 로 지정하여 gen_certs.py 출력과 연동한다.

설계 원칙:
    - 이 파일만 변경하면 엔드포인트 · 주기 · 인증서 경로 전체가 반영된다.
    - AuthMode 와 SecurityMode 는 채널(암호화) · 세션(인증) 개념을 분리하여
      조합 선택이 명확하도록 Enum 으로 정의한다.
"""

from enum import Enum
from pathlib import Path

# ── 서버 접속 정보 ────────────────────────────────────────────────────────────

# OPC UA 서버가 바인딩된 엔드포인트 URL.
# 0.0.0.0 으로 실행된 서버에 로컬에서 접속할 때는 localhost 를 사용한다.
SERVER_ENDPOINT = "opc.tcp://localhost:4840/carimatec/dm400"

# 클라이언트 ApplicationUri.
# OPC UA 스택은 이 값을 인증서의 SAN(URI) 필드와 비교하여 채널 협상을 검증한다.
# gen_certs.py 의 CLIENT_APP_URI 와 반드시 일치해야 BadCertificateUriInvalid 오류를 방지한다.
CLIENT_APP_URI = "urn:carimatec:opcua:client"

# ── 구독 설정 ─────────────────────────────────────────────────────────────────

# 데이터 변경 구독 갱신 주기 (밀리초).
# 500ms = 초당 최대 2회 폴링. 장비 데이터 특성상 이 주기로 충분하다.
# 더 빠른 갱신이 필요하면 낮추고, 네트워크 부하를 줄이려면 높인다.
SUBSCRIPTION_PERIOD_MS = 500

# ── 인증서 경로 ───────────────────────────────────────────────────────────────
# server/gen_certs.py 가 server/certs/ 에 서버·클라이언트 인증서를 모두 생성한다.
# 클라이언트는 해당 파일을 그대로 참조하여 별도 인증서 관리 부담을 최소화한다.

# client/ 패키지의 부모(OPCUA/) 기준으로 server/certs/ 를 찾는다.
_SERVER_DIR = Path(__file__).parent.parent.parent / "server"
CERTS_DIR   = _SERVER_DIR / "certs"

DEFAULT_CLIENT_CERT = CERTS_DIR / "client_cert.pem"   # 클라이언트 Self-signed 인증서
DEFAULT_CLIENT_KEY  = CERTS_DIR / "client_key.pem"    # 클라이언트 RSA 개인키
DEFAULT_SERVER_CERT = CERTS_DIR / "server_cert.pem"   # 서버 인증서 (신뢰 앵커)


# ── Enum ──────────────────────────────────────────────────────────────────────

class AuthMode(Enum):
    """
    OPC UA ActivateSession 에서 사용할 사용자 ID 토큰 방식.

    OPC UA 스펙은 채널(암호화)과 세션(인증)을 독립적으로 정의한다.
    이 Enum 은 세션 활성화 단계의 IdentityToken 종류를 표현한다.

    값:
        ANONYMOUS   — AnonymousIdentityToken. 인증 없이 연결.
                      서버 UserManager 설정에 따라 거부될 수 있다.
        USERNAME    — UserNameIdentityToken. 사용자 이름 + 비밀번호.
                      서버에서 PBKDF2-SHA256 으로 검증한다.
        CERTIFICATE — X509IdentityToken. 클라이언트 인증서 + 개인키 서명.
                      서버 pki/trusted/certs/ 에 클라이언트 인증서가 등록되어야 한다.
    """
    ANONYMOUS   = "anonymous"
    USERNAME    = "username"
    CERTIFICATE = "certificate"


class SecurityMode(Enum):
    """
    OPC UA OpenSecureChannel 에서 사용할 메시지 보안 정책.

    이 Enum 은 채널 수준 암호화(MessageSecurityMode)를 표현한다.
    AuthMode 와 독립적으로 선택할 수 있다.

    값:
        NONE         — NoSecurity. 평문 전송. 개발·테스트 환경 전용.
        SIGN_ENCRYPT — Basic256Sha256 / SignAndEncrypt.
                       AES-256 암호화 + SHA-256 서명. 운영 환경 권장.
    """
    NONE         = "none"
    SIGN_ENCRYPT = "sign_encrypt"
