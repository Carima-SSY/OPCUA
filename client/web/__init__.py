"""
web 패키지 초기화.
client/ 디렉터리를 sys.path 에 추가하여 config, client, handler 등을 import 가능하게 한다.
"""

import sys
from pathlib import Path

_CLIENT_DIR = Path(__file__).parent.parent
if str(_CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(_CLIENT_DIR))
