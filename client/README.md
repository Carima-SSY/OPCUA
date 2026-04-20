# DM400 OPC UA 클라이언트

DM400 OPC UA 서버(`server/`)에 연결하여 노드를 탐색·구독하는 Python 클라이언트.  
**터미널(CLI) 모드**와 **웹 대시보드(Web UI) 모드** 두 가지 방식으로 실행할 수 있다.

---

## 디렉터리 구조

```
client/
├── opc_client.py          # CLI 모드 진입점
├── requirements.txt       # Python 패키지 의존성
│
├── opc/                   # OPC UA 핵심 패키지
│   ├── __init__.py        # 패키지 공개 API
│   ├── config.py          # 서버 접속 상수 + Enum
│   ├── handler.py         # 구독 이벤트 콜백 핸들러
│   ├── client.py          # 연결·탐색·구독 핵심 클래스
│   └── cli.py             # 터미널 대화형 설정 및 실행
│
└── web/                   # FastAPI 웹 UI 패키지
    ├── __init__.py        # sys.path 설정 (opc/ 패키지 접근)
    ├── app.py             # FastAPI 앱 및 WebSocket 엔드포인트
    ├── opc_state.py       # OPC UA 전역 상태 + WS 브로드캐스트 핸들러
    ├── api/
    │   ├── __init__.py
    │   └── routes.py      # REST API 엔드포인트 (/api/*)
    ├── ws/
    │   ├── __init__.py
    │   └── manager.py     # WebSocket 연결 관리자
    └── static/
        ├── index.html     # 대시보드 단일 페이지
        ├── css/style.css  # 다크 테마 스타일
        └── js/app.js      # 프론트엔드 로직 (Vanilla JS)
```

---

## 사전 준비

### 1. 패키지 설치

```bash
cd client
pip install -r requirements.txt
```

`requirements.txt`:
```
asyncua>=1.0.0
cryptography>=41.0.0
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
```

### 2. 인증서 생성 (보안 채널 / X.509 인증 사용 시)

```bash
cd server
python gen_certs.py
```

`server/certs/` 에 다음 파일이 생성된다:

| 파일 | 용도 |
|------|------|
| `server_cert.pem` | 서버 인증서 (클라이언트의 신뢰 앵커) |
| `server_key.pem`  | 서버 개인키 |
| `client_cert.pem` | 클라이언트 인증서 (채널/X.509 인증용) |
| `client_key.pem`  | 클라이언트 개인키 |
| `client_cert.p12` | 클라이언트 인증서 PKCS#12 묶음 |

---

## 실행 방법

### CLI 모드

```bash
cd client
python opc_client.py
```

터미널에서 순서대로 입력:
1. 서버 엔드포인트 (기본값: `opc.tcp://localhost:4840/carimatec/dm400`)
2. 인증 방식 선택 (1=Anonymous / 2=Username·Password / 3=X.509)
3. 채널 보안 정책 선택 (1=NoSecurity / 2=Basic256Sha256/SignAndEncrypt)
4. 인증 방식에 따라 사용자 이름·비밀번호 또는 인증서 경로 입력

연결 성공 후 노드 트리가 콘솔에 출력되고, `Ctrl+C` 입력까지 데이터 변경 로그가 출력된다.

### Web UI 모드

```bash
cd client
uvicorn web.app:app --host 0.0.0.0 --port 8000 --reload
```

브라우저에서 `http://localhost:8000` 접속.

좌측 패널에서 연결 설정을 입력하고 **연결** 버튼을 클릭하면 노드 트리와 실시간 값 테이블이 표시된다.

---

## 인증 방식 및 보안 조합

OPC UA 는 **채널 보안(SecurityMode)** 과 **사용자 인증(AuthMode)** 을 독립적으로 설정한다.

| SecurityMode | AuthMode | 설명 |
|---|---|---|
| NoSecurity | Anonymous | 평문·인증 없음. 이 서버에서 거부됨 |
| NoSecurity | Username/Password | 평문 채널, 자격증명 전송. 개발·테스트용 |
| SignAndEncrypt | Username/Password | AES-256 암호화 채널 + 자격증명. 운영 권장 |
| SignAndEncrypt | X.509 Certificate | 암호화 채널 + 인증서 서명. 최고 보안 수준 |

### 기본 계정

| 사용자 이름 | 비밀번호 | 권한 |
|---|---|---|
| `admin`    | `admin123`    | 관리자 |
| `operator` | `operator123` | 운영자 |

### X.509 인증서 사용 시 추가 설정

서버의 `pki/trusted/certs/` 디렉터리에 `client_cert.pem` 을 복사해야 서버가 해당 클라이언트를 신뢰한다.

---

## REST API 레퍼런스

Web UI 모드에서 `http://localhost:8000/docs` 에 접속하면 Swagger UI 로 전체 API를 확인할 수 있다.

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/connect` | OPC UA 서버 연결 및 노드 트리 반환 |
| `POST` | `/api/disconnect` | 연결 해제 |
| `GET`  | `/api/status` | 연결 상태 및 구독 노드 수 |
| `GET`  | `/api/nodes` | 노드 트리 (연결 후 유효) |
| `GET`  | `/api/values` | 모든 Variable 노드 현재 값 |
| `GET`  | `/api/defaults` | 프론트엔드 폼 기본값 |
| `GET`  | `/ws` | WebSocket — 실시간 데이터 변경 푸시 |

### POST /api/connect 요청 본문

```json
{
  "endpoint":      "opc.tcp://localhost:4840/carimatec/dm400",
  "auth_mode":     "username",
  "security_mode": "sign_encrypt",
  "username":      "admin",
  "password":      "admin123",
  "client_cert":   "/path/to/client_cert.pem",
  "client_key":    "/path/to/client_key.pem",
  "server_cert":   "/path/to/server_cert.pem"
}
```

`auth_mode` 허용값: `"anonymous"` / `"username"` / `"certificate"`  
`security_mode` 허용값: `"none"` / `"sign_encrypt"`

### WebSocket 메시지 형식

```json
{ "type": "data_change", "node_id": "AMMachine.Sensors.BedTemp", "value": 23.4, "timestamp": "2024-01-01T00:00:00+00:00" }
{ "type": "status_change", "status": "BadNoCommunication" }
```

---

## 소스 파일 상세

### `opc_client.py`

CLI 모드 진입점. `opc.cli.main()` 을 호출하고 로깅을 초기화한다.  
`asyncua` 내부 로그는 `WARNING` 레벨로 억제되어 출력이 깔끔하게 유지된다.

---

### `opc/config.py`

서버 접속에 필요한 모든 상수와 Enum을 중앙화한 설정 파일.  
이 파일만 수정하면 엔드포인트, 구독 주기, 인증서 경로 전체가 반영된다.

| 상수 | 기본값 | 설명 |
|------|--------|------|
| `SERVER_ENDPOINT` | `opc.tcp://localhost:4840/carimatec/dm400` | 서버 엔드포인트 URL |
| `CLIENT_APP_URI` | `urn:carimatec:opcua:client` | 클라이언트 ApplicationUri (인증서 SAN 과 일치해야 함) |
| `SUBSCRIPTION_PERIOD_MS` | `500` | DataChange 구독 갱신 주기 (ms) |
| `CERTS_DIR` | `../server/certs/` | 인증서 기본 디렉터리 |

**`AuthMode` Enum**

| 값 | 설명 |
|----|------|
| `ANONYMOUS` | AnonymousIdentityToken — 인증 없음 |
| `USERNAME` | UserNameIdentityToken — 사용자 이름 + 비밀번호 |
| `CERTIFICATE` | X509IdentityToken — 클라이언트 인증서 + 개인키 서명 |

**`SecurityMode` Enum**

| 값 | 설명 |
|----|------|
| `NONE` | NoSecurity — 평문 전송 |
| `SIGN_ENCRYPT` | Basic256Sha256/SignAndEncrypt — AES-256 암호화 |

---

### `opc/handler.py`

`asyncua` 구독 콜백의 기반 클래스. CLI 모드에서 직접 사용하며 값 변경을 로그로 출력한다.  
Web UI 모드에서는 `web/opc_state.py` 의 `WebSubscriptionHandler` 로 대체된다.

| 메서드 | 호출 시점 |
|--------|-----------|
| `datachange_notification(node, val, data)` | Variable 노드 값 변경 시 |
| `event_notification(event)` | 서버 이벤트 발생 시 |
| `status_change_notification(status)` | 구독 상태 변경(연결 끊김 등) 시 |

---

### `opc/client.py`

OPC UA 연결의 모든 단계를 캡슐화한 핵심 클래스 `OPCClient`.

**연결 3단계 흐름:**

```
connect()
  ├─ _setup_channel_security()   → OpenSecureChannel (암호화 협상)
  ├─ _setup_user_identity()      → ActivateSession 토큰 준비
  └─ _client.connect()           → CreateSession + ActivateSession
```

**주요 메서드:**

| 메서드 | 설명 |
|--------|------|
| `connect()` | 채널 보안 + 사용자 인증 설정 후 서버 연결 |
| `disconnect()` | 구독 삭제 후 연결 해제 |
| `browse_all()` | Objects 루트부터 DFS 탐색, Variable 노드 목록 반환 |
| `subscribe(nodes)` | DataChange 구독 생성 (SubscriptionHandler 콜백) |
| `browse_and_subscribe()` | `browse_all()` + `subscribe()` 편의 메서드 |
| `run_forever()` | Ctrl+C 까지 수신 루프 유지 |

**Context manager 사용:**

```python
from opc import OPCClient, AuthMode, SecurityMode

async with OPCClient(
    auth_mode=AuthMode.USERNAME,
    security_mode=SecurityMode.SIGN_ENCRYPT,
    username="admin",
    password="admin123",
) as client:
    await client.browse_and_subscribe()
    await client.run_forever()
```

---

### `opc/cli.py`

터미널 대화형 설정 수집 및 비동기 실행 로직.

- `prompt_config()` — 엔드포인트·인증 방식·보안 정책을 대화형으로 입력받아 `OPCClient(**config)` 에 바로 전달할 수 있는 `dict` 로 반환한다.
- `main()` — `prompt_config()` 호출 후 `asyncio.run(_run(config))` 로 클라이언트를 실행한다.

---

### `web/app.py`

FastAPI 앱 정의 및 진입점.

- **lifespan**: 앱 종료 시 `opc_state.disconnect()` 를 호출하여 OPC UA 연결을 정리한다.
- **`/`**: `static/index.html` 을 서빙한다.
- **`/static`**: CSS, JS 등 정적 파일을 서빙한다.
- **`/ws`**: WebSocket 엔드포인트 — 브라우저와 연결을 유지하고 클라이언트 ping 을 처리한다.

---

### `web/opc_state.py`

FastAPI 앱 수명 동안 유지되는 OPC UA 전역 상태 싱글턴(`opc_state`).

**`WebSubscriptionHandler`**

`SubscriptionHandler` 를 대체하는 Web 전용 콜백 핸들러.  
값 변경 시 `node_values` 캐시를 갱신하고 `asyncio.ensure_future()` 로 WebSocket 브로드캐스트를 예약한다.

> `datachange_notification` 은 asyncua 내부에서 동기적으로 호출되므로, 비동기 브로드캐스트를 직접 `await` 할 수 없다. `ensure_future()` 로 이벤트 루프에 태스크를 예약하는 방식을 사용한다.

**`OPCState`**

| 속성 | 설명 |
|------|------|
| `connected` | 현재 연결 여부 |
| `node_tree` | 프론트엔드 렌더링용 노드 트리 (`list[dict]`) |
| `node_values` | `node_id → {value, timestamp}` 캐시 |
| `node_name_map` | `node_id → display name` 매핑 |

`connect(config)` 가 호출되면:
1. 기존 연결이 있으면 먼저 해제한다 (중복 연결 방지).
2. `OPCClient` 로 서버에 연결한다.
3. Objects 루트부터 재귀적으로 노드 트리를 구성하고 Variable 초기값을 캐시한다.
4. `WebSubscriptionHandler` 로 DataChange 구독을 생성한다.

---

### `web/api/routes.py`

`/api/*` REST 엔드포인트를 정의하는 APIRouter.

연결 요청(`POST /api/connect`)을 받으면 `ConnectRequest` Pydantic 모델로 검증한 뒤 `opc_state.connect(config)` 를 호출한다. `auth_mode` / `security_mode` 는 문자열로 수신하여 각각 `AuthMode` / `SecurityMode` Enum 으로 변환한다.

---

### `web/ws/manager.py`

활성 WebSocket 연결 목록을 관리하는 `ConnectionManager`.

- `connect(ws)`: 연결 수락 후 목록에 추가.
- `disconnect(ws)`: 목록에서 제거.
- `broadcast(data)`: 모든 연결에 JSON 메시지 전송. 전송 실패한 연결(끊긴 클라이언트)은 자동 제거.

앱 전역 싱글턴 `manager` 를 `web.ws.manager` 에서 import 하여 사용한다.

---

### `web/static/index.html`

단일 페이지 대시보드 HTML.  
좌측 사이드바(연결 폼 + 노드 트리)와 우측 메인 영역(통계 그리드 + 실시간 데이터 테이블)으로 구성된다.

---

### `web/static/css/style.css`

CSS 변수 기반 다크 테마.

| 변수 | 값 | 용도 |
|------|----|------|
| `--bg` | `#0d1117` | 페이지 배경 |
| `--surface` | `#161b22` | 카드·패널 배경 |
| `--accent` | `#58a6ff` | 강조 색상 (버튼, 배지) |

값이 변경될 때 `.cell-value.flash` 애니메이션으로 하이라이트된다.  
`.tree-children.collapsed` 클래스로 노드 트리 접기/펼치기를 지원한다.

---

### `web/static/js/app.js`

모듈 패턴으로 구성된 프론트엔드 로직.

| 모듈 | 역할 |
|------|------|
| `State` | 연결 상태·노드 트리 클라이언트 측 캐시 |
| `WS` | WebSocket 관리 (자동 재연결 + 20초 ping) |
| `API` | `fetch` 기반 REST API 호출 |
| `Tree` | 노드 트리 DOM 생성 및 `updateValue()` |
| `Table` | 데이터 테이블 초기화 및 `updateRow()` (flash 효과 포함) |
| `Stats` | 상단 통계 그리드 업데이트 |
| `UI` | 연결 상태 배지·버튼 활성화 관리 |
| `Form` | 인증 방식·보안 정책 콤보 가시성 동기화 |

---

### `web/__init__.py`

`web` 패키지 초기화 시 `client/` 디렉터리를 `sys.path` 에 추가한다.  
이를 통해 `web` 패키지 내 모든 모듈에서 `from opc.xxx import ...` 구문을 사용할 수 있다.

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `FileNotFoundError: 인증서 파일이 없습니다` | `server/certs/` 에 인증서가 없음 | `cd server && python gen_certs.py` |
| `BadCertificateUriInvalid` | `CLIENT_APP_URI` 가 인증서 SAN 과 불일치 | `opc/config.py` 와 `server/gen_certs.py` 의 URI 동일한지 확인 |
| `BadUserAccessDenied` | Anonymous 인증이 서버에서 거부됨 | Username 또는 X.509 인증 방식으로 변경 |
| `BadSecureChannelClosed` | 보안 정책 불일치 | 서버가 지원하는 SecurityMode 와 동일하게 설정 |
| WebSocket 미수신 | 브라우저가 `/ws` 에 연결되지 않음 | 브라우저 콘솔에서 WebSocket 연결 오류 확인 |
