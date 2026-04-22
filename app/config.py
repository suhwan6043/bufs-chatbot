"""
BUFS Academic Chatbot - 설정 관리
환경 변수 및 시스템 설정을 중앙 관리합니다.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

# ── Hugging Face 토큰 자동 적용 ──────────────────────
_hf_token = os.getenv("HF_TOKEN", "")
if _hf_token and not _hf_token.startswith("여기에"):
    os.environ["HUGGING_FACE_HUB_TOKEN"] = _hf_token
    os.environ["HF_TOKEN"] = _hf_token

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


# .env가 OLLAMA_* 네임스페이스를 쓸 수도 있고 LLM_* 네임스페이스를 쓸 수도 있음.
# SSOT를 통일하기 위해 LLM_* 우선 → 없으면 OLLAMA_*로 폴백 → 그래도 없으면 기본값.
# 원칙 4(하드코딩 금지): 모델명·URL은 환경변수 기반으로만 결정.
def _env_llm(primary: str, fallback: str, default: str) -> str:
    return os.getenv(primary) or os.getenv(fallback) or default


@dataclass
class LLMConfig:
    base_url: str = _env_llm("LLM_BASE_URL", "OLLAMA_BASE_URL", "http://localhost:11434")
    model: str = _env_llm("LLM_MODEL", "OLLAMA_MODEL", "gemma4:26b")
    max_tokens: int = int(_env_llm("LLM_MAX_TOKENS", "OLLAMA_NUM_CTX", "2048"))
    temperature: float = float(_env_llm("LLM_TEMPERATURE", "OLLAMA_TEMPERATURE", "0.1"))
    top_p: float = float(_env_llm("LLM_TOP_P", "OLLAMA_TOP_P", "0.9"))
    repeat_penalty: float = float(_env_llm("LLM_REPEAT_PENALTY", "OLLAMA_REPEAT_PENALTY", "1.0"))
    timeout: int = int(_env_llm("LLM_TIMEOUT", "OLLAMA_TIMEOUT", "60"))
    response_cache_ttl_seconds: int = int(os.getenv("LLM_RESPONSE_CACHE_TTL", "3600"))
    response_cache_max_entries: int = int(os.getenv("LLM_RESPONSE_CACHE_MAX_SIZE", "256"))
    # "ollama" → 네이티브 /api/chat (think:false 실제 동작), "openai" → /v1/chat/completions
    api_type: str = os.getenv("LLM_API_TYPE", "openai")


@dataclass
class EmbeddingConfig:
    model_name: str = os.getenv(
        "EMBEDDING_MODEL", "BAAI/bge-m3"
    )
    device: str = os.getenv("EMBEDDING_DEVICE", "cpu")


@dataclass
class RerankerConfig:
    model_name: str = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    device: str = os.getenv("RERANKER_DEVICE", "cpu")
    enabled: bool = os.getenv("RERANKER_ENABLED", "true").lower() == "true"
    top_k: int = int(os.getenv("RERANKER_TOP_K", "10"))
    candidate_k: int = int(os.getenv("RERANKER_CANDIDATE_K", "50"))


@dataclass
class ChromaConfig:
    persist_dir: str = os.getenv("CHROMA_PERSIST_DIR", str(DATA_DIR / "chromadb"))
    collection_name: str = os.getenv("CHROMA_COLLECTION", "bufs_academic")
    distance_metric: str = "cosine"
    n_results: int = int(os.getenv("CHROMA_N_RESULTS", "15"))


@dataclass
class GraphConfig:
    graph_path: str = str(DATA_DIR / "graphs" / "academic_graph.pkl")


@dataclass
class PDFConfig:
    pdf_dir: str = str(DATA_DIR / "pdfs")
    digital_threshold: int = 100  # 페이지당 최소 글자 수 (디지털 판별 기준)
    ocr_batch_size: int = int(os.getenv("OCR_BATCH_SIZE", "4"))
    ocr_dpi: int = int(os.getenv("OCR_DPI", "200"))
    ocr_languages: list = field(default_factory=lambda: ["ko", "en"])


@dataclass
class AppConfig:
    host: str = os.getenv("APP_HOST", "0.0.0.0")
    port: int = int(os.getenv("APP_PORT", "8000"))
    debug: bool = os.getenv("APP_DEBUG", "false").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


@dataclass
class CrawlerConfig:
    # 크롤러 활성화 여부 (기본: 비활성 — .env에서 명시적으로 켜야 함)
    enabled: bool = os.getenv("CRAWLER_ENABLED", "false").lower() == "true"
    # 공지사항 크롤링 주기 (분)
    notice_interval_minutes: int = int(os.getenv("CRAWLER_NOTICE_INTERVAL", "30"))
    # 학사안내 PDF 체크 시각 (시, 0~23)
    guide_cron_hour: int = int(os.getenv("CRAWLER_GUIDE_HOUR", "2"))
    # 수업시간표 PDF 체크 시각 (시, 0~23)
    timetable_cron_hour: int = int(os.getenv("CRAWLER_TIMETABLE_HOUR", "3"))
    # HTTP 요청 타임아웃 (초)
    request_timeout: int = int(os.getenv("CRAWLER_TIMEOUT", "30"))
    # 게시판 목록 최대 순회 페이지 수
    max_pages_per_board: int = int(os.getenv("CRAWLER_MAX_PAGES", "5"))
    # 크롤러 User-Agent
    user_agent: str = os.getenv("CRAWLER_USER_AGENT", "BUFS-CamChat-Bot/1.0")


_ADMIN_PW_DEFAULT = "bufs_admin_2025"   # 절대 프로덕션에서 사용 금지


@dataclass
class AdminConfig:
    # ── 비밀번호 ────────────────────────────────────────────────
    # 반드시 .env 에서 ADMIN_PASSWORD=강력한비밀번호 로 변경하세요.
    # 미설정 시 기본값이 사용되며, 관리자 페이지에 경고 배너가 표시됩니다.
    password: str = os.getenv("ADMIN_PASSWORD", _ADMIN_PW_DEFAULT)

    # ── 브루트포스 방지 ─────────────────────────────────────────
    # 연속 로그인 실패 허용 횟수 (초과 시 세션 잠금)
    max_login_attempts: int = int(os.getenv("ADMIN_MAX_ATTEMPTS", "5"))
    # 잠금 유지 시간 (분)
    lockout_minutes: int = int(os.getenv("ADMIN_LOCKOUT_MINUTES", "15"))

    # ── 세션 타임아웃 ───────────────────────────────────────────
    # 마지막 활동 후 자동 로그아웃 시간 (분)
    session_timeout_minutes: int = int(os.getenv("ADMIN_SESSION_TIMEOUT", "30"))


@dataclass
class AdminFaqConfig:
    """
    관리자 큐레이션 FAQ 피드백 루프 설정.

    원칙 4: 하드코딩 금지 — 거절 문구·임계치·경로 모두 환경변수로 오버라이드.
    """
    # 관리자가 추가한 FAQ 저장 파일 (gitignore, 런타임 추가)
    admin_faq_path: str = os.getenv(
        "ADMIN_FAQ_PATH", str(DATA_DIR / "faq_admin.json")
    )
    # 정식 FAQ 파일 (scripts/ingest_faq.py 와 공유)
    academic_faq_path: str = os.getenv(
        "ACADEMIC_FAQ_PATH", str(DATA_DIR / "faq_academic.json")
    )
    # answer_generator 의 거절 문구 — 미답변 탐지 시그널
    refusal_phrase_ko: str = os.getenv(
        "ADMIN_FAQ_REFUSAL_KO", "관련 정보를 찾을 수 없습니다"
    )
    refusal_phrase_en: str = os.getenv(
        "ADMIN_FAQ_REFUSAL_EN", "couldn't find relevant information"
    )
    # rating <= N 이면 미답변 후보
    uncovered_rating_threshold: int = int(os.getenv("ADMIN_FAQ_RATING_THRESHOLD", "2"))
    # 미답변 질의 그룹핑 자카드 임계치 (관리자 받은편지함 중복 제거)
    cluster_sim_threshold: float = float(os.getenv("ADMIN_FAQ_CLUSTER_SIM", "0.6"))
    # 기존 FAQ 중복 판정 — stem 커버리지 (search_faq direct_answer 와 동일 기준)
    dedup_sim_threshold: float = float(os.getenv("ADMIN_FAQ_DEDUP_SIM", "0.75"))
    # 미답변 스캔 기본 일수
    uncovered_default_days: int = int(os.getenv("ADMIN_FAQ_SCAN_DAYS", "7"))
    # 반환 상한
    uncovered_max_return: int = int(os.getenv("ADMIN_FAQ_MAX_RETURN", "100"))
    # admin FAQ 생성 시 source_question 을 검색면에 포함
    include_source_question_in_chunk: bool = (
        os.getenv("ADMIN_FAQ_INCLUDE_SOURCE_Q", "true").lower() == "true"
    )
    # student_type 기반 FAQ 필터 전역 스위치 (false → 전체 허용, 하위 호환 폴백용)
    student_type_filter_enabled: bool = (
        os.getenv("ADMIN_FAQ_STYPE_FILTER", "true").lower() == "true"
    )
    # 학년 자동계산 기준 학년도 (현재 연도 기본값, .env CURRENT_ACADEMIC_YEAR 로 오버라이드)
    current_academic_year: int = int(os.getenv("CURRENT_ACADEMIC_YEAR", "2026"))


@dataclass
class NotificationConfig:
    """
    로그인 사용자 알림 설정.
    FAQ 이송·수정 시 발송되는 알림 제목·보관 기간을 환경변수로 관리.
    """
    list_limit: int = int(os.getenv("NOTIF_LIST_LIMIT", "50"))
    retention_days: int = int(os.getenv("NOTIF_RETENTION_DAYS", "30"))
    body_max_chars: int = int(os.getenv("NOTIF_BODY_MAX_CHARS", "200"))
    title_answered_ko: str = os.getenv(
        "NOTIF_TITLE_ANSWERED_KO", "학사지원팀이 답변을 정정했습니다"
    )
    title_answered_en: str = os.getenv(
        "NOTIF_TITLE_ANSWERED_EN", "Your question has been answered by the Academic Team"
    )
    title_updated_ko: str = os.getenv(
        "NOTIF_TITLE_UPDATED_KO", "답변이 업데이트되었습니다"
    )
    title_updated_en: str = os.getenv(
        "NOTIF_TITLE_UPDATED_EN", "Answer has been updated"
    )


@dataclass
class PipelineConfig:
    """
    RAG 파이프라인 동작 플래그.
    4원칙(하드코딩 금지) — 모든 임계치·토글은 환경변수로 관리.
    """
    # context_merger._slice_evidence_text: 긴 청크에서 질문 토큰과 매칭되는 줄만 유지.
    # 2026-04-16 A/B 테스트 결과: slicing OFF가 Contains-F1 최선 (balanced +5.1pp, 퇴행 0).
    # 따라서 기본값 OFF. 재활성화 필요 시 EVIDENCE_SLICING_ENABLED=1로 override.
    evidence_slicing_enabled: bool = os.getenv(
        "EVIDENCE_SLICING_ENABLED", "0"
    ).strip().lower() not in ("0", "false", "no")
    # _slice_evidence_text 조건 임계치 (Phase C에서 완화)
    evidence_slicing_min_text_len: int = int(
        os.getenv("EVIDENCE_SLICING_MIN_TEXT_LEN", "1400")
    )
    evidence_slicing_min_sliced_len: int = int(
        os.getenv("EVIDENCE_SLICING_MIN_SLICED_LEN", "500")
    )
    evidence_slicing_context_lines: int = int(
        os.getenv("EVIDENCE_SLICING_CONTEXT_LINES", "2")
    )


@dataclass
class TranscriptRulesConfig:
    """
    학사 리포트 분석 임계치·기본값 (graph 동적 조회 실패 시 fallback).
    4원칙 #4 준수 — 모든 값이 환경변수로 override 가능.
    학사안내 업데이트는 graph 재인제스트로 반영되며, 이 fallback은 안전망.
    """
    # 부족 학점 severity 분기
    shortage_warn_min: float = float(os.getenv("TR_SHORTAGE_WARN_MIN", "0.5"))
    shortage_error_min: float = float(os.getenv("TR_SHORTAGE_ERROR_MIN", "10"))
    # 재수강 후보 기준 성적 (이하)
    retake_grade_threshold: str = os.getenv("TR_RETAKE_GRADE", "B0")
    # 조기졸업 GPA 기준 (graph fallback)
    early_grad_gpa: float = float(os.getenv("TR_EARLY_GRAD_GPA", "3.7"))
    # 총 졸업 학점 fallback
    fallback_graduation_credits: float = float(os.getenv("TR_GRAD_CREDITS_FALLBACK", "130"))
    # 한 학기 수강신청 기본 최대 학점
    fallback_reg_max: int = int(os.getenv("TR_REG_MAX_FALLBACK", "18"))
    # 직전학기 평점 우수 시 확장 학점 상한
    fallback_reg_max_extended: int = int(os.getenv("TR_REG_MAX_EXTENDED", "24"))
    # 우수 평점 기준 (확장 학점 자격)
    excellent_gpa_threshold: float = float(os.getenv("TR_EXCELLENT_GPA", "4.0"))
    # 정규 졸업 학기 수 (조기졸업 판정용)
    normal_semesters: int = int(os.getenv("TR_NORMAL_SEMESTERS", "8"))
    # 조기졸업 가능 최소 학기 수 (6 또는 7)
    early_grad_min_semesters: int = int(os.getenv("TR_EARLY_GRAD_MIN_SEMS", "6"))


@dataclass
class ConversationConfig:
    """
    멀티턴 대화 컨텍스트 설정.

    원칙 2(비용·지연 최적화): follow-up 감지 → 조건부 재작성 → 윈도우 제한 history 주입.
    원칙 4(하드코딩 금지): 모든 임계치·모델명·타임아웃을 환경변수로 오버라이드.
    """
    # ── history injection (생성 단계) ──
    history_enabled: bool = os.getenv("CONV_HISTORY_ENABLED", "true").lower() == "true"
    max_history_turns: int = int(os.getenv("CONV_MAX_HISTORY_TURNS", "2"))
    history_token_budget: int = int(os.getenv("CONV_HISTORY_TOKEN_BUDGET", "500"))

    # ── query rewriting (검색 단계) ──
    rewrite_enabled: bool = os.getenv("CONV_REWRITE_ENABLED", "true").lower() == "true"
    rewrite_model: str = os.getenv("CONV_REWRITE_MODEL", "gemma3:4b")
    # rewriter 전용 LLM 엔드포인트 — 빈 값이면 메인 LLM(settings.llm.base_url)로 폴백.
    # 경량 모델(gemma3:4b)이 Ollama에만 있을 때 Ollama URL로 분리 가능.
    rewrite_base_url: str = os.getenv("CONV_REWRITE_BASE_URL", "")
    rewrite_timeout_sec: float = float(os.getenv("CONV_REWRITE_TIMEOUT_SEC", "0.8"))
    rewrite_max_tokens: int = int(os.getenv("CONV_REWRITE_MAX_TOKENS", "80"))
    rewrite_max_input_turns: int = int(os.getenv("CONV_REWRITE_MAX_INPUT_TURNS", "2"))

    # ── follow-up 감지 ──
    follow_up_max_words: int = int(os.getenv("CONV_FOLLOW_UP_MAX_WORDS", "5"))

    # ── 단턴 쿼리 리라이팅 (recall@5 개선 실험) ──
    single_turn_rewrite_enabled: bool = os.getenv(
        "SINGLE_TURN_REWRITE_ENABLED", "false"
    ).lower() == "true"
    single_turn_rewrite_model: str = os.getenv(
        "SINGLE_TURN_REWRITE_MODEL", os.getenv("CONV_REWRITE_MODEL", "gemma3:4b")
    )
    single_turn_rewrite_timeout_sec: float = float(
        os.getenv("SINGLE_TURN_REWRITE_TIMEOUT_SEC", "0.8")
    )


@dataclass
class ContextBudgetConfig:
    """
    적응형 컨텍스트 예산 설정.

    원칙 1(유연한 스키마 진화): intent별 고정 dict 대신 결과 수 기반 공식으로 스케일.
    원칙 2(비용·지연 최적화): 적게 찾히면 적게, 많이 찾히면 비례 확장.
    원칙 4(하드코딩 금지): 모든 튜닝 상수 환경변수 오버라이드.

    공식: budget(n) = min(base + max(0, min(n, baseline_k+max_extra) - baseline_k) * per_chunk_bonus,
                          base × cap_ratio)
    - n ≤ baseline_k: base 그대로 (현행 동작 유지)
    - n > baseline_k: 청크당 per_chunk_bonus 토큰씩 추가
    - 최대 base × cap_ratio로 상한

    per_chunk_max도 n이 클수록 타이트하게 → 다양성 보장.
    """
    # ── 컴포넌트별 토글 (A/B 평가 · 환각 원인 격리용) ──
    # 팀원 eval에서 확인된 regression(특히 오답 거부율 -23.1pp) 분석을 위해
    # 3개 컴포넌트를 독립적으로 on/off 할 수 있도록 분리.
    # 1) cluster_preserve: pre-RRF 동등권위 클러스터 보존 (볼륨 증가 없음)
    # 2) adaptive_budget:  결과 수 기반 예산 확장
    # 3) fair_share:       다양성 모드 per_chunk_max 타이트화 (truncation 증가)
    # field(default_factory=): 인스턴스 생성 시점에 env 평가 (테스트/런타임 변경 가능)
    cluster_preserve_enabled: bool = field(
        default_factory=lambda: os.getenv("CTX_CLUSTER_PRESERVE", "true").lower() == "true"
    )
    adaptive_budget_enabled: bool = field(
        default_factory=lambda: os.getenv("CTX_ADAPTIVE_BUDGET", "true").lower() == "true"
    )
    fair_share_enabled: bool = field(
        default_factory=lambda: os.getenv("CTX_FAIR_SHARE", "true").lower() == "true"
    )

    # ── 예산 공식 파라미터 ──
    # 결과 수가 이 이하일 땐 base 그대로 (3개까진 원래 설계 유지)
    baseline_chunk_count: int = int(os.getenv("CTX_BASELINE_CHUNK_COUNT", "3"))
    # 청크당 추가 토큰 (평균 청크 ~450자 × TOKENS_PER_CHAR 1.5 = 675 → 1/3 여유)
    per_chunk_bonus: int = int(os.getenv("CTX_PER_CHUNK_BONUS", "225"))
    # n-baseline의 최대 반영 수 (9개 이상은 cap에 걸리도록)
    max_extra_chunks: int = int(os.getenv("CTX_MAX_EXTRA_CHUNKS", "8"))
    # 상한 (base × cap_ratio)
    cap_ratio: float = float(os.getenv("CTX_CAP_RATIO", "2.5"))
    # 다양성 모드 트리거 임계 (n이 이 이상이면 per_chunk_max 타이트하게)
    diversity_trigger_n: int = int(os.getenv("CTX_DIVERSITY_TRIGGER_N", "6"))
    # 다양성 모드에서 단일 청크 최대 글자수 (budget 비례가 아닌 절대값)
    diversity_chunk_cap: int = int(os.getenv("CTX_DIVERSITY_CHUNK_CAP", "500"))

@dataclass
class Settings:
    llm: LLMConfig = field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    reranker: RerankerConfig = field(default_factory=RerankerConfig)
    chroma: ChromaConfig = field(default_factory=ChromaConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    pdf: PDFConfig = field(default_factory=PDFConfig)
    app: AppConfig = field(default_factory=AppConfig)
    admin: AdminConfig = field(default_factory=AdminConfig)
    crawler: CrawlerConfig = field(default_factory=CrawlerConfig)
    admin_faq: AdminFaqConfig = field(default_factory=AdminFaqConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    transcript_rules: TranscriptRulesConfig = field(default_factory=TranscriptRulesConfig)
    conversation: ConversationConfig = field(default_factory=ConversationConfig)
    context_budget: ContextBudgetConfig = field(default_factory=ContextBudgetConfig)


settings = Settings()
