"""
OPC UA 연결 상태 관리자 (opc_state.py)

FastAPI 앱 전역 OPC UA 연결 상태를 보관하고,
데이터 변경 시 WebSocket 으로 브로드캐스트하는 구독 핸들러를 제공한다.
"""

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any

from asyncua import Node, ua

from opc.client import OPCClient
from opc.config import SUBSCRIPTION_PERIOD_MS, AuthMode, SecurityMode
from web.ws.manager import manager as ws_manager

logger = logging.getLogger(__name__)

_NODE_CLASS_LABEL = {
    ua.NodeClass.Object:   "Object",
    ua.NodeClass.Variable: "Variable",
    ua.NodeClass.Method:   "Method",
    ua.NodeClass.View:     "View",
}


# ── WebSocket 구독 핸들러 ──────────────────────────────────────────────────────

class WebSubscriptionHandler:
    """
    OPC UA 데이터 변경을 수신하여 캐시를 갱신하고 WebSocket 으로 브로드캐스트한다.

    asyncua 는 datachange_notification 을 이벤트 루프 내부 태스크에서 동기적으로 호출한다.
    asyncio.ensure_future 로 브로드캐스트 코루틴을 루프에 예약한다.
    """

    def __init__(self, cache: dict[str, Any]):
        self._cache = cache

    def datachange_notification(self, node: Node, val, data):
        node_id = str(node.nodeid.Identifier)
        ts = datetime.now(timezone.utc).isoformat()

        # float 특수값 정규화 (JSON 직렬화 불가)
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            val = str(val)

        self._cache[node_id] = {"value": val, "timestamp": ts}

        payload = {"type": "data_change", "node_id": node_id, "value": val, "timestamp": ts}
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(ws_manager.broadcast(payload))
        except RuntimeError:
            pass

    def event_notification(self, event):
        pass

    def status_change_notification(self, status):
        logger.warning("[OPC SUBSCRIPTION] 상태 변경: %s", status)
        asyncio.ensure_future(
            ws_manager.broadcast({"type": "status_change", "status": str(status)})
        )


# ── OPC UA 전역 상태 ──────────────────────────────────────────────────────────

class OPCState:
    """
    FastAPI 앱 수명 동안 유지되는 OPC UA 연결 상태.

    connect() 호출 시 OPCClient 를 사용해 채널/인증을 설정하고,
    노드 트리를 구성한 뒤 WebSubscriptionHandler 로 DataChange 구독을 시작한다.
    """

    def __init__(self):
        self._client: OPCClient | None = None
        self._lock = asyncio.Lock()

        self.connected: bool = False
        self.node_tree: list[dict] = []    # 프론트엔드 렌더링용 트리
        self.node_values: dict[str, Any] = {}   # node_id → {value, timestamp}
        self.node_name_map: dict[str, str] = {}  # node_id → display name

    # ── 공개 API ──────────────────────────────────────────────────────────────

    async def connect(self, config: dict) -> dict:
        """
        OPC UA 서버에 연결하고 노드 트리를 구성한 뒤 DataChange 구독을 시작한다.

        Returns:
            {"node_count": int, "tree": list}
        """
        async with self._lock:
            if self.connected:
                await self._do_disconnect()

            self.node_values = {}
            self.node_name_map = {}

            opc = OPCClient(**config)
            await opc.connect()

            # Objects 루트에서 트리를 구성하고 Variable 노드를 수집한다
            root = opc._client.nodes.objects
            var_nodes: list[Node] = []
            visited: set[str] = set()
            tree_root = await self._build_tree(root, var_nodes, visited)

            self.node_tree = tree_root.get("children", [])

            # WebSocket 브로드캐스트 핸들러로 구독 생성
            handler = WebSubscriptionHandler(self.node_values)
            sub = await opc._client.create_subscription(
                period=SUBSCRIPTION_PERIOD_MS, handler=handler
            )
            await sub.subscribe_data_change(var_nodes)
            opc._subscription = sub

            self._client = opc
            self.connected = True
            logger.info("[STATE] 연결 완료 — %d 개 Variable 노드 구독", len(var_nodes))
            return {"node_count": len(var_nodes), "tree": self.node_tree}

    async def disconnect(self) -> None:
        async with self._lock:
            await self._do_disconnect()

    async def _do_disconnect(self) -> None:
        if self._client:
            await self._client.disconnect()
            self._client = None
        self.connected = False
        self.node_tree = []
        self.node_values = {}
        self.node_name_map = {}
        logger.info("[STATE] 연결 해제")

    # ── 내부: 노드 트리 구성 ──────────────────────────────────────────────────

    async def _build_tree(
        self,
        node: Node,
        var_nodes: list[Node],
        visited: set[str],
    ) -> dict:
        """
        재귀적으로 노드 트리를 구성하고 Variable 노드를 var_nodes 에 수집한다.

        반환 형식:
          {
            "node_id":      str,   # NodeId.Identifier (ex: "AMMachine.Status.State")
            "node_id_full": str,   # 전체 NodeId 문자열 (ex: "ns=2;s=AMMachine.Status.State")
            "name":         str,   # BrowseName
            "class":        str,   # "Object" | "Variable" | ...
            "value":        Any,   # Variable 의 초기값, Object 는 None
            "children":     list,  # 자식 노드 목록
          }
        """
        node_id_full = node.nodeid.to_string()
        if node_id_full in visited:
            return {}
        visited.add(node_id_full)

        try:
            browse_name = await node.read_browse_name()
            node_class  = await node.read_node_class()
        except Exception:
            return {}

        node_id  = str(node.nodeid.Identifier)
        name     = browse_name.Name
        cls_label = _NODE_CLASS_LABEL.get(node_class, str(node_class))

        entry: dict[str, Any] = {
            "node_id":      node_id,
            "node_id_full": node_id_full,
            "name":         name,
            "class":        cls_label,
            "value":        None,
            "children":     [],
        }

        if node_class == ua.NodeClass.Variable:
            try:
                val = await node.read_value()
                if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                    val = str(val)
                entry["value"] = val
                var_nodes.append(node)
                # 이름 맵 구성 (테이블 표시용)
                self.node_name_map[node_id] = name
                # 초기값 캐시
                self.node_values[node_id] = {
                    "value": val,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            except Exception:
                pass

        try:
            for child in await node.get_children():
                child_entry = await self._build_tree(child, var_nodes, visited)
                if child_entry:
                    entry["children"].append(child_entry)
        except Exception:
            pass

        return entry


# 앱 전역 싱글턴
opc_state = OPCState()
