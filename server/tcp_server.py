"""
TCP 서버 래퍼 (tcp_server.py)

역할:
  - asyncio.start_server 를 감싸서 TCP 서버를 시작하는 단순 유틸리티
  - OPC UA 서버(opc_server.py) 와 같은 이벤트 루프에서 동작

사용 위치:
  opc_server.main() 에서 OPC UA 서버 컨텍스트 안에 호출됨.
  → OPC UA 서버와 TCP 서버가 단일 asyncio 이벤트 루프를 공유
"""

import asyncio


async def start_tcp_server(host, port, handler):
    """
    비동기 TCP 서버를 시작하고 종료될 때까지 대기한다.

    Args:
        host    : 바인딩할 호스트 ("0.0.0.0" 이면 모든 인터페이스)
        port    : 리슨 포트 (기본 9000)
        handler : 클라이언트 접속 시 호출될 코루틴 함수
                  signature: async def handler(reader, writer)
                  opc_server.py 에서 lambda 클로저로 opc_server 를 함께 전달
    """
    try:
        # client_connected_cb 에 핸들러를 등록
        # 클라이언트가 접속할 때마다 handler(reader, writer) 코루틴이 새로 실행됨
        server = await asyncio.start_server(client_connected_cb=handler, host=host, port=port)

        print("TCP Server started on port 9000")
        async with server:
            # 서버가 종료 신호를 받을 때까지 무한 대기
            await server.serve_forever()
    except Exception as e:
        # 포트 충돌, 권한 오류 등 서버 시작 실패 시
        # 현재는 예외를 무시하고 finally 로 이동 (필요 시 로깅 추가 권장)
        pass
    finally:
        print("TCP Server ended on port 9000")
