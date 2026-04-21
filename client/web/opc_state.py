"""
OPC UA 다중 서버 연결 상태 관리자 (opc_state.py)

다수의 OPC UA 서버 연결을 동시에 관리하고,
각 서버의 DataChange 를 server_id 포함 WebSocket 메시지로 브로드캐스트한다.
"""

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from asyncua import Node, ua

from opc.client import OPCClient
from opc.config import SUBSCRIPTION_PERIOD_MS
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
    OPC UA DataChange 를 수신하여 캐시를 갱신하고 server_id 를 포함한
    메시지를 WebSocket 으로 브로드캐스트한다.

    asyncua 는 datachange_notification 을 이벤트 루프 내부 태스크에서
    동기적으로 호출하므로, asyncio.ensure_future 로 브로드캐스트를 예약한다.
    """

    def __init__(self, server_id: str, cache: dict[str, Any]):
        self._server_id = server_id
        self._cache = cache

    def datachange_notification(self, node: Node, val, data):
        node_id = str(node.nodeid.Identifier)
        ts = datetime.now(timezone.utc).isoformat()

        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            val = str(val)

        self._cache[node_id] = {"value": val, "timestamp": ts}

        payload = {
            "type":      "data_change",
            "server_id": self._server_id,
            "node_id":   node_id,
            "value":     val,
            "timestamp": ts,
        }
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(ws_manager.broadcast(payload))
        except RuntimeError:
            pass

    def event_notification(self, event):
        pass

    def status_change_notification(self, status):
        logger.warning("[OPC %s] 상태 변경: %s", self._server_id, status)
        asyncio.ensure_future(
            ws_manager.broadcast({
                "type":      "status_change",
                "server_id": self._server_id,
                "status":    str(status),
            })
        )


# ── 서버 세션 ─────────────────────────────────────────────────────────────────

@dataclass
class ServerSession:
    """단일 OPC UA 서버 연결 세션이 보유하는 데이터."""
    server_id:     str
    endpoint:      str
    client:        OPCClient
    node_tree:     list = field(default_factory=list)
    node_values:   dict = field(default_factory=dict)    # node_id → {value, timestamp}
    node_name_map: dict = field(default_factory=dict)    # node_id → display name


# ── 다중 서버 상태 ────────────────────────────────────────────────────────────

class MultiServerState:
    """
    FastAPI 앱 수명 동안 유지되는 다중 OPC UA 서버 연결 상태 싱글턴.

    connect() 호출마다 고유한 server_id 를 발급하여 세션을 독립적으로 관리한다.
    동일 엔드포인트에 중복 연결도 허용하며, 각 세션은 완전히 독립된다.
    """

    def __init__(self):
        self._sessions: dict[str, ServerSession] = {}
        self._lock = asyncio.Lock()

    @property
    def sessions(self) -> dict[str, ServerSession]:
        return self._sessions

    # ── 공개 API ──────────────────────────────────────────────────────────────

    async def connect(self, config: dict) -> dict:
        """
        새 OPC UA 서버에 연결하고 노드 트리를 구성한 뒤 DataChange 구독을 시작한다.

        Returns:
            {"server_id": str, "node_count": int, "tree": list}
        """
        async with self._lock:
            server_id = str(uuid.uuid4())[:8]

            opc = OPCClient(**config)
            await opc.connect()

            session = ServerSession(
                server_id=server_id,
                endpoint=config["endpoint"],
                client=opc,
            )

            root = opc._client.nodes.objects
            var_nodes: list[Node] = []
            visited: set[str] = set()
            tree_root = await self._build_tree(root, var_nodes, visited, session)
            session.node_tree = tree_root.get("children", [])

            handler = WebSubscriptionHandler(server_id, session.node_values)
            sub = await opc._client.create_subscription(
                period=SUBSCRIPTION_PERIOD_MS, handler=handler
            )
            await sub.subscribe_data_change(var_nodes)
            opc._subscription = sub

            self._sessions[server_id] = session
            logger.info(
                "[STATE] %s 연결 완료 (%s) — %d 개 노드",
                server_id, config["endpoint"], len(var_nodes),
            )
            return {
                "server_id":  server_id,
                "node_count": len(var_nodes),
                "tree":       session.node_tree,
            }

    async def disconnect(self, server_id: str) -> None:
        """특정 서버 연결을 해제하고 세션을 제거한다."""
        async with self._lock:
            session = self._sessions.pop(server_id, None)
            if session:
                await session.client.disconnect()
                logger.info("[STATE] %s 연결 해제", server_id)

    async def disconnect_all(self) -> None:
        """모든 서버 연결을 해제한다 (앱 종료 시 호출)."""
        for server_id in list(self._sessions.keys()):
            await self.disconnect(server_id)

    # ── 내부: 노드 트리 구성 ──────────────────────────────────────────────────

    async def _build_tree(
        self,
        node:      Node,
        var_nodes: list,
        visited:   set,
        session:   ServerSession,
    ) -> dict:
        """
        재귀적으로 노드 트리를 구성하고 Variable 노드를 var_nodes 에 수집한다.
        순환 참조 방지를 위해 visited 집합으로 이미 방문한 노드를 추적한다.
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

        # Object / Variable 이외의 노드(Method, View 등)는 UI에 표시하지 않는다
        if node_class not in (ua.NodeClass.Object, ua.NodeClass.Variable):
            return {}

        node_id   = str(node.nodeid.Identifier)
        name      = browse_name.Name
        cls_label = _NODE_CLASS_LABEL.get(node_class, str(node_class))

        entry: dict = {
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
                session.node_name_map[node_id] = name
                session.node_values[node_id] = {
                    "value":     val,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            except Exception:
                pass

        try:
            for child in await node.get_children():
                # namespace 0 = OPC UA 표준 인프라 노드(Server 등) — 건너뜀
                if child.nodeid.NamespaceIndex == 0:
                    continue
                child_entry = await self._build_tree(child, var_nodes, visited, session)
                if child_entry:
                    entry["children"].append(child_entry)
        except Exception:
            pass

        return entry


# 앱 전역 싱글턴
opc_state = MultiServerState()
