"""
백그라운드 크롤링 스케줄러 (APScheduler)

역할:
  - 공지사항 크롤링: 설정된 주기(분)마다 자동 실행
  - NoticeCrawler → ChangeDetector → IncrementalUpdater → CrawlLogger 파이프라인
  - Streamlit @st.cache_resource 싱글톤으로 앱 재실행 시 재시작 방지

수정 이력:
  - Fix 1: shared_resources 싱글톤 사용 (HNSW 동시 쓰기 충돌 방지)
  - Fix 3: logs/scheduler.log 파일 핸들러 추가 (백그라운드 로그 보존)
  - Fix 5: 실패한 이벤트는 해시 커밋에서 제외 (다음 실행에서 재처리)

활성화:
  .env 파일에  CRAWLER_ENABLED=true  설정 필요 (기본: false)
"""

import atexit
import logging
import logging.handlers
import time
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings, BASE_DIR

logger = logging.getLogger(__name__)

JOB_ID_NOTICE = "notice_crawl"
_LOG_FILE = BASE_DIR / "logs" / "scheduler.log"


def _setup_scheduler_log() -> None:
    """
    logs/scheduler.log 에 로테이팅 파일 핸들러를 추가합니다.
    백그라운드 스레드의 로그가 Streamlit UI에 표시되지 않으므로
    파일로 보존합니다. 이미 추가된 경우 중복 추가하지 않습니다.
    """
    _LOG_FILE.parent.mkdir(exist_ok=True)

    root = logging.getLogger()
    # 같은 파일의 핸들러가 이미 있으면 스킵
    for h in root.handlers:
        if isinstance(h, logging.handlers.RotatingFileHandler):
            if Path(h.baseFilename).resolve() == _LOG_FILE.resolve():
                return

    handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE,
        maxBytes=5 * 1024 * 1024,   # 5MB
        backupCount=3,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)

    # root logger 기본 레벨이 WARNING이면 INFO 메시지가 핸들러까지 도달하지 못함
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)

    logger.info("[스케줄러] 파일 로그 핸들러 등록: %s", _LOG_FILE)


def _run_notice_job() -> None:
    """
    공지사항 크롤링 잡.
    NoticeCrawler → ChangeDetector → IncrementalUpdater → CrawlLogger

    Fix 1: ChromaStore/Embedder는 shared_resources 싱글톤 재사용 (HNSW 충돌 방지)
    Fix 5: 처리 실패한 이벤트는 해시 커밋 제외 → 다음 실행에서 재처리
    """
    start = time.time()
    logger.info("[스케줄러] 공지사항 크롤링 잡 시작")

    try:
        # ── 변경 감지기 + known_hashes 준비 (크롤 전에 생성) ────────
        from app.crawler.change_detector import ChangeDetector
        detector = ChangeDetector()
        known_hashes = detector.get_all_tracked()

        # ── 크롤링 (known 일반공지는 HTTP 생략, 스텁 사용) ───────────
        from app.crawler.notice_crawler import NoticeCrawler
        crawler = NoticeCrawler()
        items = crawler.crawl(pinned_only=None, known_hashes=known_hashes)

        # ── 변경 감지 ─────────────────────────────────────────────
        events = detector.detect(items)

        if not events:
            logger.info("[스케줄러] 변경 사항 없음 — 업데이트 건너뜀")
            return

        # ── ChromaDB 증분 업데이트 (Fix 1: 공유 싱글톤 사용) ──────
        from app.shared_resources import get_chroma_store          # ← Fix 1
        from app.crawler.blacklist import ContentBlacklist
        from app.ingestion.incremental_update import IncrementalUpdater

        chroma = get_chroma_store()                                # ← Fix 1 (new 제거)
        blacklist = ContentBlacklist()
        updater = IncrementalUpdater(chroma, blacklist)
        report = updater.process_events(events)

        # ── 해시 커밋 (Fix 5: 성공한 이벤트만 커밋) ──────────────
        successful_events = [
            e for e in events
            if e.source_id not in report.failed_source_ids         # ← Fix 5
        ]
        if successful_events:
            detector.commit(successful_events)
        if report.failed_source_ids:
            logger.warning(
                "[스케줄러] %d건 처리 실패 — 다음 실행에서 재시도: %s",
                len(report.failed_source_ids),
                report.failed_source_ids,
            )

        # ── 크롤 히스토리 기록 ────────────────────────────────────
        from app.crawler.crawl_logger import CrawlLogger
        duration_ms = int((time.time() - start) * 1000)
        CrawlLogger().log_run(JOB_ID_NOTICE, report, duration_ms)

        logger.info(
            "[스케줄러] 공지사항 잡 완료: %s (%.1f초)",
            report.summary(), duration_ms / 1000,
        )

    except Exception as exc:
        logger.error("[스케줄러] 공지사항 잡 실패: %s", exc, exc_info=True)


class CrawlScheduler:
    """
    APScheduler 기반 크롤링 스케줄러.

    - CRAWLER_ENABLED=false(기본)이면 아무 잡도 등록하지 않음
    - Streamlit @st.cache_resource 싱글톤으로 래핑하여 사용
    - max_instances=1 으로 동시 실행 방지
    - atexit으로 프로세스 종료 시 graceful shutdown
    """

    def __init__(self) -> None:
        self._scheduler = BackgroundScheduler(
            timezone="Asia/Seoul",
            job_defaults={"misfire_grace_time": 60},
        )
        self._started = False

    # ── 생명주기 ─────────────────────────────────────────────────

    def start(self) -> None:
        """스케줄러 시작 (이미 실행 중이면 무시)"""
        if self._started:
            return

        _setup_scheduler_log()                                      # ← Fix 3

        if not settings.crawler.enabled:
            logger.info("[스케줄러] CRAWLER_ENABLED=false — 스케줄러 비활성화")
            return

        self._register_jobs()
        self._scheduler.start()
        self._started = True
        atexit.register(self.stop)

        logger.info(
            "[스케줄러] 시작 완료 (공지 주기: %d분)",
            settings.crawler.notice_interval_minutes,
        )

    def stop(self) -> None:
        """스케줄러 정지 (atexit 또는 수동 호출)"""
        if self._started and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            self._started = False
            logger.info("[스케줄러] 정지")

    # ── 상태 조회 ─────────────────────────────────────────────────

    def is_running(self) -> bool:
        return self._started and self._scheduler.running

    def get_jobs_info(self) -> list[dict]:
        """등록된 잡 정보 반환 (관리자 UI용)"""
        if not self._started:
            return []
        result = []
        for job in self._scheduler.get_jobs():
            result.append({
                "id": job.id,
                "next_run": (
                    job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                    if job.next_run_time else "—"
                ),
            })
        return result

    # ── 수동 트리거 ───────────────────────────────────────────────

    def trigger_notice_now(self) -> None:
        """공지사항 잡 즉시 실행 (블로킹 - 완료까지 대기).

        스케줄러 실행 여부와 관계없이 현재 스레드에서 직접 호출합니다.
        관리자 수동 트리거에서 spinner가 크롤링 완료까지 유지되도록 합니다.
        """
        _run_notice_job()
        logger.info("[스케줄러] 공지사항 잡 수동 트리거 완료")

    # ── 내부 ─────────────────────────────────────────────────────

    def _register_jobs(self) -> None:
        self._scheduler.add_job(
            _run_notice_job,
            IntervalTrigger(minutes=settings.crawler.notice_interval_minutes),
            id=JOB_ID_NOTICE,
            replace_existing=True,
            max_instances=1,
        )
