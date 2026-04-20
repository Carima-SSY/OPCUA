"""
OPC UA 사용자 인증 관리자 (user_manager.py)

역할:
  - asyncua 의 UserManager 인터페이스를 구현하여 OPC UA 세션 인증을 처리
  - users.json 에 저장된 계정 정보로 사용자 이름/비밀번호 인증을 수행
  - X.509 인증서 기반 사용자 토큰 인증도 지원

지원 인증 방식:
  1. 사용자 이름 + 비밀번호 (UserNameIdentityToken)
     - 비밀번호는 PBKDF2-SHA256(반복 200,000회)로 해싱하여 users.json 에 저장
  2. X.509 인증서 사용자 토큰 (X509IdentityToken)
     - TLS 채널 수준 검증은 asyncua 가 처리, 여기서는 CN 로깅 후 허용
  3. 익명(Anonymous) → 무조건 거부

users.json 구조:
  {
    "username": {
      "role": "admin" | "operator",
      "salt": "<hex>",   // 32바이트 랜덤 솔트
      "hash": "<hex>"    // PBKDF2-SHA256 결과
    }
  }
"""

import json
import hashlib
import logging
from pathlib import Path

from cryptography.x509.oid import NameOID
from asyncua.server.user_managers import UserManager

logger = logging.getLogger(__name__)

# users.json 기본 경로: 이 파일과 같은 디렉터리
USERS_FILE = Path(__file__).parent / "users.json"

# gen_certs.py 의 _hash_password() 와 반복 횟수를 반드시 일치시켜야 검증 성공
PBKDF2_ITERATIONS = 200_000


class OPCUserManager(UserManager):
    """
    OPC UA 세션 인증을 처리하는 사용자 관리자.

    asyncua.Server 에 set_user_manager(OPCUserManager()) 로 등록하면
    클라이언트가 세션을 열 때마다 get_user() 가 호출된다.

    Attributes:
        users_file (Path): 사용자 계정 파일 경로
        _users (dict)    : 메모리에 로드된 사용자 딕셔너리
    """

    def __init__(self, users_file: Path = USERS_FILE):
        self.users_file = users_file
        self._users: dict = {}
        self._load_users()

    def _load_users(self) -> None:
        """users.json 을 읽어 메모리(_users)에 로드한다."""
        if not self.users_file.exists():
            logger.warning("users.json 없음 — 모든 인증 거부")
            return
        with open(self.users_file, encoding="utf-8") as f:
            self._users = json.load(f)
        logger.info("사용자 계정 로드: %s", list(self._users.keys()))

    def reload(self) -> None:
        """런타임에 사용자 목록을 다시 로드한다. 계정 변경 시 재시작 없이 반영 가능."""
        self._load_users()

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────

    def _verify_password(self, password: bytes | str, salt_hex: str, hash_hex: str) -> bool:
        """
        입력된 비밀번호를 PBKDF2-SHA256 으로 해싱하여 저장된 해시와 비교한다.

        Args:
            password : 클라이언트가 제출한 비밀번호 (str 또는 bytes)
            salt_hex : users.json 의 salt 필드 (hex 문자열)
            hash_hex : users.json 의 hash 필드 (hex 문자열)

        Returns:
            bool: 일치하면 True, 불일치하면 False
        """
        if isinstance(password, str):
            pwd = password.encode()
        else:
            pwd = password
        salt = bytes.fromhex(salt_hex)
        computed = hashlib.pbkdf2_hmac("sha256", pwd, salt, PBKDF2_ITERATIONS)
        return computed.hex() == hash_hex

    # ── asyncua 인터페이스 ─────────────────────────────────────────────────

    def get_user(self, _iserver, username=None, password=None, certificate=None):
        """
        asyncua 가 세션 활성화 시 호출하는 인증 콜백.

        반환값:
          - truthy (dict) : 인증 성공 → 세션 허용
          - None          : 인증 실패 → asyncua 가 BadUserAccessDenied 반환

        호출 시나리오:
          username/password 인증 : username != None, password != None
          X.509 사용자 토큰 인증 : username == None, certificate != None
          익명 접근              : 모두 None → 거부

        Args:
            _iserver    : asyncua 내부 서버 인스턴스 (사용하지 않음)
            username    : 사용자 이름 (str 또는 None)
            password    : 비밀번호 (bytes 또는 None)
            certificate : X.509 인증서 객체 또는 None
        """

        # ── 1. 사용자 이름 + 비밀번호 인증 ──────────────────────────────
        if username is not None:
            user_data = self._users.get(username)
            if user_data is None:
                # 존재하지 않는 계정
                logger.warning("[AUTH] 존재하지 않는 계정: '%s'", username)
                return None

            if not self._verify_password(password or b"", user_data["salt"], user_data["hash"]):
                # 비밀번호 불일치
                logger.warning("[AUTH] 비밀번호 불일치: '%s'", username)
                return None

            logger.info("[AUTH] 사용자 이름/비밀번호 인증 성공: '%s' (role=%s)",
                        username, user_data.get("role", "user"))
            return {"name": username, "role": user_data.get("role", "user")}

        # ── 2. X.509 인증서 사용자 토큰 인증 ────────────────────────────
        if certificate is not None:
            # asyncua 가 X509IdentityToken 의 서명 검증 및 TrustStore 확인을 먼저 수행.
            # 이 시점에 certificate 가 전달되었다면 채널 수준 검증은 이미 통과한 것.
            # 추가 검증이 필요하다면 여기서 CN, SAN 등을 확인할 수 있음.
            try:
                cn = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
                cn_str = cn[0].value if cn else "unknown"
            except Exception:
                cn_str = "unknown"
            logger.info("[AUTH] 인증서 인증 성공: CN=%s", cn_str)
            return {"name": cn_str, "role": "cert_user"}

        # ── 3. 익명 접근 → 거부 ───────────────────────────────────────────
        # username, certificate 모두 None 인 경우 (AnonymousIdentityToken)
        logger.warning("[AUTH] 익명 접근 시도 거부")
        return None
