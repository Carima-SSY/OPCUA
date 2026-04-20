"""
OPC UA 구독 이벤트 핸들러 (opc/handler.py)

역할:
    asyncua Subscription 이 DataChange · Event · StatusChange 알림을 수신할 때
    호출되는 콜백 메서드를 정의한다.

asyncua 콜백 계약:
    - datachange_notification : Variable 값이 변경될 때마다 호출.
                                (node, val, data) — val 은 Python 네이티브 타입.
    - event_notification      : 이벤트 기반 알림 수신 시 호출.
    - status_change_notification : 구독 상태 변경(연결 끊김 등) 시 호출.

    세 메서드 모두 asyncua 의 내부 async 태스크에서 동기적으로 호출된다.
    즉, 이벤트 루프가 실행 중인 컨텍스트에서 실행된다.

확장 방법:
    이 클래스를 상속하여 datachange_notification 을 오버라이드하면
    웹 UI 브로드캐스트 · DB 저장 · 알람 처리 등 다양한 동작으로 확장할 수 있다.
    (web/opc_state.py 의 WebSubscriptionHandler 가 이 패턴을 사용한다.)
"""

import logging

from asyncua import Node

logger = logging.getLogger(__name__)


class SubscriptionHandler:
    """
    OPC UA DataChange 구독 기본 핸들러.

    수신된 값을 로그에 출력한다. CLI 모드에서 직접 사용한다.
    웹 UI 모드에서는 WebSubscriptionHandler 로 대체된다.
    """

    def datachange_notification(self, node: Node, val, data):
        """
        Variable 노드의 값이 변경될 때 asyncua 가 호출한다.

        Args:
            node : 값이 변경된 OPC UA 노드 객체
            val  : 변경된 값 (Python 네이티브 타입 — int, float, str 등)
            data : asyncua DataChangeNotification 원본 (타임스탬프 등 포함)
        """
        logger.info("[DATA] %-45s = %s", node.nodeid.Identifier, val)

    def event_notification(self, event):
        """서버 이벤트 발생 시 호출된다."""
        logger.info("[EVENT] %s", event)

    def status_change_notification(self, status):
        """
        구독 상태가 변경될 때 호출된다.

        연결 끊김(BadTimeout, BadNoCommunication 등)을 감지하는 용도로 활용한다.
        """
        logger.warning("[SUBSCRIPTION STATUS] %s", status)
