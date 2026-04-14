import threading

from .crawl_scheduler import CrawlScheduler

__all__ = ["CrawlScheduler", "get_scheduler"]

_scheduler: CrawlScheduler | None = None
_lock = threading.Lock()


def get_scheduler() -> CrawlScheduler:
    """
    프로세스 수명 동안 단 하나의 CrawlScheduler 인스턴스를 반환합니다.
    Streamlit / FastAPI 양쪽에서 동일하게 동작하는 모듈 레벨 싱글톤.
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    with _lock:
        if _scheduler is not None:
            return _scheduler
        _scheduler = CrawlScheduler()
        return _scheduler
