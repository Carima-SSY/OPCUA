"""
OPC UA 클라이언트 핵심 클래스 (opc/client.py)

역할:
    OPC UA 서버와의 연결 · 보안 설정 · 사용자 인증 · 노드 탐색 · 구독 생성을
    하나의 클래스(OPCClient)로 캡슐화한다.

OPC UA 연결 3단계:
    1. OpenSecureChannel  — 채널 협상 및 암호화 설정 (SecurityMode)
    2. CreateSession      — 세션 오브젝트 생성
    3. ActivateSession    — 사용자 ID 토큰으로 인증 (AuthMode)

채널 보안(SecurityMode) × 사용자 인증(AuthMode) 조합:
    ┌───────────────────────┬────────────────────────────────────────┐
    │ NoSecurity + Anonymous │ 채널 평문, 인증 없음 → 서버가 거부함  │
    │ NoSecurity + Username  │ 평문 채널, 자격증명 전송 (테스트용)   │
    │ SignAndEncrypt + User  │ 암호화 채널 + 자격증명 (운영 권장)    │
    │ SignAndEncrypt + Cert  │ 암호화 채널 + X509 토큰 (최고 보안)   │
    └───────────────────────┴────────────────────────────────────────┘

의존성:
    asyncua>=1.0.0, cryptography>=41.0.0
"""

import asyncio
import logging
from pathlib import Path

from asyncua import Client, Node, ua
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
from cryptography import x509 as crypto_x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from opc.config import (
    CLIENT_APP_URI,
    DEFAULT_CLIENT_CERT,
    DEFAULT_CLIENT_KEY,
    DEFAULT_SERVER_CERT,
    SERVER_ENDPOINT,
    SUBSCRIPTION_PERIOD_MS,
    AuthMode,
    SecurityMode,
)
from opc.handler import SubscriptionHandler

logger = logging.getLogger(__name__)

# 노드 트리 콘솔 출력에 사용하는 구분선 길이
_SEP = "─" * 72


class OPCClient:
    """
    DM400 OPC UA 서버 클라이언트 래퍼.

    채널 암호화(SecurityMode)와 사용자 인증(AuthMode)을 독립적으로 설정할 수 있다.
    asyncua.Client 를 내부적으로 보유하며, 연결·해제 시 리소스를 올바르게 정리한다.

    Attributes:
        endpoint      : 서버 엔드포인트 URL
        auth_mode     : 사용자 인증 방식 (AuthMode)
        security_mode : 채널 암호화 정책 (SecurityMode)
        username      : Username 인증 시 사용자 이름
        password      : Username 인증 시 비밀번호
        client_cert   : 클라이언트 인증서 PEM 경로
        client_key    : 클라이언트 개인키 PEM 경로
        server_cert   : 서버 인증서 PEM 경로 (채널 신뢰 앵커)

    Context manager 사용 예:
        async with OPCClient(
            auth_mode=AuthMode.USERNAME,
            security_mode=SecurityMode.SIGN_ENCRYPT,
            username="admin",
            password="admin123",
        ) as client:
            await client.browse_and_subscribe()
            await client.run_forever()
    """

    def __init__(
        self,
        endpoint:      str          = SERVER_ENDPOINT,
        auth_mode:     AuthMode     = AuthMode.ANONYMOUS,
        security_mode: SecurityMode = SecurityMode.NONE,
        username:      str | None   = None,
        password:      str | None   = None,
        client_cert:   Path         = DEFAULT_CLIENT_CERT,
        client_key:    Path         = DEFAULT_CLIENT_KEY,
        server_cert:   Path         = DEFAULT_SERVER_CERT,
    ):
        self.endpoint      = endpoint
        self.auth_mode     = auth_mode
        self.security_mode = security_mode
        self.username      = username
        self.password      = password
        self.client_cert   = Path(client_cert)
        self.client_key    = Path(client_key)
        self.server_cert   = Path(server_cert)

        # asyncua 내부 클라이언트 — connect() 호출 전까지 None
        self._client: Client | None = None
        # 생성된 구독 객체 — disconnect() 에서 삭제한다
        self._subscription = None

    # ── context manager ───────────────────────────────────────────────────────

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *_):
        await self.disconnect()

    # ── 연결 / 해제 ───────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        채널 보안 설정 → 사용자 인증 토큰 설정 → 서버 연결 순서로 수행한다.

        asyncua.Client.connect() 는 내부적으로 아래 순서를 실행한다:
          1. OpenSecureChannel — _setup_channel_security() 의 설정이 적용됨
          2. CreateSession     — 세션 오브젝트 생성
          3. ActivateSession   — _setup_user_identity() 의 토큰으로 인증
        """
        self._client = Client(url=self.endpoint)
        # ApplicationUri 는 인증서의 SAN(URI) 과 반드시 일치해야 한다
        self._client.application_uri = CLIENT_APP_URI

        await self._setup_channel_security()
        self._setup_user_identity()

        await self._client.connect()
        logger.info(
            "[CONNECTED] %s | auth=%s | security=%s",
            self.endpoint, self.auth_mode.value, self.security_mode.value,
        )

    async def disconnect(self) -> None:
        """구독을 먼저 삭제한 뒤 서버 연결을 닫는다."""
        if self._subscription:
            try:
                await self._subscription.delete()
                logger.info("[UNSUBSCRIBED]")
            except Exception as exc:
                # 이미 연결이 끊긴 경우 등 무시 가능한 오류
                logger.debug("구독 해제 중 오류 무시: %s", exc)
        if self._client:
            await self._client.disconnect()
            logger.info("[DISCONNECTED]")

    # ── 채널 보안 설정 ────────────────────────────────────────────────────────

    async def _setup_channel_security(self) -> None:
        """
        OpenSecureChannel 단계의 메시지 보안 정책을 설정한다.

        NoSecurity:
            암호화 없음. 인증서 파일 불필요.
            개발·내부망 환경 전용.

        Basic256Sha256 / SignAndEncrypt:
            client_cert.pem + client_key.pem 으로 채널을 암호화하고,
            server_cert.pem 을 신뢰 앵커로 서버를 검증한다.
            운영 환경에서 사용할 것.
        """
        if self.security_mode == SecurityMode.SIGN_ENCRYPT:
            self._check_cert_files()
            await self._client.set_security(
                SecurityPolicyBasic256Sha256,
                certificate=str(self.client_cert),
                private_key=str(self.client_key),
                server_certificate=str(self.server_cert),
                mode=ua.MessageSecurityMode.SignAndEncrypt,
            )
            logger.info("[SECURITY] Basic256Sha256 / SignAndEncrypt 활성화")
        else:
            logger.info("[SECURITY] NoSecurity (암호화 없음)")

    # ── 사용자 인증 토큰 설정 ─────────────────────────────────────────────────

    def _setup_user_identity(self) -> None:
        """
        ActivateSession 단계에서 서버로 전송할 사용자 ID 토큰을 설정한다.

        Anonymous:
            AnonymousIdentityToken 전송.
            이 서버의 OPCUserManager 는 Anonymous 를 거부하도록 구성되어 있다.

        Username/Password:
            UserNameIdentityToken 전송.
            서버가 PBKDF2-SHA256(200,000 회) 으로 비밀번호를 검증한다.
            기본 계정: admin/admin123, operator/operator123.

        X.509 Certificate:
            X509IdentityToken 전송.
            ① 클라이언트 인증서를 DER 바이트로 변환하여 토큰에 첨부한다.
            ② 개인키로 토큰 데이터에 서명하여 소유 증명을 한다.
            ③ 서버의 pki/trusted/certs/ 에 클라이언트 인증서가 등록되어 있어야 한다.
            SignAndEncrypt 채널과 함께 사용하는 것을 강력 권장한다.
        """
        if self.auth_mode == AuthMode.USERNAME:
            if not self.username:
                raise ValueError("Username 인증: username 이 필요합니다.")
            if not self.password:
                raise ValueError("Username 인증: password 가 필요합니다.")
            self._client.set_user(self.username)
            self._client.set_password(self.password)
            logger.info("[AUTH] Username/Password — user: %s", self.username)

        elif self.auth_mode == AuthMode.CERTIFICATE:
            self._check_cert_files()
            if self.security_mode == SecurityMode.NONE:
                logger.warning("[AUTH] X.509 토큰은 SignAndEncrypt 채널과 함께 사용을 권장합니다.")

            # PEM → DER 변환: asyncua 는 ActivateSession 시 DER 바이트를
            # X509IdentityToken.CertificateData 에 담아 서버로 전송한다.
            cert_der = crypto_x509.load_pem_x509_certificate(
                self.client_cert.read_bytes()
            ).public_bytes(serialization.Encoding.DER)

            # 개인키: asyncua 가 토큰 데이터에 RSA 서명을 생성할 때 사용한다.
            private_key = load_pem_private_key(self.client_key.read_bytes(), password=None)

            self._client.user_certificate = cert_der
            self._client.user_private_key  = private_key
            logger.info("[AUTH] X.509 Certificate — %s", self.client_cert.name)

        else:
            # AnonymousIdentityToken: 서버 OPCUserManager 에서 거부될 수 있음
            logger.info("[AUTH] Anonymous")

    # ── 인증서 파일 검증 ──────────────────────────────────────────────────────

    def _check_cert_files(self) -> None:
        """보안 설정에 필요한 인증서 파일이 모두 존재하는지 확인한다."""
        missing = [
            p for p in (self.client_cert, self.client_key, self.server_cert)
            if not p.exists()
        ]
        if missing:
            paths = "\n".join(f"  - {p}" for p in missing)
            raise FileNotFoundError(
                f"인증서 파일이 없습니다:\n{paths}\n\n"
                "서버 디렉터리에서 다음 명령을 실행하세요:\n"
                "  cd server && python gen_certs.py"
            )

    # ── 노드 탐색 ─────────────────────────────────────────────────────────────

    async def browse_all(self) -> list[Node]:
        """
        OPC UA Address Space 의 Objects 루트부터 전체 트리를 DFS 로 순회하고
        구독 가능한 Variable 노드 목록을 반환한다.

        방문한 NodeId 를 집합(visited)으로 추적하여 순환 참조를 방지한다.
        Object / Method 노드는 콘솔에 트리 형태로 출력되지만 반환 목록에는 포함되지 않는다.

        Returns:
            list[Node]: 발견된 모든 Variable 노드
        """
        root      = self._client.nodes.objects
        var_nodes: list[Node] = []
        visited:   set[str]   = set()

        print(f"\n{_SEP}\n  OPC UA 노드 트리 탐색\n{_SEP}")
        await self._browse_recursive(root, var_nodes, visited, depth=0)
        print(_SEP)
        logger.info("[BROWSE] Variable 노드 합계: %d 개", len(var_nodes))
        return var_nodes

    async def _browse_recursive(
        self,
        node:      Node,
        var_nodes: list[Node],
        visited:   set[str],
        depth:     int,
    ) -> None:
        """
        단일 노드를 처리하고 자식 노드를 재귀적으로 탐색한다.

        Args:
            node      : 현재 처리할 노드
            var_nodes : Variable 노드를 누적하는 리스트 (out)
            visited   : 이미 방문한 NodeId 집합 (순환 참조 방지)
            depth     : 현재 트리 깊이 (들여쓰기 계산용)
        """
        node_id_str = node.nodeid.to_string()
        if node_id_str in visited:
            return
        visited.add(node_id_str)

        try:
            browse_name = await node.read_browse_name()
            node_class  = await node.read_node_class()
        except Exception:
            return   # 읽기 권한 없거나 삭제된 노드 — 조용히 건너뜀

        indent    = "  " * depth
        cls_label = {
            ua.NodeClass.Object:   "Object  ",
            ua.NodeClass.Variable: "Variable",
            ua.NodeClass.Method:   "Method  ",
            ua.NodeClass.View:     "View    ",
        }.get(node_class, f"{node_class!s:8}")

        if node_class == ua.NodeClass.Variable:
            try:
                value = await node.read_value()
                print(
                    f"{indent}[{cls_label}] {browse_name.Name:<30} "
                    f"id={node_id_str:<42}  val={value!r}"
                )
                var_nodes.append(node)
            except Exception as exc:
                print(f"{indent}[{cls_label}] {browse_name.Name}  (값 읽기 오류: {exc})")
        else:
            print(f"{indent}[{cls_label}] {browse_name.Name}")

        # HierarchicalReferences 기준의 자식 노드를 재귀 탐색
        try:
            for child in await node.get_children():
                await self._browse_recursive(child, var_nodes, visited, depth + 1)
        except Exception:
            pass   # 브라우징 권한 없는 노드 — 건너뜀

    # ── 구독 생성 ─────────────────────────────────────────────────────────────

    async def subscribe(self, nodes: list[Node]) -> None:
        """
        Variable 노드 목록에 대한 DataChange 구독을 생성한다.

        서버는 SUBSCRIPTION_PERIOD_MS 주기로 변경된 값을 클라이언트에 푸시한다.
        수신 콜백은 SubscriptionHandler.datachange_notification 에서 처리된다.

        Args:
            nodes : 구독할 Variable 노드 목록 (browse_all() 의 반환값)
        """
        if not nodes:
            logger.warning("[SUBSCRIPTION] 구독할 Variable 노드가 없습니다.")
            return
        self._subscription = await self._client.create_subscription(
            period=SUBSCRIPTION_PERIOD_MS,
            handler=SubscriptionHandler(),
        )
        await self._subscription.subscribe_data_change(nodes)
        logger.info(
            "[SUBSCRIPTION] %d 개 노드 구독 완료 (갱신 주기: %d ms)",
            len(nodes), SUBSCRIPTION_PERIOD_MS,
        )

    async def browse_and_subscribe(self) -> None:
        """노드 탐색 후 발견된 모든 Variable 노드를 구독하는 편의 메서드."""
        nodes = await self.browse_all()
        await self.subscribe(nodes)

    # ── 수신 루프 ─────────────────────────────────────────────────────────────

    async def run_forever(self) -> None:
        """
        Ctrl+C (KeyboardInterrupt) 또는 CancelledError 까지 구독 데이터를 수신한다.

        구독이 활성 상태인 동안 asyncua 가 내부적으로 데이터를 수신하므로
        이 메서드는 단순히 이벤트 루프를 유지하는 역할만 한다.
        """
        logger.info("[RUNNING] 데이터 수신 중 — 종료: Ctrl+C")
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("[STOPPING]")
