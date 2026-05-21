# LLM 파이프라인 전체 구조

**기준**: `refactor/llm-pipeline-overhaul` (origin/main 10de3dc, PR #25 머지 후)
**작성일**: 2026-05-21

본 문서는 main 운영 환경에 실제 배포된 LLM 관련 모든 모듈·호출 지점·설정·외부 서비스의 정적 구조를 정리한 청사진입니다. PR #24·#26은 포함 안 됨.

---

## 1. 요청 라이프사이클 (개관)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  사용자 요청  GET/POST /api/chat[/stream]?session_id&question                │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  backend/routers/chat.py — Stream(SSE) 또는 Sync 분기                       │
│                                                                              │
│  Stage 1: CHAT_START + language_detector                                    │
│  Stage 2: follow_up_detector.detect()          (룰, <1ms)                   │
│  Stage 3: [조건부] query_rewriter.rewrite()    (LLM L3, 최대 0.8s)          │
│  Stage 4: query_analyzer.analyze()             (룰, ~100ms incl. embedding) │
│  Stage 5: query_router.route_and_search()      (검색, 평균 7s)               │
│            ├─ ChromaDB(M1) + BM25 + GraphDB                                  │
│            └─ reranker(M2) CrossEncoder                                      │
│  Stage 6: context_merger.merge()               (인메모리, ~10ms)            │
│            ├─ direct_answer 채택 (rerank gate 0.20, AnswerUnit gate)        │
│            └─ confidence 산정                                                │
│  Stage 7: [조건부 confidence<0.3] answer_generator.rewrite_query() (LLM L2) │
│            └─ retry route_and_search() + 재머지                              │
│  Stage 8: [path 분기]                                                        │
│            ├─ direct_answer 경로: LLM 우회, 즉시 반환                        │
│            └─ generate 경로: answer_generator.generate() (LLM L1, 평균 5s)  │
│  Stage 9: [조건부] translator.translate_if_needed() (lang≠ko, LLM L4)       │
│  Stage 10: response_validator.validate()        (룰)                        │
│  Stage 11: CHAT_END + transcript 저장                                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
                       SSE stream 또는 JSON
```

---

## 2. LLM·모델 호출 인벤토리

### 2.1 LLM 호출 5개

| ID | 위치 | 역할 | 모델 | Timeout | 캐싱 | 발동률 (app.log) |
|---|---|---|---|---|---|---|
| **L1** | [answer_generator.generate()](app/pipeline/answer_generator.py#L837) | 메인 답변 (스트리밍) | `qwen3:8b` (env) | 60s | ✅ TTL 1h | 83.6% (122/146) |
| **L2** | [answer_generator.rewrite_query()](app/pipeline/answer_generator.py#L709) | 저신뢰 재시도 쿼리 재작성 | 메인 LLM | 5s ⚠️하드코딩 | ❌ | **0% (dead code 의심)** |
| **L3** | [query_rewriter.rewrite()](app/pipeline/query_rewriter.py#L312) | follow-up 쿼리 재작성 | `gemma3:4b` (env) | 0.8s | ❌ | 0% (no follow-up 트래픽) |
| **L4** | [translator.translate_if_needed()](app/pipeline/translator.py#L126) | KO→EN/JA 컨텍스트 번역 | M2M-100 or `qwen2.5:7b` | 120s | ❌ | 0% (KO-only) |
| **L5** | [answer_generator.health_check()](app/pipeline/answer_generator.py#L1150) | LLM heartbeat | 메인 LLM | 5s ⚠️하드코딩 | ❌ | 시작 시 1회 |

### 2.2 모델 추론 2개 (in-process 싱글톤)

| ID | 위치 | 모델 | Device | 발동 |
|---|---|---|---|---|
| **M1** | [shared_resources.get_embedder()](app/shared_resources.py#L41) | `BAAI/bge-m3` | env (default cpu) | 매 검색 |
| **M2** | [shared_resources.get_reranker_model()](app/shared_resources.py#L98) | `BAAI/bge-reranker-v2-m3` CrossEncoder | env (default cpu) | 매 검색 |

### 2.3 빌드 타임 (런타임 영향 없음)

| ID | 위치 | 역할 |
|---|---|---|
| **V1** | [vlm_extractor.py](app/pdf/vlm_extractor.py) | 인제스트용 PDF 표 추출 VLM (`qwen2.5vl:7b`) |

---

## 3. 모듈 카탈로그

### 3.1 핵심 파이프라인 모듈 (`app/pipeline/`)

| 파일 | LoC | LLM | 역할 |
|---|---|---|---|
| [follow_up_detector.py](app/pipeline/follow_up_detector.py) | 152 | ❌ | 룰 기반 follow-up 감지 |
| [query_rewriter.py](app/pipeline/query_rewriter.py) | 363 | L3 | follow-up 쿼리 재작성 (Stage 2 룰 + Stage 3 LLM) |
| [query_analyzer.py](app/pipeline/query_analyzer.py) | 1,189 | M1 | intent·entity·qtype 분류 (룰 + embedding) |
| [query_router.py](app/pipeline/query_router.py) | (확인) | M1·M2 | 3-engine 병렬 검색 + RRF + rerank |
| [reranker.py](app/pipeline/reranker.py) | (확인) | M2 | CrossEncoder 재순위화 + tier boost |
| [context_merger.py](app/pipeline/context_merger.py) | 1,000 | ❌ | 검색 결과 통합 + direct_answer + confidence |
| [answer_generator.py](app/pipeline/answer_generator.py) | 1,160 | L1·L2·L5 | 답변 LLM 호출 + 저신뢰 재시도 + health |
| [response_validator.py](app/pipeline/response_validator.py) | (확인) | ❌ | 답변 검증 (환각·완전성) |
| [translator.py](app/pipeline/translator.py) | 254 | L4 | 컨텍스트 번역 (M2M-100 또는 Ollama) |
| [language_detector.py](app/pipeline/language_detector.py) | (확인) | ❌ | 한국어/영어 감지 |
| [glossary.py](app/pipeline/glossary.py) | (확인) | ❌ | 용어사전 정규화 (학식→학생식당 등) |
| [ko_tokenizer.py](app/pipeline/ko_tokenizer.py) | (확인) | ❌ | 한국어 토큰화 (kiwipiepy) |
| [answer_units.py](app/pipeline/answer_units.py) | (확인) | ❌ | 답변 단위·구별자 검증 (PR #25 게이트와 협업) |
| [clarification.py](app/pipeline/clarification.py) | (확인) | ❌ | 모호한 질문 명확화 (사용 여부 확인 필요) |
| [community_selector.py](app/pipeline/community_selector.py) | (확인) | ❌ | 그래프 커뮤니티 선택 (Leiden) |

### 3.2 백엔드 (`backend/`)

| 파일 | 역할 |
|---|---|
| [backend/routers/chat.py](backend/routers/chat.py) | **통합 지점** — 모든 LLM 모듈 오케스트레이션. SSE stream / sync |
| [backend/routers/admin/*.py](backend/routers/admin/) | 어드민 FAQ·로그·캐시 |
| [backend/dependencies.py](backend/dependencies.py) | 파이프라인 초기화 (Embedder·Reranker 강제 로드) |
| [backend/main.py](backend/main.py) | FastAPI lifespan, 라우터 등록 |

### 3.3 공유 리소스 (`app/`)

| 파일 | 역할 |
|---|---|
| [app/shared_resources.py](app/shared_resources.py) | Embedder·Reranker·ChromaStore·BM25 싱글톤 |
| [app/config.py](app/config.py) | 모든 환경변수·설정 중앙화 |
| [app/models.py](app/models.py) | Pydantic·dataclass 모델 (Intent·QueryAnalysis·MergedContext 등) |
| [app/embedding/embedder.py](app/embedding/embedder.py) | bge-m3 wrapper |
| [app/vectordb/](app/vectordb/) | ChromaDB store + BM25 인덱스 |
| [app/graphdb/academic_graph.py](app/graphdb/academic_graph.py) | NetworkX 학사 그래프 + FAQ 역인덱스 |

---

## 4. 데이터 흐름 (요청 → 응답)

### 4.1 핵심 데이터 객체

```
SearchRequest  ──┐
                 ▼
question:str ──→ FollowUpSignal ──→ search_query:str ──→ QueryAnalysis ──→
              follow_up_detector   query_rewriter        query_analyzer
                                                       (intent, entities, qtype, lang)
                                                                │
                                                                ▼
                                                       SearchResult[]  ──→
                                                       query_router
                                                       (vector + graph + bm25 + rerank)
                                                                │
                                                                ▼
                                                       MergedContext  ──→
                                                       context_merger
                                                       (formatted_context, direct_answer,
                                                        context_confidence, source_urls)
                                                                │
                                                                ▼
                                                       ChatResponse / SSE
                                                       answer_generator
                                                       (answer, sources, warnings)
                                                                │
                                                                ▼
                                                       [validation_passed]
                                                       response_validator
```

### 4.2 Stage별 입출력 표

| Stage | 입력 | 출력 | LLM | 비용 |
|---|---|---|---|---|
| 1. detect | question, history | FollowUpSignal | — | <1ms |
| 2. rewrite | question, history | search_query | L3 (조건부) | 최대 0.8s |
| 3. analyze | search_query | QueryAnalysis | M1 (qtype embedding) | ~100ms |
| 4. search | search_query, analysis | vector_results + graph_results | M1·M2 | 평균 7s |
| 5. merge | search results | MergedContext (formatted + direct_answer + confidence) | — | ~10ms |
| 6. retry | low-conf 시 | 새 search_query → 재검색 → 재머지 | L2 (조건부) | 5s max |
| 7. generate | context + question | answer (스트리밍) | L1 | 평균 5s |
| 8. translate | answer (KO) | answer (target_lang) | L4 (lang≠ko 시) | 최대 120s |
| 9. validate | answer | warnings | — | <10ms |

---

## 5. 흐름 다이어그램 — 경로별 분기

### 5.1 일반 흐름 (gen 경로)

```
question
    │
    ▼
[Stage 1] follow_up_detector.detect()
    │
    ▼ FollowUpSignal{is_follow_up, reason, skip_rule_stage}
    │
    ▼ if is_follow_up AND rewrite_enabled
[Stage 2] query_rewriter.rewrite()
    │ ├─ standalone-question skip (휴리스틱)
    │ ├─ Stage 2 rule (대명사 치환, <5ms)
    │ └─ Stage 3 LLM (gemma3:4b, 0.8s timeout) ← L3
    ▼ search_query
    │
    ▼
[Stage 3] query_analyzer.analyze()  ─── M1 (qtype embedding)
    │
    ▼ QueryAnalysis{intent, entities, qtype, lang, ...}
    │
    ▼
[Stage 4] query_router.route_and_search()
    ├─ ChromaDB query (M1 embedder)
    ├─ BM25 query
    ├─ Graph search (academic_graph)
    └─ reranker.rerank() ─── M2 CrossEncoder
    ▼ vector_results + graph_results
    │
    ▼
[Stage 5] context_merger.merge()
    │ ├─ direct_answer 채택 루프 (PR #25 게이트 적용)
    │ │  ├─ rerank gate: raw_score ≥ 0.20
    │ │  ├─ AnswerUnit gate: _answer_unit_aligns()
    │ │  └─ 통과 시 direct_answer + confidence=1.0
    │ ├─ context 포맷팅 (formatted_context)
    │ └─ confidence 산정 (1.0/0.8/0.6/0.4/0.0)
    ▼ MergedContext
    │
    ▼ if confidence < 0.3 AND no direct_answer AND no transcript
[Stage 6] L2 저신뢰 재시도
    ├─ answer_generator.rewrite_query()  ← L2
    ├─ route_and_search() 재호출
    └─ 결과 머지 (중복 제거)
    ▼
    │
    ▼ if direct_answer → path=direct (LLM 우회, 즉시 반환)
    │ else → path=stream/sync
    │
    ▼
[Stage 7] answer_generator.generate()  ← L1
    │ ├─ _build_prompt() (system + context + history + user)
    │ ├─ _LLM_SEMAPHORE (백프레셔)
    │ ├─ 캐시 조회 (TTL 1h)
    │ └─ 스트리밍 yield
    ▼ answer text
    │
    ▼ if lang≠ko AND lang in SUPPORTED_TARGET_LANGS
[Stage 8] translator.translate_if_needed()  ← L4 (optional)
    │
    ▼
[Stage 9] response_validator.validate()
    │
    ▼
[Stage 10] CHAT_END + transcript 저장 + SSE close
```

### 5.2 direct_answer 우회 흐름 (path=direct)

```
... (Stage 1~5 동일) ...
context_merger.merge() → MergedContext{direct_answer: "...", confidence: 1.0}
    │
    ▼ if direct_answer 존재
chat.py → DIRECT_ANSWER 로그 + answer = merged.direct_answer
    │
    ▼ Stage 7~9 모두 우회
CHAT_END(path=direct)
```

전체 트래픽의 16.4% (24/146, app.log 기준).

### 5.3 follow-up + 멀티턴 흐름

```
question = "그럼 영문과는?"
history = [{user: "졸업학점은?"}, {assistant: "120학점입니다"}, ...]
    │
    ▼
follow_up_detector.detect() → is_follow_up=True
    │
    ▼
query_rewriter.rewrite()
    ├─ standalone-question? NO (질문이 너무 짧고 대명사 의존)
    ├─ Stage 2 rule:
    │  ├─ _extract_last_assistant_entity() → "졸업학점" (예상)
    │  └─ replace("그럼", "졸업학점") → ??? (대명사 매칭 안 되면)
    │  └─ "그럼" 미매치 → Stage 2 실패
    └─ Stage 3 LLM:
       └─ gemma3:4b로 "영문과의 졸업학점은 얼마입니까?" 생성
    ▼ search_query = "영문과의 졸업학점은 얼마입니까?"
... (이후 일반 흐름) ...
```

---

## 6. 외부 서비스

### 6.1 Ollama (LLM 서버)

```
http://<base_url>:11434/
├─ /api/chat              (api_type=ollama, think:false 지원)
├─ /api/generate          (사용 안 함)
├─ /api/tags              (모델 목록)
└─ /v1/chat/completions   (api_type=openai, OpenAI 호환)
```

**상주 모델** (`OLLAMA_MAX_LOADED_MODELS=2`):
- 메인 답변: `qwen3:8b` (L1·L2·L5)
- 경량 재작성: `gemma3:4b` (L3)
- (선택) 번역: `qwen2.5:7b` (L4, TRANSLATOR_BACKEND=ollama 시)

**동시성**:
- Ollama 측: `OLLAMA_NUM_PARALLEL=2`
- Backend 측: `_LLM_SEMAPHORE(settings.llm.max_concurrent=2)` (PR #22)

### 6.2 ChromaDB (벡터 DB)

```
data/chromadb/              (운영 컬렉션 = bufs_academic)
data/chromadb_new/          (5/6 재인제스트, 환경별 사용)
data/chromadb.bak-*/        (백업)
```

총 ~3,439 청크 (PDF + FAQ + notice + scholarship + timetable).

### 6.3 인-프로세스 모델

- HuggingFace cache: `~/.cache/huggingface/`
- bge-m3 (M1): ~2.2GB
- bge-reranker-v2-m3 (M2): ~2.2GB

---

## 7. 설정 트리

```
settings (config.py Settings 클래스)
├─ llm: LLMConfig
│  ├─ base_url:       LLM_BASE_URL / OLLAMA_BASE_URL (default localhost:11434)
│  ├─ model:          LLM_MODEL / OLLAMA_MODEL (default gemma4:26b)
│  ├─ max_tokens:     LLM_MAX_TOKENS (2048)
│  ├─ temperature:    LLM_TEMPERATURE (0.1)
│  ├─ top_p:          LLM_TOP_P (0.9)
│  ├─ timeout:        LLM_TIMEOUT (60s)
│  ├─ api_type:       LLM_API_TYPE (openai / ollama, default openai)
│  ├─ max_concurrent: LLM_MAX_CONCURRENT (2)  ← PR #22 세마포어
│  ├─ response_cache_ttl_seconds: LLM_RESPONSE_CACHE_TTL (3600)
│  └─ response_cache_max_entries: LLM_RESPONSE_CACHE_MAX_SIZE (256)
│
├─ embedding: EmbeddingConfig
│  ├─ model_name:     EMBEDDING_MODEL (BAAI/bge-m3)
│  └─ device:         EMBEDDING_DEVICE (cpu / cuda)
│
├─ reranker: RerankerConfig
│  ├─ model_name:     RERANKER_MODEL (BAAI/bge-reranker-v2-m3)
│  ├─ device:         RERANKER_DEVICE
│  ├─ enabled:        RERANKER_ENABLED (true)
│  ├─ top_k:          RERANKER_TOP_K (10)
│  └─ candidate_k:    RERANKER_CANDIDATE_K (30)
│
├─ chroma: ChromaConfig
│  ├─ persist_dir:    CHROMA_PERSIST_DIR (data/chromadb)
│  ├─ collection_name: CHROMA_COLLECTION (bufs_academic)
│  └─ n_results:      CHROMA_N_RESULTS (15)
│
├─ conversation: ConversationConfig  ← Stage 2 follow-up 영역
│  ├─ history_enabled:           CONV_HISTORY_ENABLED (true)
│  ├─ max_history_turns:         CONV_MAX_HISTORY_TURNS (2)
│  ├─ history_token_budget:      CONV_HISTORY_TOKEN_BUDGET (500)
│  ├─ rewrite_enabled:           CONV_REWRITE_ENABLED (true)
│  ├─ rewrite_model:             CONV_REWRITE_MODEL (gemma3:4b)
│  ├─ rewrite_base_url:          CONV_REWRITE_BASE_URL ("")
│  ├─ rewrite_timeout_sec:       CONV_REWRITE_TIMEOUT_SEC (0.8s)
│  ├─ rewrite_max_tokens:        CONV_REWRITE_MAX_TOKENS (80)
│  ├─ rewrite_max_input_turns:   CONV_REWRITE_MAX_INPUT_TURNS (2)
│  └─ follow_up_max_words:       CONV_FOLLOW_UP_MAX_WORDS (5)
│
├─ pipeline: PipelineConfig
│  ├─ evidence_slicing_enabled:  EVIDENCE_SLICING_ENABLED (false)
│  ├─ evidence_slicing_min_text_len:    EVIDENCE_SLICING_MIN_TEXT_LEN (1400)
│  ├─ evidence_slicing_min_sliced_len:  EVIDENCE_SLICING_MIN_SLICED_LEN (500)
│  ├─ evidence_slicing_context_lines:   EVIDENCE_SLICING_CONTEXT_LINES (2)
│  ├─ en_vector_ko_query_threshold:     EN_VECTOR_KO_QUERY_THRESHOLD (3)
│  └─ rerank_bypass_threshold:          RERANK_BYPASS_THRESHOLD (0.20)  ← PR #25
│
├─ admin_faq: AdminFaqConfig (도서관·관리자 FAQ + refusal 문구)
├─ transcript_rules: TranscriptRulesConfig (학사 룰 fallback)
├─ crawler: CrawlerConfig (스케줄러 — 본 audit 범위 밖)
├─ pdf: PDFConfig (인제스트 — 본 audit 범위 밖)
└─ admin / notifications / app / graph (기타)
```

translator 설정은 별도 `settings.translator`가 있으면 사용, 없으면 환경변수 직접 (`TRANSLATOR_BACKEND`, `TRANSLATOR_MODEL`, `TRANSLATOR_TIMEOUT` 등).

---

## 8. 의존성 그래프

```
┌──────────────────────────────────────────────────────────────────┐
│                        chat.py (통합 지점)                       │
└────────┬──────────────────────────────────────────────┬──────────┘
         │                                              │
         ▼                                              ▼
┌────────────────┐  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
│ follow_up_     │  │ query_rewriter │  │ query_analyzer │  │ query_router   │
│ detector       │  │  (L3 LLM)      │  │  (M1 embed)    │  │  (M1·M2)       │
└────────────────┘  └────────┬───────┘  └────────┬───────┘  └────────┬───────┘
                              │                    │                    │
                              ▼                    ▼                    ▼
                       ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
                       │ ko_tokenizer │     │ glossary     │     │ reranker     │
                       │ Settings     │     │ ko_tokenizer │     │  (M2)        │
                       └──────────────┘     │ academic_grp │     └──────────────┘
                                            └──────────────┘
                                                                          │
                                                                          ▼
                                                                  ┌──────────────┐
                                                                  │ chroma_store │
                                                                  │ bm25_index   │
                                                                  │ academic_grp │
                                                                  └──────────────┘
         │
         ▼
┌────────────────────────┐
│ context_merger         │ ── answer_units, academic_graph (FAQ heur)
└────────┬───────────────┘
         │
         ▼
┌────────────────────────┐
│ answer_generator       │  (L1·L2·L5 LLM)
│  - generate()          │
│  - rewrite_query()     │
│  - health_check()      │
└────────┬───────────────┘
         │
         ▼
┌────────────────────────┐    ┌────────────────────────┐
│ response_validator     │    │ translator             │ (L4 LLM, 조건부)
└────────────────────────┘    └────────────────────────┘
```

**싱글톤 (shared_resources.py)**:
- `Embedder` (bge-m3) → query_analyzer, query_router, retriever
- `CrossEncoder` (bge-reranker) → reranker
- `ChromaStore` → query_router
- `BM25Index` → query_router

---

## 9. 빌드 타임 vs 런타임 분리

### 9.1 빌드 타임 (인제스트, 본 audit 범위 밖)

```
scripts/ingest_all_v2.py
    ├─ PDF parsing (Surya OCR)
    ├─ Table extraction (VLM — V1)
    ├─ Chunking (chunking_v2)
    ├─ Embedding (bge-m3, batch)
    ├─ ChromaDB upsert
    ├─ BM25 인덱스 rebuild
    └─ academic_graph 노드/엣지 갱신
```

### 9.2 런타임 (요청-응답, 본 audit 범위)

위 Stages 1-11. **본 audit 모든 모듈이 여기에 속함**.

---

## 10. PR 영향 매트릭스 (현재 main 기준)

| PR | 상태 | LLM 모듈 영향 | 본 audit 관련성 |
|---|---|---|---|
| #21 Ollama 동시처리 | ✅ main | L1·L3 동시성 슬롯 | 외부 의존(Ollama) 운영 |
| #22 Semaphore 백프레셔 | ✅ main | L1·L2 백엔드 큐 | LLMConfig.max_concurrent 추가 |
| #23 거절 메시지 cleanup | ✅ main | L1 거절 문구 | 학사지원팀 하드코딩 제거 |
| **#25 CrossEncoder 게이트** | ✅ main | context_merger (direct_answer) | **본 audit에 포함 — PR #25 게이트 적용 후 코드가 audit 대상** |
| #24 LLM understanding | 🟡 OPEN | 신규 `query_understanding.py`로 L3 + analyzer 통합 | **audit 범위 밖**. 평행 트랙. |
| #26 Observability + P0 | 🟡 OPEN | 12-stage trace 로그 + cite_key | **audit 범위 밖**. 머지 시 관찰성 자동 보강. |

---

## 11. 모듈 간 인터페이스 표 (예약 — Phase A 진행하며 채워질)

| 모듈 → 모듈 | 전달 데이터 | 인터페이스 |
|---|---|---|
| follow_up_detector → chat.py | `FollowUpSignal` | dataclass |
| query_rewriter → chat.py | `str` (search_query) | 단순 반환 |
| query_analyzer → chat.py | `QueryAnalysis` | dataclass |
| query_router → chat.py | `dict{vector_results, graph_results}` | dict |
| context_merger → chat.py | `MergedContext` | dataclass |
| answer_generator → chat.py | `AsyncGenerator[str]` 또는 `str` | 스트리밍/단발 |
| (모듈 2~7 deep dive 진행하며 확장) | | |

---

## 12. 측정·검증 인프라

| 도구 | 위치 | 용도 |
|---|---|---|
| `eval_contains_f1.py` | scripts/eval_contains_f1.py | 164문 회귀 평가 (CLAUDE.md 검증 표준) |
| `eval_f1_score.py` | scripts/eval_f1_score.py | 토큰 F1 채점 헬퍼 |
| `eval_full.py` | scripts/eval_full.py | 검색 지표 (Recall@5, MRR@5) |
| `measure_rerank_bypass_threshold.py` | scripts/ | **PR #25에서 추가**. CrossEncoder 게이트 임계치 측정 |
| `eval_ragas.py` | scripts/eval_ragas.py | RAGAS 평가 |
| `eval_multilingual.py` | scripts/eval_multilingual.py | EN 지표 |
| `app.log` | / | 운영 로그 (Phase 2 분석 소스) |

baseline: `combined_p0_1a_20260518_143121.json` = **68.90%** (현 환경 기준, CLAUDE.md NO-GO 기준선).

---

## 13. 결론 — 청사진의 의미

이 구조에서 LLM 관련 **수정 가능한 surface**는:

1. **모듈 단위** (8개 모듈 + chat.py 통합 지점)
2. **호출 단위** (5개 LLM 호출 + 2개 모델 추론)
3. **프롬프트** (L1/L2/L3 system prompt + L4 번역 프롬프트)
4. **설정** (env 80+개)
5. **알고리즘** (rewrite 휴리스틱 / context_merger confidence / reranker boost / direct_answer gate 등)

**가장 큰 잠재 손실 영역** (audit Phase 4 우선순위 기반):
- 🔴 **L1 답변 LLM** — 평균 5초, 핫패스. 프롬프트·max_tokens·history 튜닝 영향 큼
- 🔴 **Stage 4 검색** — 평균 7초, 핫패스. 인-프로세스 모델 비용 + boost 정책
- 🟡 **L3 follow-up rewrite** — 운영 발동률 미측정. dead code 위험
- 🟡 **L2 저신뢰 재시도** — 발동 0% 확인됨. dead code 강한 의심
- 🟢 **L4 번역** — KO-only 환경에서 비용 0

판단에 도움이 될 추가 정보가 필요하면 말씀 주세요:
- 특정 모듈을 더 깊이 (예: chat.py 통합 로직)
- 특정 시퀀스를 단계별 코드까지 (예: direct_answer 채택)
- 외부 의존 한 발짝 더 (Ollama 모델 로드 동작 등)
