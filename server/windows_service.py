import win32serviceutil
import win32service
import servicemanager
import asyncio
import logging
import os
import sys

LOG_FILE = r"C:\carimatec\logs\opcua_service.log"
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


class OpcUaService(win32serviceutil.ServiceFramework):
    _svc_name_ = "OpcUaServer"
    _svc_display_name_ = "OPC UA Server"
    _svc_description_ = "DM400 3D 프린터 OPC UA 서버"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self._loop = None

    def SvcStop(self):
        logging.info("서비스 중지 요청")
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._cancel_all_tasks)

    def _cancel_all_tasks(self):
        for task in asyncio.all_tasks(self._loop):
            task.cancel()

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, '')
        )
        logging.info("서비스 시작")
        self._run()

    def _run(self):
        if getattr(sys, 'frozen', False):
            server_dir = os.path.dirname(sys.executable)
        else:
            server_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.abspath(os.path.join(server_dir, '..', '..', '..'))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)

        os.chdir(server_dir)

        try:
            from GitConn.OPCUA.server import opc_server

            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            logging.info("OPC UA 서버 시작")

            try:
                self._loop.run_until_complete(opc_server.main())
            except asyncio.CancelledError:
                logging.info("서버 태스크 취소")
            except Exception as e:
                logging.error(f"서버 오류: {e}", exc_info=True)
            finally:
                pending = asyncio.all_tasks(self._loop)
                if pending:
                    for task in pending:
                        task.cancel()
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                self._loop.close()

        except ImportError as e:
            logging.error(f"임포트 오류: {e}", exc_info=True)
        except Exception as e:
            logging.error(f"초기화 오류: {e}", exc_info=True)

        logging.info("서비스 종료")


if __name__ == '__main__':
    win32serviceutil.HandleCommandLine(OpcUaService)
