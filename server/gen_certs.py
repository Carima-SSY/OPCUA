#!/usr/bin/env python3
"""
OPC UA 인증서 생성 스크립트 (gen_certs.py)

실행: python gen_certs.py

역할:
  OPC UA 통신 보안(TLS/암호화)에 필요한 인증서와 개인키를 생성한다.
  서버·클라이언트 모두 Self-signed 방식을 사용하며 CA(인증기관) 구조는 사용하지 않는다.

[인증서 방식 변경 이력]
  초기: CA 서명 방식 — CA 인증서가 서버/클라이언트 인증서에 모두 서명
        → CA cert 하나를 신뢰하면 체인으로 상대방 검증 가능
  변경: Self-signed 방식 — 각 인증서가 자신의 키로 직접 서명
        → CA 불필요, 상대방 인증서 자체를 pki/trusted/certs/ 에 직접 등록

[Self-signed 핵심 원칙]
  1. issuer_name == subject_name   (자기 자신이 발급자)
  2. .sign(자신의_key, SHA256)     (CA 키가 아닌 자신의 키로 서명)
  3. AuthorityKeyIdentifier        (자신의 공개키를 참조)

[IP 자동 감지]
  소켓 라우팅 트릭으로 현재 환경의 외부 통신 IP를 감지하여
  인증서 SAN(Subject Alternative Name) 에 자동 포함.
  → 다른 환경에서도 인증서 재생성만 하면 IP 불일치 오류 방지

생성 파일:
  certs/server_cert.pem              - 서버 Self-signed 인증서
  certs/server_cert.der              - 서버 인증서 DER 포맷 (바이너리)
  certs/server_key.pem               - 서버 개인키
  certs/client_cert.pem              - 클라이언트 Self-signed 인증서
  certs/client_cert.der              - 클라이언트 인증서 DER 포맷 (바이너리)
  certs/client_cert.p12              - 클라이언트 인증서+키 통합 (UaExpert X509 user token용)
  certs/client_key.pem               - 클라이언트 개인키
  pki/trusted/certs/server_cert.pem  - 서버가 클라이언트를 신뢰 / 클라이언트가 서버를 신뢰하기 위한 사본
  pki/trusted/certs/client_cert.pem  - 서버가 클라이언트 cert 를 직접 신뢰 등록
  users.json                         - 초기 사용자 계정 (PBKDF2-SHA256 해시 저장)
"""

import os
import json
import hashlib
import datetime
import ipaddress
import shutil
import socket
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12

# 이 스크립트 파일 위치를 기준으로 경로 설정
BASE_DIR = Path(__file__).parent
CERTS_DIR = BASE_DIR / "certs"
PKI_TRUSTED_DIR = BASE_DIR / "pki" / "trusted" / "certs"

# OPC UA ApplicationUri — 인증서 SAN(URI) 과 opc_server.py 의 APPLICATION_URI 가 반드시 일치해야 함
SERVER_APP_URI = "urn:carimatec:opcua:server"
CLIENT_APP_URI = "urn:carimatec:opcua:client"


# ── 키/인증서 저장 유틸 ─────────────────────────────────────────────────────

def _gen_rsa_key() -> rsa.RSAPrivateKey:
    """RSA 2048비트 개인키를 생성한다. (공개 지수 65537 = 표준 권장값)"""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _save_key(key: rsa.RSAPrivateKey, path: Path) -> None:
    """개인키를 PEM 형식(암호화 없음)으로 파일에 저장한다."""
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    print(f"  [저장] {path}")


def _save_cert_pem(cert: x509.Certificate, path: Path) -> None:
    """인증서를 PEM 형식(Base64 텍스트)으로 파일에 저장한다."""
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"  [저장] {path}")


def _save_cert_der(cert: x509.Certificate, path: Path) -> None:
    """
    인증서를 DER 형식(바이너리)으로 파일에 저장한다.
    일부 OPC UA 클라이언트(UaExpert 등)는 DER 형식을 요구하므로 함께 생성.
    """
    path.write_bytes(cert.public_bytes(serialization.Encoding.DER))
    print(f"  [저장] {path}")


# ── Self-signed 인증서 생성 ──────────────────────────────────────────────────
#
# [변경] 기존 generate_ca() + generate_endpoint_cert() 구조에서
#        단일 generate_self_signed_cert() 로 통합.
#
# CA 구조 제거 이유:
#   - CA 인증서/키 파일 관리 부담 제거 (유출 시 전체 신뢰 체계 붕괴 위험)
#   - 소규모 폐쇄망 환경에서는 Self-signed 로도 충분한 보안 확보 가능
#   - 신뢰 등록이 단순: 상대방 cert 파일 자체를 trusted 폴더에 복사하면 됨
#
# Self-signed 동작 원리:
#   일반 CA 서명:   개인키(cert용) 생성 → CSR → CA 가 CA키로 서명 → cert 발급
#   Self-signed:    개인키 생성 → subject == issuer 로 설정 → 자신의 키로 직접 서명

def generate_self_signed_cert(
    common_name: str,
    app_uri: str,
    dns_names: list[str] | None = None,
    ip_list: list[str] | None = None,
    is_client: bool = False,
    validity_days: int = 825,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """
    Self-signed 인증서를 생성한다 (서버/클라이언트 공용).

    Args:
        common_name   : 인증서 CN 필드 (예: "Carimatec OPC UA Server")
        app_uri       : OPC UA ApplicationUri (SAN URI 필드에 포함)
        dns_names     : SAN 에 추가할 DNS 이름 목록 (예: ["localhost", "hostname"])
        ip_list       : SAN 에 추가할 IP 주소 목록 (예: ["127.0.0.1", "192.168.1.10"])
        is_client     : True 면 CLIENT_AUTH 만, False(서버)면 SERVER_AUTH+CLIENT_AUTH
        validity_days : 인증서 유효 기간 (일). 기본 825일 (브라우저 최대 허용치)

    Returns:
        (RSAPrivateKey, Certificate) 튜플
    """
    key = _gen_rsa_key()

    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "KR"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Carimatec"),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])

    # SAN(Subject Alternative Name) 구성
    # OPC UA 스택은 ApplicationUri 와 SAN URI 를 비교하므로 반드시 포함해야 함
    san: list[x509.GeneralName] = [x509.UniformResourceIdentifier(app_uri)]
    for d in (dns_names or []):
        san.append(x509.DNSName(d))
    for ip in (ip_list or []):
        # ipaddress.ip_address() 로 파싱하여 IPv4/IPv6 자동 판별
        san.append(x509.IPAddress(ipaddress.ip_address(ip)))

    # EKU(Extended Key Usage) 설정
    # 서버: SERVER_AUTH + CLIENT_AUTH (OPC UA 양방향 인증 요구사항)
    # 클라이언트: CLIENT_AUTH 만
    eku_oids = [ExtendedKeyUsageOID.CLIENT_AUTH]
    if not is_client:
        eku_oids.insert(0, ExtendedKeyUsageOID.SERVER_AUTH)

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        # [Self-signed 핵심] issuer == subject → 자기 자신이 발급자
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=validity_days))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(
            x509.KeyUsage(
                # OPC UA 보안 채널에 필요한 키 사용 목적 설정
                digital_signature=True,   # 서명 생성
                content_commitment=True,  # 부인 방지 (NonRepudiation)
                key_encipherment=True,    # 키 암호화 (TLS 키 교환)
                data_encipherment=True,   # 데이터 직접 암호화
                key_agreement=False,
                key_cert_sign=False,      # CA 가 아니므로 False
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage(eku_oids), critical=False)
        # ca=False: 이 인증서는 다른 인증서에 서명할 수 없음 (End-Entity cert)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        # SubjectKeyIdentifier: 이 인증서의 공개키 식별자
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        # [Self-signed 핵심] CA 공개키 대신 자신의 공개키를 AuthorityKeyIdentifier 로 설정
        # (발급자 == 자신이므로 authority = 자신의 공개키)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
            critical=False,
        )
        # [Self-signed 핵심] CA 키가 아닌 자신의 키로 직접 서명 → Self-signed 완성
        .sign(key, hashes.SHA256())
    )
    return key, cert


# ── 사용자 계정 생성 ────────────────────────────────────────────────────────

def _hash_password(password: str) -> dict:
    """
    비밀번호를 PBKDF2-SHA256 으로 해싱하여 salt, hash 를 반환한다.

    - salt: 32바이트 랜덤 값 (레인보우 테이블 공격 방지)
    - 반복 횟수 200,000회 (user_manager.py 의 PBKDF2_ITERATIONS 와 일치 필수)
    """
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return {"salt": salt.hex(), "hash": key.hex()}


def generate_users(users_path: Path) -> None:
    """
    초기 사용자 계정(users.json)을 생성한다.
    파일이 이미 존재하면 덮어쓰지 않는다 (기존 계정 보호).

    기본 계정:
      admin    / admin123  (role: admin)
      operator / operator123 (role: operator)
    ※ 운영 환경에서는 반드시 비밀번호를 변경할 것.
    """
    if users_path.exists():
        print(f"\n[건너뜀] users.json 이미 존재: {users_path}")
        return

    default_users = {
        "admin":    {"role": "admin",    **_hash_password("admin123")},
        "operator": {"role": "operator", **_hash_password("operator123")},
    }
    users_path.write_text(json.dumps(default_users, indent=2), encoding="utf-8")
    print(f"\n[저장] {users_path}")
    print("  기본 계정:")
    print("    admin    / admin123")
    print("    operator / operator123")
    print("  ※ 운영 전 반드시 비밀번호를 변경하세요.")


# ── 현재 환경 IP 자동 감지 ───────────────────────────────────────────────────

def _get_local_ip() -> str | None:
    """
    현재 환경에서 외부 통신에 사용하는 네트워크 인터페이스 IP 를 반환한다.

    원리:
      UDP 소켓으로 8.8.8.8:80 에 connect() 를 호출하면
      OS 라우팅 테이블을 조회하여 실제 사용할 인터페이스를 선택한다.
      connect() 는 UDP 이므로 실제 패킷이 전송되지 않으며,
      getsockname() 으로 선택된 로컬 IP 를 읽어 반환한다.

    Returns:
      str: 로컬 IP 주소 (예: "192.168.1.10")
      None: 네트워크 인터페이스 없거나 오류 발생 시
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


# ── 인증서 자동 관리 ─────────────────────────────────────────────────────────

# 서버 시작 전 인증서 유효성 검사 시 만료까지 이 일수 미만이면 재생성
_RENEW_BEFORE_DAYS = 30

# 서버가 직접 로드하는 필수 파일 목록
_SERVER_CERT_FILES = [
    CERTS_DIR / "server_cert.pem",
    CERTS_DIR / "server_key.pem",
]

# gen_certs.main() 이 생성하고 관리하는 전체 파일 목록
# (regenerate 시 이 파일들만 삭제하여 수동 등록 파일은 보존)
_MANAGED_FILES = [
    CERTS_DIR / "server_cert.pem",
    CERTS_DIR / "server_cert.der",
    CERTS_DIR / "server_key.pem",
    CERTS_DIR / "client_cert.pem",
    CERTS_DIR / "client_cert.der",
    CERTS_DIR / "client_cert.p12",
    CERTS_DIR / "client_key.pem",
    PKI_TRUSTED_DIR / "server_cert.pem",
    PKI_TRUSTED_DIR / "client_cert.pem",
]


def _load_cert(cert_path: Path) -> x509.Certificate | None:
    """PEM 파일에서 인증서 객체를 로드한다. 파싱 실패 시 None 반환."""
    try:
        return x509.load_pem_x509_certificate(cert_path.read_bytes())
    except Exception:
        return None


def _cert_not_after(cert: x509.Certificate) -> datetime.datetime:
    """
    인증서 만료 시각을 timezone-aware datetime 으로 반환한다.

    cryptography 42.0.0+ 에서 not_valid_after 가 deprecated 되어
    not_valid_after_utc 로 대체되었다. 두 버전 모두 지원한다.
    """
    try:
        return cert.not_valid_after_utc  # cryptography >= 42.0.0
    except AttributeError:
        # cryptography < 42.0.0: naive datetime 을 UTC 로 변환
        return cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)


def _check_cert_validity(cert_path: Path) -> tuple[bool, str]:
    """
    서버 인증서 파일의 유효성을 검사한다.

    검사 항목 (순서대로):
      1. 파일 파싱 가능 여부
      2. 만료 여부 (_RENEW_BEFORE_DAYS 일 여유 포함)
      3. 현재 IP 가 SAN IP 목록에 포함되어 있는지
      4. 현재 호스트명이 SAN DNS 목록에 포함되어 있는지

    Args:
        cert_path: 검사할 서버 인증서 PEM 파일 경로

    Returns:
        (is_valid, reason)
        is_valid: True 면 유효, False 면 재생성 필요
        reason  : 사람이 읽기 쉬운 판정 이유 문자열
    """
    # ── 1. 파싱 ─────────────────────────────────────────────────────────────
    cert = _load_cert(cert_path)
    if cert is None:
        return False, "인증서 파일을 파싱할 수 없음 (손상되었거나 형식 오류)"

    # ── 2. 만료 검사 ─────────────────────────────────────────────────────────
    now = datetime.datetime.now(datetime.timezone.utc)
    not_after = _cert_not_after(cert)
    remaining = not_after - now

    if remaining.total_seconds() <= 0:
        return False, f"인증서 만료됨 ({not_after.date()} 까지)"

    if remaining.days < _RENEW_BEFORE_DAYS:
        return False, f"인증서 만료 {remaining.days}일 전 — 갱신 기준({_RENEW_BEFORE_DAYS}일) 미만"

    # ── 3. 현재 IP 검사 ───────────────────────────────────────────────────────
    local_ip = _get_local_ip()
    if local_ip:
        try:
            san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            san_ips = [str(ip) for ip in san_ext.value.get_values_for_type(x509.IPAddress)]
        except Exception:
            return False, "SAN(Subject Alternative Name) 확장을 읽을 수 없음"

        if local_ip not in san_ips:
            return False, (
                f"현재 IP({local_ip})가 인증서 SAN에 없음 "
                f"(등록된 IP: {san_ips})"
            )

    # ── 4. 현재 호스트명 검사 ─────────────────────────────────────────────────
    hostname = socket.gethostname()
    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        san_dns = san_ext.value.get_values_for_type(x509.DNSName)
    except Exception:
        return False, "SAN DNS 목록을 읽을 수 없음"

    if hostname not in san_dns:
        return False, (
            f"현재 호스트명({hostname})이 인증서 SAN에 없음 "
            f"(등록된 DNS: {san_dns})"
        )

    return True, f"유효 (만료까지 {remaining.days}일)"


def _cleanup_certs() -> None:
    """
    gen_certs.main() 이 생성한 관리 대상 파일을 모두 삭제한다.

    수동으로 추가한 pki/trusted/certs/ 내 다른 클라이언트 인증서는
    _MANAGED_FILES 목록에 없으므로 삭제하지 않는다.
    """
    for path in _MANAGED_FILES:
        if path.exists():
            path.unlink()
            print(f"  [삭제] {path}")


def ensure_server_certs() -> None:
    """
    서버 시작 전 인증서 유효성을 확인하고, 없거나 유효하지 않으면 재생성한다.

    opc_server.py 의 main() 에서 서버 초기화 전에 호출한다.

    처리 흐름:
      필수 파일 존재 여부 확인
        → 없음: 즉시 생성
        → 있음: _check_cert_validity() 로 유효성 검사
              → 유효하지 않음: 기존 파일 삭제 후 재생성
              → 유효: 통과
    """
    print("[인증서 검사] 서버 인증서 유효성 확인 중...")

    # 필수 파일(cert + key) 중 하나라도 없으면 전체 재생성
    missing = [f.name for f in _SERVER_CERT_FILES if not f.exists()]
    if missing:
        print(f"  필수 파일 없음: {missing} → 인증서를 새로 생성합니다.")
        main()
        return

    # 인증서 유효성 검사
    is_valid, reason = _check_cert_validity(CERTS_DIR / "server_cert.pem")
    if not is_valid:
        print(f"  [재생성 필요] {reason}")
        print("  기존 인증서를 삭제하고 재생성합니다...")
        _cleanup_certs()
        main()
    else:
        print(f"  [통과] {reason}")


# ── 메인 실행 ────────────────────────────────────────────────────────────────

def main():
    """
    인증서 생성 전체 흐름:
      1) 서버 Self-signed 인증서 생성 (PEM + DER)
         → pki/trusted/certs/ 에 복사 (클라이언트 신뢰 등록용)
      2) 클라이언트 Self-signed 인증서 생성 (PEM + DER + PKCS#12)
         → pki/trusted/certs/ 에 복사 (서버 신뢰 등록용)
      3) 초기 사용자 계정 생성 (users.json)
    """
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    PKI_TRUSTED_DIR.mkdir(parents=True, exist_ok=True)

    # 현재 환경 IP 감지 — 감지된 IP 를 127.0.0.1 과 함께 SAN 에 포함
    # 이를 통해 클라이언트가 실제 IP 로 접속해도 인증서 SAN 불일치 오류 방지
    local_ip = _get_local_ip()
    ip_list = list(dict.fromkeys(filter(None, ["127.0.0.1", local_ip])))
    if local_ip:
        print(f"  로컬 IP 자동 감지: {local_ip}")
    else:
        print("  로컬 IP 감지 실패 — 127.0.0.1만 포함합니다.")

    # ── 1. 서버 인증서 ──────────────────────────────────────────────────────
    print("=" * 50)
    print("1) 서버 인증서 생성  [Self-signed]")
    print("=" * 50)
    hostname = socket.gethostname()
    # 호스트명과 localhost 를 모두 DNS SAN 에 포함, 중복 제거
    server_dns = list(dict.fromkeys(["localhost", hostname]))
    print(f"  호스트명 자동 감지: {hostname}")
    print(f"  SAN IP 목록: {ip_list}")
    srv_key, srv_cert = generate_self_signed_cert(
        common_name="Carimatec OPC UA Server",
        app_uri=SERVER_APP_URI,
        dns_names=server_dns,
        ip_list=ip_list,
        is_client=False,
    )
    _save_key(srv_key, CERTS_DIR / "server_key.pem")
    _save_cert_pem(srv_cert, CERTS_DIR / "server_cert.pem")
    _save_cert_der(srv_cert, CERTS_DIR / "server_cert.der")

    # Self-signed 서버 인증서는 CA 체인이 없으므로 cert 자체가 신뢰 앵커.
    # 클라이언트(UaExpert 등)가 서버를 신뢰하려면 이 파일을 직접 신뢰 목록에 추가해야 함.
    shutil.copy(CERTS_DIR / "server_cert.pem", PKI_TRUSTED_DIR / "server_cert.pem")
    print(f"  [복사] {PKI_TRUSTED_DIR / 'server_cert.pem'}  <- 클라이언트 신뢰 등록용")

    # ── 2. 클라이언트 인증서 ────────────────────────────────────────────────
    print("\n" + "=" * 50)
    print("2) 클라이언트 인증서 생성  [Self-signed]")
    print("=" * 50)
    cli_key, cli_cert = generate_self_signed_cert(
        common_name="Carimatec OPC UA Client",
        app_uri=CLIENT_APP_URI,
        dns_names=["localhost"],
        ip_list=ip_list,
        is_client=True,
    )
    _save_key(cli_key, CERTS_DIR / "client_key.pem")
    _save_cert_pem(cli_cert, CERTS_DIR / "client_cert.pem")
    _save_cert_der(cli_cert, CERTS_DIR / "client_cert.der")

    # 서버가 클라이언트를 신뢰하려면 클라이언트 cert 를 서버 PKI trusted 폴더에 등록해야 함.
    shutil.copy(CERTS_DIR / "client_cert.pem", PKI_TRUSTED_DIR / "client_cert.pem")
    print(f"  [복사] {PKI_TRUSTED_DIR / 'client_cert.pem'}  <- 서버 신뢰 등록용")

    # PKCS#12: cert + key 를 하나의 파일로 묶은 포맷.
    # UaExpert 에서 X509 UserIdentityToken 으로 사용할 때 .p12 파일을 가져온다.
    p12_data = pkcs12.serialize_key_and_certificates(
        name=b"Carimatec OPC UA Client",
        key=cli_key,
        cert=cli_cert,
        cas=None,                                   # CA 체인 없음 (Self-signed)
        encryption_algorithm=serialization.NoEncryption(),
    )
    p12_path = CERTS_DIR / "client_cert.p12"
    p12_path.write_bytes(p12_data)
    print(f"  [저장] {p12_path}  <- UaExpert X509 user token용")

    # ── 3. 사용자 계정 ──────────────────────────────────────────────────────
    print("\n" + "=" * 50)
    print("3) 사용자 계정 초기화")
    print("=" * 50)
    generate_users(BASE_DIR / "users.json")

    # ── 완료 요약 ────────────────────────────────────────────────────────────
    print("\n" + "=" * 50)
    print("완료! 생성된 파일 목록:")
    print("=" * 50)
    for f in sorted(CERTS_DIR.iterdir()):
        print(f"  {f}")
    print(f"  {PKI_TRUSTED_DIR / 'server_cert.pem'}")
    print(f"  {PKI_TRUSTED_DIR / 'client_cert.pem'}")
    print(f"  {BASE_DIR / 'users.json'}")
    print("\n클라이언트에 배포할 파일:")
    print("  certs/server_cert.pem <- 서버 Self-signed 인증서 (클라이언트 신뢰 등록)")
    print("  certs/server_cert.der <- 서버 인증서 DER 포맷 (바이너리, 일부 클라이언트 호환용)")
    print("  certs/client_cert.pem <- 클라이언트 인증서")
    print("  certs/client_cert.der <- 클라이언트 인증서 DER 포맷")
    print("  certs/client_key.pem  <- 클라이언트 개인키")


if __name__ == "__main__":
    main()
