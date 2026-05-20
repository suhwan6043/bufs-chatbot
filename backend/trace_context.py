"""요청 단위 trace_id를 ContextVar로 관리.

목적: 한 채팅 요청이 시스템 전체(router → analyzer → query_router → graph/vector → reranker
→ context_merger → answer_generator → response_validator)를 거치는 동안 모든 logger.info에
같은 trace_id를 자동 prefix하여, `grep <trace_id>` 한 번으로 전체 흐름을 재구성 가능하게 함.

사용법:
    from backend.trace_context import set_trace_id, new_trace_id

    # 라우터 진입부:
    tid = new_trace_id()         # "a1b2c3d4" (8자리 hex)
    set_trace_id(tid)            # ContextVar set
    logger.info("CHAT_START ...")  # 이후 모든 logger 출력에 [a1b2c3d4] prefix 자동 부착

같은 비동기 요청 콘텍스트 내에서는 contextvars 표준 동작에 따라 자동 격리됨 (asyncio task별 분리).
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar


# 요청 단위 trace_id 저장소. default "-"는 "trace_id 없음(서버 startup·백그라운드 잡)"을 의미.
_trace_id_var: ContextVar[str] = ContextVar("camchat_trace_id", default="-")


def new_trace_id() -> str:
    """짧고 식별 가능한 8자리 hex trace_id 생성."""
    return uuid.uuid4().hex[:8]


def set_trace_id(tid: str) -> None:
    """현재 요청 컨텍스트의 trace_id 설정."""
    _trace_id_var.set(tid)


def get_trace_id() -> str:
    """현재 요청의 trace_id 반환 (없으면 '-')."""
    return _trace_id_var.get()


class TraceFilter(logging.Filter):
    """모든 LogRecord에 trace_id 속성 자동 주입.

    logging format에 `%(trace_id)s`를 포함시키면 자동으로 [트레이스 ID] prefix 출력됨.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id_var.get()
        return True
