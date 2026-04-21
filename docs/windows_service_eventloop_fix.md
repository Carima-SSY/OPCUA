# Windows 서비스 asyncio 이벤트 루프 오류 해결

## 오류 메시지

```
[ERROR] 초기화 오류: set_wakeup_fd only works in main thread of the main interpreter

Traceback (most recent call last):
  File "C:\OPCUA\windows_service.py", line 61, in _run
    self._loop = asyncio.new_event_loop()
  File "...\asyncio\windows_events.py", line 316, in __init__
    super().__init__(proactor)
  File "...\asyncio\proactor_events.py", line 643, in __init__
    signal.set_wakeup_fd(self._csock.fileno())
ValueError: set_wakeup_fd only works in main thread of the main interpreter
```

---

## 원인 분석

### Windows 서비스의 스레드 구조

Windows 서비스는 SCM(Service Control Manager)이 서비스 프로세스를 시작하면,
`SvcDoRun()` 메서드를 **메인 스레드가 아닌 워커 스레드**에서 호출합니다.

```
[프로세스 시작]
  └─ 메인 스레드: win32serviceutil 초기화, SCM 연결 대기
       └─ 워커 스레드: SvcDoRun() → _run() → asyncio.new_event_loop()  ← 여기서 오류
```

### `ProactorEventLoop`의 `signal.set_wakeup_fd()` 호출

Python 3.8부터 Windows의 기본 이벤트 루프는 `ProactorEventLoop`(IOCP 기반)입니다.
`asyncio.new_event_loop()`는 현재 정책(기본: `WindowsProactorEventLoopPolicy`)에 따라
`ProactorEventLoop` 인스턴스를 생성합니다.

`ProactorEventLoop.__init__()` 내부 호출 순서:
```
ProactorEventLoop.__init__()
  └─ BaseProactorEventLoop.__init__()
       └─ BaseSelectorEventLoop.__init__()  (실제로는 다른 경로지만 유사)
            └─ signal.set_wakeup_fd(self._csock.fileno())  ← ValueError 발생
```

`signal.set_wakeup_fd()`는 **CPython 인터프리터의 메인 스레드에서만** 동작합니다.
워커 스레드에서 호출하면 `ValueError`가 발생합니다.

### 영향 범위

| 조건 | 영향 |
|------|------|
| Python 3.8+ / Windows | 항상 재현 |
| Python 3.7 이하 | `SelectorEventLoop`이 기본이어서 미발생 |
| Linux/macOS | `ProactorEventLoop` 없어서 미발생 |
| 직접 실행(`python opc_server.py`) | 메인 스레드 실행이어서 미발생 |

---

## 해결 방법

### `SelectorEventLoop`을 직접 생성

`ProactorEventLoop` 대신 `SelectorEventLoop`을 명시적으로 생성합니다.
`SelectorEventLoop`은 초기화 시 `signal.set_wakeup_fd()`를 호출하지 않아
워커 스레드에서도 안전합니다.

**수정 전:**
```python
self._loop = asyncio.new_event_loop()   # ProactorEventLoop 생성 → ValueError
asyncio.set_event_loop(self._loop)
```

**수정 후:**
```python
self._loop = asyncio.SelectorEventLoop()  # set_wakeup_fd 호출 없음
asyncio.set_event_loop(self._loop)
```

### `SelectorEventLoop` vs `ProactorEventLoop` 비교

| 항목 | `ProactorEventLoop` | `SelectorEventLoop` |
|------|--------------------|--------------------|
| 기반 기술 | Windows IOCP | `select()` / `WaitForMultipleObjects` |
| 기본값 (Python 3.8+/Windows) | ✅ | ✗ |
| TCP/UDP 서버 | ✅ | ✅ |
| SSL/TLS | ✅ | ✅ |
| 서브프로세스 (Windows) | ✅ | ✗ |
| 워커 스레드 생성 가능 | ✗ | ✅ |
| OPC UA + TCP 서버 용도 | 사용 불가 (서비스 환경) | **사용 가능** ✅ |

이 서버는 TCP 연결과 OPC UA 통신만 사용하며 서브프로세스가 없으므로
`SelectorEventLoop`으로 모든 기능이 정상 동작합니다.

---

## 수정 파일

### `server/windows_service.py`

```diff
- self._loop = asyncio.new_event_loop()
+ self._loop = asyncio.SelectorEventLoop()
  asyncio.set_event_loop(self._loop)
```

---

## 참고: 대안적 방법 (미채택)

### 방법 A: `WindowsSelectorEventLoopPolicy` 전역 설정

```python
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
self._loop = asyncio.new_event_loop()
```

전역 정책을 변경하므로 다른 모듈에도 영향을 줄 수 있어 미채택.

### 방법 B: `ValueError` 예외 무시

```python
try:
    self._loop = asyncio.new_event_loop()
except ValueError:
    self._loop = asyncio.SelectorEventLoop()
```

오류를 회피할 뿐 근본 원인을 해결하지 않아 미채택.
