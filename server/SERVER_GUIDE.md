# OPC UA 서버 가이드

DM400 장비용 OPC UA 서버. Python + [asyncua](https://github.com/FreeOpcUa/opcua-asyncio) 기반.

---

## 목차

1. [아키텍처](#1-아키텍처)
2. [파일 구성](#2-파일-구성)
3. [OPC UA Address Space](#3-opc-ua-address-space)
4. [TCP 메시지 프로토콜](#4-tcp-메시지-프로토콜)
5. [인증서](#5-인증서)
6. [사용자 인증](#6-사용자-인증)
7. [패키지 설치](#7-패키지-설치)
8. [개발 환경 실행](#8-개발-환경-실행)
9. [Windows 서비스 배포](#9-windows-서비스-배포)
10. [보안 정책](#10-보안-정책)

---

## 1. 아키텍처

```
┌─────────────────────────────────────────────────┐
│              asyncio 이벤트 루프                  │
│                                                 │
│  ┌──────────────────┐  ┌─────────────────────┐  │
│  │  OPC UA Server   │  │    TCP Server       │  │
│  │  asyncua (4840)  │  │  asyncio (9000)     │  │
│  │                  │  │                     │  │
│  │  Address Space   │←─│  handler.py         │  │
│  └──────────────────┘  └─────────────────────┘  │
│           ↑                       ↑             │
└───────────┼───────────────────────┼─────────────┘
            │                       │
    OPC UA 클라이언트            DM400 장비
    (UaExpert 등)            (TCP CSV 메시지)
```

---

## 2. 파일 구성

| 파일 | 역할 |
|------|------|
| `opc_server.py` | OPC UA 서버 초기화, Address Space 구성, 진입점 |
| `handler.py` | TCP 메시지 파싱, OPC UA 노드 업데이트 |
| `tcp_server.py` | asyncio TCP 서버 유틸리티 |
| `user_manager.py` | OPC UA 세션 인증 |
| `gen_certs.py` | Self-signed 인증서 생성 및 자동 유효성 관리 |
| `windows_service.py` | Windows 서비스 래퍼 (pywin32) |
| `requirements.txt` | Python 의존성 목록 |
| `users.json` | 사용자 계정 (PBKDF2-SHA256 해시) |
| `certs/` | 인증서·키 파일 |
| `pki/trusted/certs/` | 상호 신뢰 인증서 등록 디렉터리 |

---

## 3. OPC UA Address Space

```
Objects/
└── AMMachine
    ├── MachineIdentifier
    │   ├── Manufacturer  (String)
    │   ├── Model         (String)
    │   └── SerialNumber  (String)
    ├── MachineStatus
    │   ├── State         (Int32)
    │   ├── StateText     (String)
    │   ├── Progress      (Int32)
    │   ├── BuildJob      (String)
    │   ├── CurrentLayer  (Int32)
    │   ├── TotalLayers   (Int32)
    │   ├── RemainingTime (Int32)
    │   ├── TotalBuildTime(Int32)
    │   ├── StartTime     (Int32)
    │   └── EndTime       (Int32)
    └── Sensors
        ├── BuildPlatformZPosition (Float)
        ├── LevelTankZPosition     (Float)
        ├── BladeState             (Int32)
        ├── CollectBladeState      (Int32)
        ├── PrintBladeState        (Int32)
        ├── ResinTemp              (Float)
        ├── ResinLevel             (Float)
        ├── ResinLevelStablity     (Float)
        ├── VatPres                (Float)
        ├── UVLTemp                (Float)
        └── UVRTemp                (Float)
```

---

## 4. TCP 메시지 프로토콜

포트 **9000**, 줄바꿈(`\n`) 구분 CSV 형식.

### IDENTIFIER
```
IDENTIFIER,<제조사>,<모델>,<시리얼번호>
```
예: `IDENTIFIER,Carimatec,DM400,DM400-SN-000001`

### STATUS
```
STATUS,<state>,<state_text>,<progress>,<build_job>,<current_layer>,<total_layers>,<remaining_time>,<expected_time>,<start_time>,<end_time>
```
예: `STATUS,2,Printing,45,job_001.stl,120,265,3600,7200,1700000000,0`

### SENSORS
```
SENSORS,<platform_zpos>,<tank_zpos>,<blade_state>,<collectblade_state>,<printblade_state>,<resin_temp>,<resin_level>,<resin_levelstable>,<vat_pres>,<uv_ltemp>,<uv_rtemp>
```
예: `SENSORS,12.5,8.3,1,0,1,28.5,75.2,0.98,101.3,42.1,41.8`

---

## 5. 인증서

Self-signed 방식. CA 구조 없이 인증서 자체를 `pki/trusted/certs/` 에 등록하여 상호 신뢰.

### 자동 관리

서버 시작 시 `gen_certs.ensure_server_certs()` 가 자동으로 검사한다.

| 조건 | 동작 |
|------|------|
| 인증서 없음 | 자동 생성 |
| 현재 IP 불일치 | 삭제 후 재생성 |
| 현재 호스트명 불일치 | 삭제 후 재생성 |
| 만료 30일 미만 | 삭제 후 재생성 |
| 정상 | 통과 |

### 수동 생성

```bash
python gen_certs.py
```

### 생성 파일

```
certs/
├── server_cert.pem / .der
├── server_key.pem
├── client_cert.pem / .der / .p12
└── client_key.pem

pki/trusted/certs/
├── server_cert.pem
└── client_cert.pem
```

---

## 6. 사용자 인증

| 방식 | 설명 |
|------|------|
| 사용자명/비밀번호 | `users.json` PBKDF2-SHA256 해시 비교 |
| X.509 인증서 | `client_cert.p12` → UaExpert 가져오기 |
| 익명 | 항상 거부 |

기본 계정:

| 계정 | 비밀번호 |
|------|----------|
| admin | admin123 |
| operator | operator123 |

> 운영 환경에서는 반드시 변경할 것.

---

## 7. 패키지 설치

Python 3.10 이상 필요.

```powershell
pip install -r requirements.txt
```

Windows 서비스 사용 시 pywin32 초기화 (최초 1회, 관리자 PowerShell):

```powershell
python -m pywin32_postinstall -install
```

---

## 8. 개발 환경 실행

```bash
python opc_server.py
```

출력:
```
[인증서 검사] 서버 인증서 유효성 확인 중...
  [통과] 유효 (만료까지 NNN일)
OPC UA SERVER STARTED -> ENDPOINT:opc.tcp://0.0.0.0:4840/carimatec/dm400
TCP Server started on port 9000
```

종료: `Ctrl+C`

---

## 9. Windows 서비스 배포

관리자 권한 PowerShell 에서 실행.

### 서비스 등록 및 시작

```powershell
cd <project_root>\GitConn\OPCUA\server
python windows_service.py install
python windows_service.py start
```

부팅 시 자동 시작으로 변경:

```powershell
Set-Service -Name OpcUaServer -StartupType Automatic
```

### 서비스 관리

```powershell
python windows_service.py stop      # 중지
python windows_service.py restart   # 재시작
python windows_service.py remove    # 제거
python windows_service.py status    # 상태 확인
```

### 로그 확인

```powershell
Get-Content C:\carimatec\logs\opcua_service.log -Wait
```

### 서비스 종료 흐름

```
services.msc → 중지
  → SvcStop() : SERVICE_STOP_PENDING 보고
              → loop.call_soon_threadsafe(_cancel_all_tasks)
                → 모든 Task.cancel()
                  → CancelledError 전파
                    → TCP 서버 종료 (포트 9000)
                    → OPC UA 서버 종료 (포트 4840)
  → SvcDoRun() 반환 → 서비스 종료
```

---

## 10. 보안 정책

```python
server.set_security_policy([
    ua.SecurityPolicyType.NoSecurity,
    ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt
])
```

| 정책 | 암호화 | 권장 환경 |
|------|--------|-----------|
| NoSecurity | 없음 | 개발·테스트 |
| Basic256Sha256_SignAndEncrypt | AES-256 / SHA-256 | 운영 |

### UaExpert 연결

1. Add Server → `opc.tcp://<서버IP>:4840/carimatec/dm400`
2. Security Policy: `Basic256Sha256`, Mode: `SignAndEncrypt`
3. Authentication: Username 또는 Certificate (`certs/client_cert.p12`)
