"""Vercel Python(서버리스) 진입점.

Vercel은 `/api/*.py`에서 export된 ASGI `app`을 자동 감지해 함수로 감싼다.
프로젝트 루트를 import 경로에 추가해 `webapp.server`(→ `agit`)를 그대로 재사용한다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp.server import app  # noqa: E402  (sys.path 설정 후 import)

# Vercel @vercel/python ASGI 핸들러가 이 `app`을 사용한다.
