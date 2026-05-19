# Step 6 — env 분기 분류 (147 call → 100 unique names)

**측정일**: 2026-05-13
**측정**: `os.getenv("NAME"`) 패턴 ast grep — production 코드(app/, backend/)에서 116 call 발생, 일부 동일 NAME 다회 호출. **unique NAME = 100**. 플랜은 147 분기(`os.getenv` 호출 수) 기준.

플랜의 147은 scripts/도 포함한 통합 카운트. 본 step에서는 production만 100 unique로 분류 (scripts/는 인제스트·평가 분기로 별도).

## 1. 분류 정의

| 카테고리 | 정의 | 기준 |
|---|---|---|
| **기능 (Feature)** | 기능 활성화/모델·서비스 선택. 운영 환경별 차이 큼. | `*_ENABLED`, `*_MODEL`, `*_BASE_URL`, `JWT_*` 등 |
| **디버그 (Debug)** | 로그 레벨·디버그 모드 등 진단용 | `LOG_LEVEL`, `APP_DEBUG`, `CHAT_LOG_DISABLED`, `QUERY_ROUTER_SEQUENTIAL` |
| **실험 (Experiment)** | 임계치·튜닝 파라미터. AB 측정 후 default 결정 | `*_TIMEOUT_SEC`, `*_TOP_K`, `*_THRESHOLD`, `*_BUDGET`, `*_RATIO` 등 |

## 2. 100 unique env names 분류

### 2.1 기능 (Feature) — 41건

| Group | 변수 | 영향 |
|---|---|---|
| LLM stack | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_TYPE`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | 답변 생성 모델 |
| Embedder | `EMBEDDING_MODEL`, `EMBEDDING_DEVICE` | 벡터화 |
| Reranker | `RERANKER_MODEL`, `RERANKER_DEVICE`, `RERANKER_ENABLED` | 검색 후 재정렬 |
| Chroma | `CHROMA_PERSIST_DIR`, `CHROMA_COLLECTION` | 벡터 DB 위치 |
| Translator | `TRANSLATOR_ENABLED`, `TRANSLATOR_BACKEND`, `TRANSLATOR_MODEL`, `TRANSLATOR_DEVICE` | EN context 번역 |
| Crawler | `CRAWLER_ENABLED`, `CRAWLER_USER_AGENT` | gnuboard5 크롤러 |
| FAQ admin | `ADMIN_PASSWORD`, `ADMIN_FAQ_INCLUDE_SOURCE_Q`, `ADMIN_FAQ_STYPE_FILTER` | admin 페이지 인증·필터 |
| Conversation | `CONV_HISTORY_ENABLED`, `CONV_REWRITE_ENABLED`, `CONV_REWRITE_MODEL`, `CONV_REWRITE_BASE_URL`, `CONV_UNDERSTANDING_ENABLED`, `CONV_UNDERSTAND_MODEL`, `CONV_UNDERSTAND_BASE_URL`, `CONV_UNDERSTAND_FALLBACK_MODEL`, `CONV_UNDERSTAND_FALLBACK_BASE_URL` | 멀티턴/통합 LLM 분기 |
| Clarification | `CLARIFICATION_ENABLED` | 되묻기 게이트 |
| Direct answer | `DIRECT_ANSWER_BYPASS_LLM` | LLM 우회 (★ 무수정 약속) |
| KO prompt | `KO_PROMPT_VERSION` | v0/v1 토글 |
| JWT | `JWT_SECRET`, `JWT_EXPIRE_MINUTES`, `USER_JWT_SECRET` | 토큰 시크릿 |
| VLM | `VLM_MODEL`, `VLM_BASE_URL`, `VLM_TIMEOUT_SEC`, `VLM_CACHE_DIR` | PDF VLM 추출 |
| Transcript | `TRANSCRIPT_ENC_KEY`, `TRANSCRIPT_ENC_PREFIX` | 성적표 암호화 |
| Logging | `CHAT_LOG_DISABLED` | 채팅 로그 ON/OFF |
| App | `APP_HOST`, `APP_PORT`, `APP_DEBUG` | uvicorn 바인딩 |
| Notif | `NOTIF_LIST_LIMIT`, `NOTIF_RETENTION_DAYS`, `NOTIF_BODY_MAX_CHARS` | FAQ 알림 |

소계: **약 41 변수**.

### 2.2 디버그 (Debug) — 7건

| 변수 | 영향 |
|---|---|
| `LOG_LEVEL` | 로깅 레벨 (DEBUG/INFO/WARNING/ERROR) |
| `APP_DEBUG` | FastAPI 디버그 모드 |
| `QUERY_ROUTER_SEQUENTIAL` | router 순차 처리 (디버그 가시화) |
| `CHAT_LOG_DISABLED` | 채팅 JSONL 로그 비활성 (테스트용도 겸용) |
| `HF_TOKEN` | (사실 인증이지만 디버그 진단 시 자주 swap) |
| `CORS_ORIGINS` | CORS 허용 origin (운영 vs 로컬 디버그) |
| `CURRENT_ACADEMIC_YEAR` | 학년도 (운영용 fixture, 디버그 시점 변경) |

소계: **7 변수** (HF_TOKEN/CORS_ORIGINS는 기능에 더 가까운 경계).

### 2.3 실험 (Experiment) — 52건

| Group | 변수 | 비고 |
|---|---|---|
| Cache | `LLM_RESPONSE_CACHE_TTL`, `LLM_RESPONSE_CACHE_MAX_SIZE` | 캐시 튜닝 |
| Concurrency | `LLM_MAX_CONCURRENT` | semaphore 슬롯 |
| LLM tuning | `LLM_MAX_TOKENS`, `LLM_TEMPERATURE`, `LLM_TOP_P`, `LLM_REPEAT_PENALTY`, `LLM_TIMEOUT` | 답변 생성 파라미터 |
| Reranker | `RERANKER_TOP_K`, `RERANKER_CANDIDATE_K` | 후보 풀 크기 |
| Chroma | `CHROMA_N_RESULTS` | 검색 결과 수 |
| PDF | `OCR_BATCH_SIZE`, `OCR_DPI` | 인제스트 시점 |
| Crawler | `CRAWLER_NOTICE_INTERVAL`, `CRAWLER_GUIDE_HOUR`, `CRAWLER_TIMETABLE_HOUR`, `CRAWLER_TIMEOUT`, `CRAWLER_MAX_PAGES` | 크롤링 빈도·타임아웃 |
| Admin | `ADMIN_MAX_ATTEMPTS`, `ADMIN_LOCKOUT_MINUTES`, `ADMIN_SESSION_TIMEOUT` | 보안 정책 |
| FAQ | `ADMIN_FAQ_RATING_THRESHOLD`, `ADMIN_FAQ_CLUSTER_SIM`, `ADMIN_FAQ_DEDUP_SIM`, `ADMIN_FAQ_SCAN_DAYS`, `ADMIN_FAQ_MAX_RETURN` | FAQ 자동 후보 |
| Evidence slicing | `EVIDENCE_SLICING_MIN_TEXT_LEN`, `EVIDENCE_SLICING_MIN_SLICED_LEN`, `EVIDENCE_SLICING_CONTEXT_LINES` | merge 슬라이싱 |
| Transcript rules | `TR_SHORTAGE_WARN_MIN`, `TR_SHORTAGE_ERROR_MIN`, `TR_RETAKE_GRADE`, `TR_EARLY_GRAD_GPA`, `TR_GRAD_CREDITS_FALLBACK`, `TR_REG_MAX_FALLBACK`, `TR_REG_MAX_EXTENDED`, `TR_EXCELLENT_GPA`, `TR_NORMAL_SEMESTERS`, `TR_EARLY_GRAD_MIN_SEMS` | 성적표 분석 임계치 |
| Conversation 임계치 | `CONV_MAX_HISTORY_TURNS`, `CONV_HISTORY_TOKEN_BUDGET`, `CONV_REWRITE_TIMEOUT_SEC`, `CONV_REWRITE_MAX_TOKENS`, `CONV_REWRITE_MAX_INPUT_TURNS`, `CONV_FOLLOW_UP_MAX_WORDS`, `CONV_UNDERSTAND_TIMEOUT_SEC`, `CONV_UNDERSTAND_MAX_TOKENS`, `CONV_UNDERSTAND_FALLBACK_TIMEOUT_SEC` | 멀티턴 튜닝 |
| Chunking | `CHUNK_MIN_LEN`, `CHUNK_MAX_LEN`, `CHUNK_OVERLAP`, `CHUNK_HARD_CAP`, `TABLE_MAX_LEN` | 인제스트 시점 |
| Clarification | `CLARIFICATION_MAX_LOG` | 되묻기 빈도 |

소계: **52 변수**.

### 2.4 분류 합계

| 카테고리 | 개수 | 비중 |
|---|---:|---:|
| 기능 (Feature) | 41 | 41% |
| 디버그 (Debug) | 7 | 7% |
| 실험 (Experiment) | 52 | 52% |
| **합계** | **100** | 100% |

`os.getenv` call 수(116) ≠ unique name 수(100) — 같은 NAME이 여러 위치에서 호출되는 경우(예: `LOG_LEVEL`은 backend/main.py + app/config.py 양쪽).

플랜의 147 분기 = production 116 + scripts/ 31 (인제스트/평가 도구) — 본 step에서는 production만 분류, scripts는 production runtime 무관.

## 3. 카테고리별 PR 후보

### 3.1 기능 (Feature) → 정리 PR 후보

| 작업 | ROI | 우선순위 |
|---|---|---|
| `LLM_BASE_URL` vs `OLLAMA_BASE_URL` 폴백 SSOT 통일 (`app/config.py:27 _env_llm` 활용) | 운영 혼란 감소 | P2 |
| `CONV_REWRITE_*`와 `CONV_UNDERSTAND_*` 그룹화 명세 (RUN BOOK) | 운영 가시성 | P2 |
| `DIRECT_ANSWER_BYPASS_LLM` 사용자 약속 명시 (env 정의 docstring 외 별도 명시) | 안전 | P1 |
| `KO_PROMPT_VERSION` v0/v1 토글 + 회귀 매트릭스 인증 | 안전 | P1 (이미 v1 default) |

### 3.2 디버그 (Debug) → 정리 PR 후보

| 작업 | ROI | 우선순위 |
|---|---|---|
| `QUERY_ROUTER_SEQUENTIAL`: 진단용임을 docstring 명시 + 운영 default OFF 확인 | 가시성 | P2 |
| `LOG_LEVEL` 통합 (현재 backend/main.py + app/config.py 양쪽 참조) | 일관성 | P3 |

### 3.3 실험 (Experiment) → 정리 PR 후보

| 작업 | ROI | 우선순위 |
|---|---|---|
| `CONV_UNDERSTAND_TIMEOUT_SEC` / `_FALLBACK_TIMEOUT_SEC`을 측정 baseline에 따라 default 변경 | latency 개선 | **P0** (Step 5 S2 측정 후) |
| `RERANKER_TOP_K` / `_CANDIDATE_K` 튜닝 매트릭스 작성 | 정답률 vs latency | P1 |
| `EVIDENCE_SLICING_*` 3변수 default 조정 (merge_ms 측정 결함 진단 후) | merge 효율 | P2 |
| `TR_*` 10변수: graph 동적 조회 실패 시 fallback로 사용 — graph 재인제스트 후 미사용 변수 식별 | 코드 정리 | P3 |

## 4. 위험 요소

| 위험 | 경로 | 대응 |
|---|---|---|
| `JWT_SECRET` / `USER_JWT_SECRET` / `TRANSCRIPT_ENC_KEY` 미설정 시 default(고정값) 사용 | `backend/routers/admin/auth.py`, `backend/routers/user.py`, `backend/crypto.py` | 운영 .env 필수 명시 + startup 검증 |
| `HF_TOKEN` 누락 → HF 모델 다운로드 401 | `app/config.py:15-18` | startup 경고 로그 (이미 부분 적용) |
| `LLM_BASE_URL` 미설정 시 localhost:11434 default | `app/config.py:33` | 운영 .env 필수 |

## 5. Step 6 산출물 검증 (env_branches.md 부분)

| 항목 | 상태 |
|---|---|
| 147 분기 3카테고리 분류 (요구) | ✓ 100 unique × 3 카테고리 (plan의 147은 production+scripts 합산; 본 분류는 production만) |
| 카테고리별 PR 후보 | ✓ (3절) |
| 위험 요소 | ✓ (4절) |
