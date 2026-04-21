# 순환 임포트(Circular Import) 오류 해결

## 오류 메시지

```
[ERROR] 초기화 오류: partially initialized module 'opc_server' has no attribute 'OPC_Server'
(most likely due to a circular import)

Traceback (most recent call last):
  File "C:\OPCUA\windows_service.py", line 59, in _run
    import opc_server
  File "C:\OPCUA\opc_server.py", line 19, in <module>
    import handler
  File "C:\OPCUA\handler.py", line 27, in <module>
    async def tcp_handler(..., opc_server: opc_server.OPC_Server):
AttributeError: partially initialized module 'opc_server' has no attribute 'OPC_Server'
```

---

## 원인 분석

### 순환 임포트 발생 구조

```
windows_service.py
    └─ import opc_server
           └─ (module level) import handler        ← opc_server.py line 19
                  └─ (module level) import opc_server  ← handler.py line 23
                         └─ ⚠ 아직 OPC_Server 클래스가 정의되지 않은 상태
```

### Python 모듈 로딩 순서와 문제 발생 지점

1. `windows_service.py` 가 `opc_server` 모듈 임포트 시작
2. Python이 `opc_server.py` 를 실행하기 시작 → `sys.modules['opc_server']` 에 **미완성 모듈 객체** 등록
3. `opc_server.py` line 19: `import handler` 실행
4. Python이 `handler.py` 를 실행하기 시작
5. `handler.py` line 23: `import opc_server` 실행
6. Python이 `sys.modules` 에서 `opc_server` 를 찾음 → 2번에서 등록된 **미완성 객체** 반환
7. `handler.py` line 27: 함수 정의 시 타입 어노테이션 `opc_server.OPC_Server` 평가
8. `OPC_Server` 클래스는 `opc_server.py` line 33에 정의되어 있지만 아직 실행되지 않은 상태
9. `AttributeError: partially initialized module 'opc_server' has no attribute 'OPC_Server'` 발생

### 핵심 문제: 두 파일의 상호 의존

| 파일 | 임포트 대상 | 사용 위치 |
|------|------------|----------|
| `opc_server.py` | `handler` (모듈 레벨) | `main()` 함수 내부에서만 사용 |
| `handler.py` | `opc_server` (모듈 레벨) | 타입 힌트 `opc_server.OPC_Server` 에서만 사용 |

두 임포트 모두 모듈 레벨에 있어 파일이 로딩되는 순간 실행되어 순환이 발생합니다.

---

## 해결 방법

### 1. `handler.py` — `TYPE_CHECKING` 가드로 런타임 임포트 제거

**수정 전:**
```python
import asyncio
import GitConn.OPCUA.server.opc_server as opc_server
from asyncua import ua

async def tcp_handler(reader, writer, opc_server: opc_server.OPC_Server):
```

**수정 후:**
```python
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from asyncua import ua

if TYPE_CHECKING:
    from GitConn.OPCUA.server.opc_server import OPC_Server

async def tcp_handler(reader, writer, opc_server: OPC_Server):
```

**핵심 원리:**
- `TYPE_CHECKING` 은 mypy/IDE 같은 타입 검사 도구 실행 시에만 `True`
- 런타임(실제 실행)에서는 `False` 이므로 `if TYPE_CHECKING:` 블록 내 임포트는 실행되지 않음
- `from __future__ import annotations` 는 모든 타입 어노테이션을 문자열로 지연 평가(PEP 563)
  - 함수 정의 시점에 `OPC_Server` 를 실제로 찾지 않아도 됨

### 2. `opc_server.py` — `handler` 임포트를 `main()` 내부로 이동

**수정 전:**
```python
# 모듈 레벨 (파일 상단)
import GitConn.OPCUA.server.handler as handler
```

**수정 후:**
```python
# main() 함수 내부로 이동
async def main():
    import GitConn.OPCUA.server.handler as handler
    ...
```

**핵심 원리:**
- `handler` 는 `main()` 함수 안에서만 사용되므로 모듈 레벨에 있을 필요가 없음
- `main()` 이 호출되는 시점에는 `opc_server` 모듈이 완전히 초기화된 상태
- 이 시점의 임포트는 `handler.py` 가 `opc_server` 를 다시 임포트해도 이미 완성된 모듈을 반환

---

## 수정 파일 요약

### `server/handler.py`

```diff
+ from __future__ import annotations
  import asyncio
- import GitConn.OPCUA.server.opc_server as opc_server
+ from typing import TYPE_CHECKING
  from asyncua import ua

+ if TYPE_CHECKING:
+     from GitConn.OPCUA.server.opc_server import OPC_Server

- async def tcp_handler(reader, writer, opc_server: opc_server.OPC_Server):
+ async def tcp_handler(reader, writer, opc_server: OPC_Server):
```

### `server/opc_server.py`

```diff
  import asyncio
  import GitConn.OPCUA.server.tcp_server as tcp_server
- import GitConn.OPCUA.server.handler as handler
  import GitConn.OPCUA.server.gen_certs as gen_certs

  async def main():
+     import GitConn.OPCUA.server.handler as handler
      gen_certs.ensure_server_certs()
      ...
```

---

## Windows 배포 환경 주의사항

Windows (`C:\OPCUA\`) 에 배포할 때는 패키지 경로(`GitConn.OPCUA.server`)가 없으므로
`windows_service.py` 의 `_run()` 메서드가 `sys.path` 에 프로젝트 루트를 추가합니다.

```python
project_root = os.path.abspath(os.path.join(server_dir, '..', '..', '..'))
sys.path.insert(0, project_root)
```

배포 경로가 `C:\GitConn\OPCUA\server\` 구조를 따르거나,
플랫 배포(`C:\OPCUA\`) 시에는 `import opc_server` 형태의 단순 임포트로 변경해야 합니다.

---

## 순환 임포트 예방 원칙

1. **타입 힌트 전용 임포트는 `TYPE_CHECKING` 가드 사용** — 런타임에 불필요한 의존성 방지
2. **모듈 레벨 임포트는 해당 모듈이 실제로 필요한 시점에만** — 함수 내부 임포트 활용
3. **의존 방향은 단방향으로** — `A → B → A` 구조 설계 단계에서 차단
