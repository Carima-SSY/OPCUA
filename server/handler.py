"""
TCP 메시지 핸들러 (handler.py)

역할:
  - TCP 클라이언트(장비 DM400)로부터 CSV 형식 메시지를 수신
  - 메시지 키워드에 따라 OPC UA 노드 값을 비동기로 업데이트

메시지 프로토콜 (줄바꿈 구분, CSV 형식):
  IDENTIFIER,<제조사>,<모델>,<시리얼번호>
  STATUS,<state>,<state_text>,<progress>,<build_job>,<current_layer>,
         <total_layers>,<remaining_time>,<expected_time>,<start_time>,<end_time>
  SENSORS,<platform_zpos>,<tank_zpos>,<blade_state>,<collectblade_state>,
          <printblade_state>,<resin_temp>,<resin_level>,<resin_levelstable>,
          <vat_pres>,<uv_ltemp>,<uv_rtemp>

연결 방식:
  - asyncio.StreamReader / StreamWriter 기반 비동기 TCP
  - 클라이언트 1개 연결당 하나의 tcp_handler 코루틴이 실행됨
  - 연결 종료 시 writer 를 안전하게 닫고 루프 종료
"""

import asyncio
import GitConn.OPCUA.server.opc_server as opc_server
from asyncua import ua


async def tcp_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, opc_server: opc_server.OPC_Server):
    """
    장비(DM400)와의 TCP 연결을 처리하는 비동기 핸들러.

    tcp_server.start_tcp_server() 의 client_connected_cb 로 등록된다.
    opc_server 는 클로저(lambda)를 통해 전달받으며, OPC UA 노드 참조 트리에 접근한다.

    Args:
        reader     : 장비로부터 데이터를 읽는 스트림
        writer     : 장비로 데이터를 보내는 스트림 (현재는 수신 전용으로 사용)
        opc_server : OPC_Server 인스턴스 (nodelists 참조)
    """
    addr = writer.get_extra_info("peername")  # 접속한 클라이언트 IP:Port

    # opc_server.nodelists 는 init_opcua() 가 반환한 노드 참조 트리
    # {"AMMachine": {"identifier": {...}, "status": {...}, "sensors": {...}}}
    nodes = opc_server.nodelists
    print(f"[TCP] Connected: {addr}")

    try:
        while True:
            # 줄바꿈(\n) 단위로 한 메시지씩 읽음 (readline 은 \n 포함해서 반환)
            data = await reader.readline()

            # 빈 데이터 = 클라이언트가 연결을 끊은 경우 (EOF)
            if not data:
                print('INVALID DATA - CLIENT IS DISCONNECTED')
                break

            # 바이트 디코딩 후 앞뒤 공백·줄바꿈 제거
            msg = data.decode().strip()
            print(f"MESSAGE: {msg}")

            # CSV 분리 후 첫 번째 필드를 키워드로 사용
            msg_list = msg.split(',')
            keyword = msg_list[0]

            # ── IDENTIFIER 메시지 처리 ─────────────────────────────────────
            # 형식: IDENTIFIER,<제조사>,<모델>,<시리얼번호>
            # 필드 수: 최소 4개 (키워드 포함)
            if keyword == "IDENTIFIER":
                if len(msg_list) < 4:
                    print(f"[WARN] IDENTIFIER message field count insufficient: {msg}")
                    continue
                print("UPDATE IDENTIFIER")
                id_var = nodes["AMMachine"]["identifier"]["var"]
                # asyncio.gather 로 세 노드를 동시에 업데이트 (순차 대비 성능 향상)
                await asyncio.gather(
                    id_var["manufacturer"].set_value(ua.Variant(msg_list[1], ua.VariantType.String)),
                    id_var["model"].set_value(ua.Variant(msg_list[2], ua.VariantType.String)),
                    id_var["serial_number"].set_value(ua.Variant(msg_list[3], ua.VariantType.String)),
                )

            # ── STATUS 메시지 처리 ─────────────────────────────────────────
            # 형식: STATUS,<state>,<state_text>,<progress>,<build_job>,
            #              <current_layer>,<total_layers>,<remaining_time>,
            #              <expected_time>,<start_time>,<end_time>
            # 필드 수: 최소 11개 (키워드 포함)
            elif keyword == "STATUS":
                if len(msg_list) < 11:
                    print(f"[WARN] STATUS message field count insufficient: {msg}")
                    continue
                print("UPDATE STATUS")
                st_var = nodes["AMMachine"]["status"]["var"]
                await asyncio.gather(
                    st_var["state"].set_value(ua.Variant(int(msg_list[1]), ua.VariantType.Int32)),
                    st_var["state_text"].set_value(ua.Variant(msg_list[2], ua.VariantType.String)),
                    st_var["progress"].set_value(ua.Variant(int(msg_list[3]), ua.VariantType.Int32)),
                    st_var["build_job"].set_value(ua.Variant(msg_list[4], ua.VariantType.String)),
                    st_var["current_layer"].set_value(ua.Variant(int(msg_list[5]), ua.VariantType.Int32)),
                    st_var["total_layers"].set_value(ua.Variant(int(msg_list[6]), ua.VariantType.Int32)),
                    st_var["remaining_time"].set_value(ua.Variant(int(msg_list[7]), ua.VariantType.Int32)),
                    st_var["expected_time"].set_value(ua.Variant(int(msg_list[8]), ua.VariantType.Int32)),
                    st_var["start_time"].set_value(ua.Variant(int(msg_list[9]), ua.VariantType.Int32)),
                    st_var["end_time"].set_value(ua.Variant(int(msg_list[10]), ua.VariantType.Int32)),
                )

            # ── SENSORS 메시지 처리 ────────────────────────────────────────
            # 형식: SENSORS,<platform_zpos>,<tank_zpos>,<blade_state>,
            #               <collectblade_state>,<printblade_state>,<resin_temp>,
            #               <resin_level>,<resin_levelstable>,<vat_pres>,
            #               <uv_ltemp>,<uv_rtemp>
            # 필드 수: 최소 12개 (키워드 포함)
            elif keyword == "SENSORS":
                if len(msg_list) < 12:
                    print(f"[WARN] SENSORS message field count insufficient: {msg}")
                    continue
                print("UPDATE SENSORS")
                s_var = nodes["AMMachine"]["sensors"]["var"]
                await asyncio.gather(
                    s_var["platform_zpos"].set_value(ua.Variant(float(msg_list[1]), ua.VariantType.Float)),
                    s_var["tank_zpos"].set_value(ua.Variant(float(msg_list[2]), ua.VariantType.Float)),
                    s_var["blade_state"].set_value(ua.Variant(int(msg_list[3]), ua.VariantType.Int32)),
                    s_var["collectblade_state"].set_value(ua.Variant(int(msg_list[4]), ua.VariantType.Int32)),
                    s_var["printblade_state"].set_value(ua.Variant(int(msg_list[5]), ua.VariantType.Int32)),
                    s_var["resin_temp"].set_value(ua.Variant(float(msg_list[6]), ua.VariantType.Float)),
                    s_var["resin_level"].set_value(ua.Variant(float(msg_list[7]), ua.VariantType.Float)),
                    s_var["resin_levelstable"].set_value(ua.Variant(float(msg_list[8]), ua.VariantType.Float)),
                    s_var["vat_pres"].set_value(ua.Variant(float(msg_list[9]), ua.VariantType.Float)),
                    s_var["uv_ltemp"].set_value(ua.Variant(float(msg_list[10]), ua.VariantType.Float)),
                    s_var["uv_rtemp"].set_value(ua.Variant(float(msg_list[11]), ua.VariantType.Float)),
                )

            else:
                # 알 수 없는 키워드는 경고만 출력하고 루프 유지
                print(f"[WARN] Unknown keyword: {keyword}")

    # ── 예외 처리 ──────────────────────────────────────────────────────────
    # 각 예외를 별도로 처리하여 원인을 구체적으로 로깅

    except ConnectionResetError:
        # 클라이언트가 TCP RST 를 보내며 강제 종료한 경우
        print(f"[TCP ERROR] Connection reset by client: {addr}")

    except BrokenPipeError:
        # 연결이 끊긴 소켓에 쓰기 시도 시 발생
        print(f"[TCP ERROR] Broken pipe: {addr}")

    except OSError as e:
        # 소켓 관련 OS 레벨 오류
        print(f"[TCP ERROR] OS error: {addr} | {e}")

    except ValueError as e:
        # int() / float() 변환 실패 — 잘못된 메시지 형식
        print(f"[TCP ERROR] Value parsing error: {addr} | {e}")

    except Exception as e:
        print(f"[TCP ERROR] {e}")

    finally:
        # 정상/비정상 종료 모두 writer 를 닫아 소켓 리소스 해제
        try:
            writer.close()
            await writer.wait_closed()
        except Exception as e:
            print(f"[TCP] Writer close error: {e}")

        print(f"[TCP] Disconnected: {addr}")
