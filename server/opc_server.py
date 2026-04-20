"""
OPC UA 서버 핵심 모듈 (opc_server.py)

역할:
  - asyncua 라이브러리를 래핑하여 OPC UA 서버를 초기화·관리
  - DM400 장비의 Address Space(노드 트리)를 정의
  - TCP 내부 서버와 연동하여 장비 데이터를 OPC UA 노드에 반영

아키텍처 흐름:
  장비(DM400) → TCP(9000) → handler.py → OPC_Server 노드 업데이트
                                                       ↓
                                           OPC UA 클라이언트 (UaExpert 등)
"""

from asyncua import Server, ua, Node

import asyncio
import GitConn.OPCUA.server.tcp_server as tcp_server
import GitConn.OPCUA.server.handler as handler
import GitConn.OPCUA.server.gen_certs as gen_certs

# ── 서버 엔드포인트 / 식별자 상수 ───────────────────────────────────────────
# OPC UA 클라이언트가 접속할 URL. 0.0.0.0 으로 모든 인터페이스 바인딩
END_POINT = "opc.tcp://0.0.0.0:4840/carimatec/dm400"

# 서버 인증서의 SubjectAlternativeName URI 와 일치해야 채널 협상이 성공
APPLICATION_URI = "urn:carimatec:opcua:server"

# OPC UA Address Space 내 커스텀 노드의 네임스페이스 URI
NAMESPACE = "http://c-hub.info/opcua/device/dm400"


class OPC_Server:
    """
    OPC UA 서버 래퍼 클래스.

    asyncua.Server 의 설정·초기화를 캡슐화하고,
    노드 딕셔너리(nodelists)를 통해 handler.py 와 데이터를 공유한다.

    Attributes:
        endpoint (str)       : OPC UA 접속 URL
        namespace (str)      : 커스텀 네임스페이스 URI
        application_uri (str): 서버 애플리케이션 URI (인증서 SAN 과 일치)
        server (Server)      : asyncua Server 인스턴스
        ns_idx (int)         : 등록된 네임스페이스 인덱스
        objects (Node)       : OPC UA Objects 폴더 노드
        nodelists (dict)     : init_opcua() 가 반환한 노드 참조 트리
    """

    def __init__(self, endpoint, application_uri, namespace):
        self.endpoint = endpoint
        self.namespace = namespace
        self.application_uri = application_uri

        self.server = None   # set_opcserver() 호출 전까지 None

        self.ns_idx = None   # register_namespace() 반환값
        self.objects = None  # OPC UA Objects 루트 노드

        # handler.py 가 노드를 업데이트할 때 참조하는 딕셔너리
        # 구조: {"AMMachine": {"identifier": {...}, "status": {...}, "sensors": {...}}}
        self.nodelists = dict()

    async def set_opcserver(self):
        """
        OPC UA 서버를 초기화한다.

        수행 순서:
          1. Server 인스턴스 생성 및 기본 초기화
          2. Self-signed 인증서·개인키 로드
          3. ApplicationUri 설정 (인증서 SAN 과 반드시 일치)
          4. 엔드포인트 URL 설정
          5. 보안 정책 설정 (NoSecurity + Basic256Sha256_SignAndEncrypt)
          6. 커스텀 네임스페이스 등록
        """
        self.server = Server()

        await self.server.init()

        # Self-signed 서버 인증서와 개인키 로드
        # gen_certs.py 로 생성한 certs/server_cert.pem, certs/server_key.pem 사용
        await self.server.load_certificate("certs/server_cert.pem")
        await self.server.load_private_key("certs/server_key.pem")

        # ApplicationUri 는 인증서의 SAN(URI) 필드와 정확히 일치해야 함
        # 불일치 시 클라이언트가 채널 협상 단계에서 BadCertificateUriInvalid 오류 반환
        await self.server.set_application_uri(self.application_uri)

        self.server.set_endpoint(self.endpoint)

        # 보안 정책 목록 설정
        #   NoSecurity              : 암호화 없음 (개발·테스트용)
        #   Basic256Sha256_SignAndEncrypt : AES-256 암호화 + SHA-256 서명 (운영 권장)
        self.server.set_security_policy([
            ua.SecurityPolicyType.NoSecurity,
            ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt
        ])

        # 커스텀 네임스페이스 등록 후 인덱스 보관
        # 모든 커스텀 노드 ID 는 이 ns_idx 를 사용
        self.ns_idx = await self.server.register_namespace(self.namespace)
        self.objects = self.server.nodes.objects

    def set_nodelist(self, nodes: dict):
        """init_opcua() 가 반환한 노드 참조 트리를 저장한다."""
        self.nodelists = nodes

    # ── get-or-create 패턴 헬퍼 ───────────────────────────────────────────
    # 서버 재시작 시 이미 존재하는 노드를 재사용하고, 없으면 새로 생성.
    # 중복 생성으로 인한 오류를 방지하는 방어적 초기화 패턴.

    async def gc_object(self, parent: Node, nodeid_str, browse_name, ns_idx):
        """
        Object 노드를 get-or-create 방식으로 반환.

        - 이미 존재하면 기존 노드를 반환 (브라우즈 이름 읽기 성공 = 존재 확인)
        - 없으면 parent 아래에 새 Object 노드를 생성하여 반환
        """
        nodeid = ua.NodeId(nodeid_str, ns_idx)
        node = self.server.get_node(nodeid)

        try:
            await node.read_browse_name()
            print(f"[OK] Object exists: {nodeid_str}")
            return node
        except Exception:
            print(f"[CREATE] Object created: {nodeid_str}")
            return await parent.add_object(nodeid, browse_name)

    async def gc_variable(self, parent: Node, nodeid_str, browse_name, value, variant_type, ns_idx):
        """
        Variable 노드를 get-or-create 방식으로 반환.

        - 이미 존재하면 기존 노드를 반환 (값 읽기 성공 = 존재 확인)
        - 없으면 parent 아래에 초기값(value)으로 새 Variable 노드를 생성하여 반환
        """
        nodeid = ua.NodeId(nodeid_str, ns_idx)
        node = self.server.get_node(nodeid)

        try:
            await node.read_value()
            print(f"[OK] Variable exists: {nodeid_str}")
            return node
        except Exception:
            print(f"[CREATE] Variable created: {nodeid_str}")
            return await parent.add_variable(nodeid, browse_name, ua.Variant(value, variant_type))

    async def gc_method(self, parent: Node, nodeid_str, browse_name, handler, in_args, out_args, ns_idx):
        """
        Method 노드를 get-or-create 방식으로 반환.

        - 이미 존재하면 기존 노드를 반환
        - 없으면 parent 아래에 새 Method 노드를 생성하여 반환
        """
        nodeid = ua.NodeId(nodeid_str, ns_idx)
        node = self.server.get_node(nodeid)

        try:
            await node.read_browse_name()
            print(f"[OK] Method exists: {nodeid_str}")
            return node
        except Exception:
            print(f"[CREATE] Method created: {nodeid_str}")
            return await parent.add_method(nodeid, browse_name, handler, in_args, out_args)


async def init_opcua(opc_server: OPC_Server):
    """
    DM400 장비의 OPC UA Address Space 를 구성한다.

    노드 트리 구조:
      Objects/
      └── AMMachine                         (Object)
          ├── MachineIdentifier             (Object)
          │   ├── Manufacturer              (Variable, String)
          │   ├── Model                     (Variable, String)
          │   └── SerialNumber              (Variable, String)
          ├── MachineStatus                 (Object)
          │   ├── State                     (Variable, Int32)  장비 상태 코드
          │   ├── StateText                 (Variable, String) 상태 텍스트
          │   ├── Progress                  (Variable, Int32)  진행률 (%)
          │   ├── BuildJob                  (Variable, String) 현재 작업명
          │   ├── CurrentLayer              (Variable, Int32)  현재 레이어
          │   ├── TotalLayers               (Variable, Int32)  전체 레이어 수
          │   ├── RemainingTime             (Variable, Int32)  남은 시간 (초)
          │   ├── TotalBuildTime            (Variable, Int32)  총 빌드 시간 (초)
          │   ├── StartTime                 (Variable, Int32)  시작 시각 (Unix)
          │   └── EndTime                   (Variable, Int32)  종료 시각 (Unix)
          └── Sensors                       (Object)
              ├── BuildPlatformZPosition    (Variable, Float)  빌드 플랫폼 Z축 위치
              ├── LevelTankZPosition        (Variable, Float)  수평 탱크 Z축 위치
              ├── BladeState                (Variable, Int32)  블레이드 상태
              ├── CollectBladeState         (Variable, Int32)  수집 블레이드 상태
              ├── PrintBladeState           (Variable, Int32)  출력 블레이드 상태
              ├── ResinTemp                 (Variable, Float)  레진 온도 (°C)
              ├── ResinLevel                (Variable, Float)  레진 수위
              ├── ResinLevelStablity        (Variable, Float)  레진 수위 안정도
              ├── VatPres                   (Variable, Float)  배트 압력
              ├── UVLTemp                   (Variable, Float)  UV 좌측 온도 (°C)
              └── UVRTemp                   (Variable, Float)  UV 우측 온도 (°C)

    Returns:
        dict: 노드 참조 트리 (handler.py 에서 set_value 호출에 사용)
        None: 초기화 실패 시
    """
    try:
        ns = opc_server.ns_idx

        # 최상위 AMMachine Object 노드
        machine = await opc_server.gc_object(parent=opc_server.objects, nodeid_str="AMMachine", browse_name="AMMachine", ns_idx=ns)

        # ── 식별자 그룹 ────────────────────────────────────────────────────
        identifier = await opc_server.gc_object(parent=machine, nodeid_str="AMMachine.Identifier", browse_name="MachineIdentifier", ns_idx=ns)
        manufacturer = await opc_server.gc_variable(parent=identifier, nodeid_str="AMMachine.Identifier.Manufacturer", browse_name="Manufacturer", value="Carimatec", variant_type=ua.VariantType.String, ns_idx=ns)
        model = await opc_server.gc_variable(parent=identifier, nodeid_str="AMMachine.Identifier.Model", browse_name="Model", value="DM400", variant_type=ua.VariantType.String, ns_idx=ns)
        serial_number = await opc_server.gc_variable(parent=identifier, nodeid_str="AMMachine.Identifier.SerialNumber", browse_name="SerialNumber", value="DM400-TEST-000001", variant_type=ua.VariantType.String, ns_idx=ns)

        # ── 상태 그룹 ──────────────────────────────────────────────────────
        status = await opc_server.gc_object(parent=machine, nodeid_str="AMMachine.Status", browse_name="MachineStatus", ns_idx=ns)
        state = await opc_server.gc_variable(parent=status, nodeid_str="AMMachine.Status.State", browse_name="State", value=0, variant_type=ua.VariantType.Int32, ns_idx=ns)
        state_text = await opc_server.gc_variable(parent=status, nodeid_str="AMMachine.Status.StateText", browse_name="StateText", value="", variant_type=ua.VariantType.String, ns_idx=ns)
        progress = await opc_server.gc_variable(parent=status, nodeid_str="AMMachine.Status.Progress", browse_name="Progress", value=0, variant_type=ua.VariantType.Int32, ns_idx=ns)
        build_job = await opc_server.gc_variable(parent=status, nodeid_str="AMMachine.Status.BuildJob", browse_name="BuildJob", value="", variant_type=ua.VariantType.String, ns_idx=ns)
        current_layer = await opc_server.gc_variable(parent=status, nodeid_str="AMMachine.Status.CurrentLayer", browse_name="CurrentLayer", value=0, variant_type=ua.VariantType.Int32, ns_idx=ns)
        total_layers = await opc_server.gc_variable(parent=status, nodeid_str="AMMachine.Status.TotalLayers", browse_name="TotalLayers", value=0, variant_type=ua.VariantType.Int32, ns_idx=ns)
        remaining_time = await opc_server.gc_variable(parent=status, nodeid_str="AMMachine.Status.RemainingTime", browse_name="RemainingTime", value=0, variant_type=ua.VariantType.Int32, ns_idx=ns)
        totalbuild_time = await opc_server.gc_variable(parent=status, nodeid_str="AMMachine.Status.TotalBuildTime", browse_name="TotalBuildTime", value=0, variant_type=ua.VariantType.Int32, ns_idx=ns)
        start_time = await opc_server.gc_variable(parent=status, nodeid_str="AMMachine.Status.StartTime", browse_name="StartTime", value=0, variant_type=ua.VariantType.Int32, ns_idx=ns)
        end_time = await opc_server.gc_variable(parent=status, nodeid_str="AMMachine.Status.EndTime", browse_name="EndTime", value=0, variant_type=ua.VariantType.Int32, ns_idx=ns)

        # ── 센서 그룹 ──────────────────────────────────────────────────────
        sensors = await opc_server.gc_object(parent=machine, nodeid_str="AMMachine.Sensors", browse_name="Sensors", ns_idx=ns)
        platform_zpos = await opc_server.gc_variable(parent=sensors, nodeid_str="AMMachine.Sensors.BuildPlatformZPosition", browse_name="BuildPlatformZPosition", value=0.0, variant_type=ua.VariantType.Float, ns_idx=ns)
        tank_zpos = await opc_server.gc_variable(parent=sensors, nodeid_str="AMMachine.Sensors.LevelTankZPosition", browse_name="LevelTankZPosition", value=0.0, variant_type=ua.VariantType.Float, ns_idx=ns)
        blade_state = await opc_server.gc_variable(parent=sensors, nodeid_str="AMMachine.Sensors.BladeState", browse_name="BladeState", value=0, variant_type=ua.VariantType.Int32, ns_idx=ns)
        collectblade_state = await opc_server.gc_variable(parent=sensors, nodeid_str="AMMachine.Sensors.CollectBladeState", browse_name="CollectBladeState", value=0, variant_type=ua.VariantType.Int32, ns_idx=ns)
        printblade_state = await opc_server.gc_variable(parent=sensors, nodeid_str="AMMachine.Sensors.PrintBladeState", browse_name="PrintBladeState", value=0, variant_type=ua.VariantType.Int32, ns_idx=ns)
        resin_temp = await opc_server.gc_variable(parent=sensors, nodeid_str="AMMachine.Sensors.ResinTemp", browse_name="ResinTemp", value=0.0, variant_type=ua.VariantType.Float, ns_idx=ns)
        resin_level = await opc_server.gc_variable(parent=sensors, nodeid_str="AMMachine.Sensors.ResinLevel", browse_name="ResinLevel", value=0.0, variant_type=ua.VariantType.Float, ns_idx=ns)
        resin_levelstable = await opc_server.gc_variable(parent=sensors, nodeid_str="AMMachine.Sensors.ResinLevelStablity", browse_name="ResinLevelStablity", value=0.0, variant_type=ua.VariantType.Float, ns_idx=ns)
        vat_pres = await opc_server.gc_variable(parent=sensors, nodeid_str="AMMachine.Sensors.VatPres", browse_name="VatPres", value=0.0, variant_type=ua.VariantType.Float, ns_idx=ns)
        uv_ltemp = await opc_server.gc_variable(parent=sensors, nodeid_str="AMMachine.Sensors.UVLTemp", browse_name="UVLTemp", value=0.0, variant_type=ua.VariantType.Float, ns_idx=ns)
        uv_rtemp = await opc_server.gc_variable(parent=sensors, nodeid_str="AMMachine.Sensors.UVRTemp", browse_name="UVRTemp", value=0.0, variant_type=ua.VariantType.Float, ns_idx=ns)

        # handler.py 가 set_value() 를 호출할 때 사용하는 노드 참조 트리 반환
        return {
            "AMMachine": {
                "root": machine,
                "identifier": {
                    "root": identifier,
                    "var": {
                        "manufacturer": manufacturer,
                        "model": model,
                        "serial_number": serial_number
                    }
                },
                "status": {
                    "root": status,
                    "var": {
                        "state": state,
                        "state_text": state_text,
                        "progress": progress,
                        "build_job": build_job,
                        "current_layer": current_layer,
                        "total_layers": total_layers,
                        "remaining_time": remaining_time,
                        "expected_time": totalbuild_time,
                        "start_time": start_time,
                        "end_time": end_time
                    }
                },
                "sensors": {
                    "root": sensors,
                    "var": {
                        "platform_zpos": platform_zpos,
                        "tank_zpos": tank_zpos,
                        "blade_state": blade_state,
                        "collectblade_state": collectblade_state,
                        "printblade_state": printblade_state,
                        "resin_temp": resin_temp,
                        "resin_level": resin_level,
                        "resin_levelstable": resin_levelstable,
                        "vat_pres": vat_pres,
                        "uv_ltemp": uv_ltemp,
                        "uv_rtemp": uv_rtemp
                    }
                }
            }
        }

    except Exception as e:
        print(f"init_opcua exception error: {str(e)}")
        return None


async def main():
    """
    서버 진입점. 수행 순서:
      1. 인증서 유효성 확인 — 없거나 IP/호스트명 불일치 시 자동 재생성
      2. OPC_Server 인스턴스 생성 및 설정
      3. OPC UA Address Space 초기화 (노드 트리 구성)
      4. OPC UA 서버 컨텍스트 진입 (클라이언트 접속 수락 시작)
      5. TCP 내부 서버 시작 — 장비 데이터 수신 루프 진입
    """
    # 인증서를 비동기 루프 시작 전에 동기 함수로 확인·생성한다.
    # RSA 키 생성은 최대 1초 이내로 완료되므로 이벤트 루프 블로킹 허용 범위.
    gen_certs.ensure_server_certs()

    opc_server = OPC_Server(endpoint=END_POINT, application_uri=APPLICATION_URI, namespace=NAMESPACE)

    await opc_server.set_opcserver()

    opc_nodes = await init_opcua(opc_server=opc_server)

    if opc_nodes is None:
        print("OPC SERVER IS DISCONNECTED")
        return

    # 노드 참조 트리를 서버 인스턴스에 등록 → handler.py 가 공유해서 사용
    opc_server.set_nodelist(nodes=opc_nodes)

    async with opc_server.server:
        print(f"OPC UA SERVER STARTED -> ENDPOINT:{opc_server.endpoint} / NAMESPACE: {opc_server.namespace}")

        # TCP 서버 시작 (포트 9000)
        # handler.tcp_handler 에 opc_server 를 클로저로 전달하여 노드 업데이트 가능하게 함
        await tcp_server.start_tcp_server(
            host="0.0.0.0",
            port=9000,
            handler=lambda r, w: handler.tcp_handler(r, w, opc_server=opc_server)
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("OPC UA SERVER IS ENDED")
