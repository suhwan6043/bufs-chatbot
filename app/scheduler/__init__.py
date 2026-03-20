import streamlit as st

from .crawl_scheduler import CrawlScheduler

__all__ = ["CrawlScheduler", "get_scheduler"]


@st.cache_resource
def get_scheduler() -> CrawlScheduler:
    """
    프로세스 수명 동안 단 하나의 CrawlScheduler 인스턴스를 반환합니다.

    @st.cache_resource 를 모듈 레벨에 직접 적용하여
    함수 객체가 항상 동일 → 캐시 키가 안정적으로 유지됩니다.

    chat_app.py 와 admin.py 모두 동일 함수를 호출하면
    Streamlit이 같은 인스턴스를 반환합니다.

    주의: Streamlit 컨텍스트 외부(테스트 스크립트 등)에서는
          일반 CrawlScheduler()를 직접 생성해 사용하세요.
    """
    scheduler = CrawlScheduler()
    scheduler.start()
    return scheduler
