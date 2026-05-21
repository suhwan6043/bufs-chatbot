# LLM 파이프라인 종합 감사 보고서

**기준**: `review/llm-pipeline-audit` 브랜치 (origin/main `10de3dc` 기준 — PR #25 머지 후)
**시작일**: 2026-05-20
**감사 대상**: main 코드의 모든 LLM 호출 + 모델 추론(임베더·리랭커)
**비고**: PR #24(`query_understanding.py`)는 main에 없음. 팀원이 보고한 "97% rule_fallback / 28초 rewrite"는 PR #24 브랜치 환경 측정치이며 main 행동과 분리해서 진단해야 함.

---

## Phase 1 — LLM 터치포인트 인벤토리

### 인벤토리 한눈에

| # | 위치 | 종류 | 모델 | 타임아웃 | 캐시 | 발동 조건 |
|---|---|---|---|---|---|---|
| L1 | [answer_generator.generate()](app/pipeline/answer_generator.py#L837) | 답변 LLM (스트리밍) | `settings.llm.model` (env: qwen3:8b) | `settings.llm.timeout` = 60s | ✅ LLM_RESPONSE_CACHE | 매 요청 (direct_answer 우회 제외) |
| L2 | [answer_generator.rewrite_query()](app/pipeline/answer_generator.py#L709) | 저신뢰 재시도 쿼리 재작성 | 메인 LLM (동일) | **5.0s 하드코딩** | ❌ | `context_confidence < 0.3` AND no direct_answer AND no transcript |
| L3 | [query_rewriter.rewrite()](app/pipeline/query_rewriter.py#L312) | follow-up 재작성 (2단계) | Stage2: 룰 / Stage3: `settings.conversation.rewrite_model` = `gemma3:4b` | `rewrite_timeout_sec` = 0.8s | ❌ | `follow_up_signal.is_follow_up = True` |
| L4 | [translator.translate_if_needed()](app/pipeline/translator.py#L126) | 컨텍스트 번역 | M2M-100 418M (CPU) 또는 ollama `qwen2.5:7b` | `TRANSLATOR_TIMEOUT` = 120s | ❌ (모델은 lazy-load 후 메모리 캐시) | `target_lang ≠ ko` AND 컨텍스트 한국어 포함 시 |
| L5 | [answer_generator.health_check()](app/pipeline/answer_generator.py#L1150) | LLM heartbeat | 메인 LLM | 5s 하드코딩 | ❌ | `/api/health/llm` 엔드포인트 |
| M1 | [shared_resources.get_embedder()](app/shared_resources.py#L41) | 임베딩 추론 | `bge-m3` | n/a (in-process) | 모델 메모리 (싱글톤) | 매 검색 + reranker 단계 |
| M2 | [shared_resources.get_reranker_model()](app/shared_resources.py#L98) | CrossEncoder 추론 | `bge-reranker-v2-m3` | n/a (in-process) | 모델 메모리 (싱글톤) | 매 검색 후보 재순위화 |
| V1 | [vlm_extractor.py](app/pdf/vlm_extractor.py) | PDF 표 추출 VLM | 환경변수 | n/a | n/a | 인제스트 빌드 시점 전용 (런타임 hot path 아님) |

---

### L1 — `answer_generator.generate()` 메인 답변 LLM

- **파일/라인**: [app/pipeline/answer_generator.py:837](app/pipeline/answer_generator.py#L837)
- **모델**: `.env`의 `LLM_MODEL`(주: `qwen3:8b`) 또는 `OLLAMA_MODEL`. `config.py`의 default `gemma4:26b`는 .env 미설정 시만.
- **엔드포인트**:
  - `api_type=ollama`: `{base_url}/api/chat` (네이티브, `think:false` 동작)
  - `api_type=openai` (default): `{base_url}/v1/chat/completions` (OpenAI 호환)
- **타임아웃**: `settings.llm.timeout` = 60s (.env `LLM_TIMEOUT`)
- **호출 형태**: 스트리밍 (token-by-token yield)
- **캐싱**: ✅ `_make_cache_key()` + `LLM_RESPONSE_CACHE_TTL=3600s` + `LLM_RESPONSE_CACHE_MAX_SIZE=256`
- **백프레셔**: ✅ `_LLM_SEMAPHORE = asyncio.Semaphore(settings.llm.max_concurrent)` (chat.py 모듈 레벨)
- **재시도**: ❌ 없음 — 실패 시 거절 메시지 반환
- **input**:
  - 프롬프트 = system + context + history(`_trim_history_for_llm`) + user query
  - max_tokens = `_resolve_max_tokens()` (질문 타입별 동적)
  - temperature = `settings.llm.temperature` = 0.1
- **실패 경로**:
  - `httpx.ConnectError` → logger.error("Ollama 서버 연결 실패. Ollama를 실행해주세요.")
  - 그 외 예외 → 거절 문구 반환 (admin_faq.refusal_phrase_ko)

### L2 — `answer_generator.rewrite_query()` 저신뢰 재시도 재작성

- **파일/라인**: [app/pipeline/answer_generator.py:709](app/pipeline/answer_generator.py#L709)
- **모델**: 메인 LLM (`self.model`, `self.base_url`)
- **타임아웃**: **5.0s 하드코딩** ⚠️ — 4원칙 위반 (env 오버라이드 없음)
- **호출 형태**: 단발 (non-streaming, `stream: false`, max_tokens=80)
- **캐싱**: ❌
- **재시도**: ❌
- **호출 위치**: [chat.py:624](backend/routers/chat.py#L624)
- **발동 조건** (전부 AND):
  - `merged.context_confidence < 0.3`
  - `not merged.direct_answer`
  - `not transcript_context`
- **input**: 짧은 system + "원본: {q}\n재작성된 쿼리:" 형식 user
- **output 처리**: 첫 줄, 접두사 제거, 따옴표 제거 → 너무 짧거나 길면 원본 유지
- **재시도 후 동작**: 재작성된 쿼리로 `router_inst.route_and_search()` 다시 → 결과 머지 (중복 제거)
- **실패 경로**: `logger.warning("쿼리 재작성 실패, 원본 사용")` 후 원본 반환 (graceful)

### L3 — `query_rewriter.rewrite()` follow-up 재작성

- **파일/라인**: [app/pipeline/query_rewriter.py:312](app/pipeline/query_rewriter.py#L312)
- **구조**: 2-Stage 폴백
  - **Stage 2** (룰 치환): `rule_based_rewrite()` — "그거"·"it" 등 단일 대명사를 직전 assistant entity로 치환. <5ms.
  - **Stage 3** (경량 LLM): `llm_rewrite()` — gemma3:4b 호출.
- **Stage 3 모델**: `settings.conversation.rewrite_model` = `gemma3:4b` (env `CONV_REWRITE_MODEL`)
- **Stage 3 엔드포인트**: `rewrite_base_url` or `settings.llm.base_url`, openai or ollama api_type
- **Stage 3 타임아웃**: `rewrite_timeout_sec` = 0.8s (env `CONV_REWRITE_TIMEOUT_SEC`)
- **max_tokens**: `rewrite_max_tokens` = 80
- **input**: history `_format_history_for_prompt(max_turns=2)` + 마지막 질문 + system prompt
- **output 검증**: 4단계
  1. 길이 가드 (3자 미만 또는 원본의 5배 초과 → 거부)
  2. 의문문 어미·키워드 가드 (`?`, "까", "what", "어떻", ... 중 하나 필수)
  3. 이전 assistant 답변 substring 매칭 → 거부 (prior answer 복붙 방지)
  4. 동일 쿼리 → 거부
- **캐싱**: ❌
- **발동 조건** (전부 AND):
  - `settings.conversation.rewrite_enabled = True` (env `CONV_REWRITE_ENABLED`)
  - `follow_up_signal.is_follow_up = True`
  - 원본이 **이미 self-contained 의문문**이면 skip (의문어 + 의문 어미 + 길이 ≥ 8자 기준)
- **호출 위치**: [chat.py:552](backend/routers/chat.py#L552) (chat_stream), [chat.py:969](backend/routers/chat.py#L969) (chat_sync)
- **실패 경로**: 타임아웃·예외 시 `None` 반환 → caller가 원본 쿼리 사용
- **최대 비용**: 0.8s (timeout 한계). main 코드에선 28초 불가능.

### L4 — `translator.translate_if_needed()` 컨텍스트 번역

- **파일/라인**: [app/pipeline/translator.py:126](app/pipeline/translator.py#L126)
- **백엔드 선택**: `TRANSLATOR_BACKEND` 환경변수
  - `m2m100` (default): facebook/m2m100_418M CPU. lazy-load 후 메모리 캐시. MIT 라이선스.
  - `ollama`: `TRANSLATOR_MODEL` (default `qwen2.5:7b`) — Ollama 호출
- **타임아웃**: `TRANSLATOR_TIMEOUT` = 120s (ollama 백엔드 시)
- **발동 조건**:
  - `settings.translator.enabled = True`
  - `target_lang in SUPPORTED_TARGET_LANGS` (ko 제외)
  - 컨텍스트에 한국어 문자 포함
- **워밍업**: 서버 시작 시 `translator.warmup()` 백그라운드 스레드에서 M2M-100 로드 (~1.6GB 다운로드 포함)
- **호출 위치**: 컨텍스트 머지 후 EN 사용자 응답 직전 (chat.py에서)
- **실패 경로**: 타임아웃·예외 시 원문 컨텍스트 그대로 반환
- **트래픽 비중**: KO 사용자는 무조건 pass-through (zero cost). EN 사용자만 비용.

### L5 — `answer_generator.health_check()`

- **파일/라인**: [app/pipeline/answer_generator.py:1150](app/pipeline/answer_generator.py#L1150)
- **모델**: 메인 LLM
- **타임아웃**: 5s 하드코딩
- **호출 위치**: `/api/health/llm` 엔드포인트 ([backend/routers/health.py:150-160])
- **빈도**: 관리자 조회 시. 핫 패스 아님.

### M1 — Embedder (`bge-m3`)

- **싱글톤**: [app/shared_resources.py:41](app/shared_resources.py#L41)
- **device**: `EMBEDDING_DEVICE` (default `cpu`, 운영은 `cuda` 설정 권장)
- **로드 시점**: 서버 시작 시 `dependencies.init_all()`에서 강제 로드
- **사용처**: ChromaDB 인덱싱 + 매 쿼리 dense retrieval + (audit 신규) Q-Q cosine 게이트 보조
- **warm prediction**: ❌ 없음 (reranker는 있음)
- **출력 정규화**: `normalize_embeddings=True` → cosine = dot product

### M2 — Reranker (`bge-reranker-v2-m3` CrossEncoder)

- **싱글톤**: [app/shared_resources.py:98](app/shared_resources.py#L98)
- **device**: `RERANKER_DEVICE` (default `cpu`)
- **로드 시점**: 서버 시작 시 강제 로드 + warm prediction 1회
- **호출**: [app/pipeline/reranker.py:106](app/pipeline/reranker.py#L106) `self.model.predict(pairs)` (batch)
- **boost 로직**:
  - FAQ → `+ tier2_bonus = abs(top_raw) * 0.05`
  - pinned notice → 동일 5%
  - URL-aware → 4%~18% (asks_url 질문)
  - Tier 1 (domestic/guide): 부스트 없음 (2026-04-18 제거)
- **출력**:
  - `result.score` = boosted score (downstream 사용)
  - `result.metadata["raw_score"]` = 부스트 전 raw logit (PR #25 게이트가 사용)
- **컷오프**:
  - relative_threshold = top_score * 0.5
  - absolute_floor = -3.0
  - top_k = `settings.reranker.top_k` = 10
- **다이버시티 가드**: top-k에 PDF가 0개면 최상위 PDF 1개 강제 삽입

### V1 — VLM (PDF 표 추출, 빌드 타임)

- **파일**: [app/pdf/vlm_extractor.py](app/pdf/vlm_extractor.py) (419 LoC)
- **사용처**: 인제스트 파이프라인 — PDF 페이지 → 마크다운 표 변환
- **런타임 영향**: 없음 (사용자 응답 흐름과 분리됨)
- **감사 범위**: **본 audit에서 제외** — 인덱싱 품질에 영향하지만 런타임 LLM 호출 아님

---

## Phase 1 요약

| 핫패스 위치 | 호출 빈도 | 최대 지연 (main 기준) |
|---|---|---|
| L1 답변 LLM | 매 요청 | 60s (timeout) |
| L2 저신뢰 재시도 | confidence<0.3 시만 | 5s |
| L3 follow-up 재작성 | follow-up 감지 시만 | 0.8s |
| L4 번역 | EN 사용자만 | 120s |
| M2 리랭커 | 매 요청 | 인-프로세스 (~수십 ms) |

**main 코드의 "rewrite" 슬롯 최대 비용 = 0.8s**. 팀원의 "28초 rewrite" 측정은 PR #24 환경에서 나옴 — `query_understanding.py`의 3-tier fallback (1차 LLM 타임아웃 + 2차 LLM 타임아웃 + 룰 fallback)에서 발생. 본 audit Phase 3에서 명확히 다룸.

**4원칙 위반 발견**:
- ⚠️ L2 [rewrite_query()](app/pipeline/answer_generator.py#L756) 타임아웃 5.0s 하드코딩 — env 오버라이드 없음.
- ⚠️ L5 [health_check()](app/pipeline/answer_generator.py#L1150) 타임아웃 5s 하드코딩.

**캐싱 부재**:
- L2/L3/L4 모두 캐싱 없음. 같은 쿼리에 대해 매번 LLM 호출.

다음 Phase 2에서 `app.log` 분석으로 각 호출의 실제 발동률·지연 분포를 측정합니다.

---

## Phase 2 — 로그 시그너처 + 발동률·지연 분포

**데이터**: `/Users/sungwon.l/bufs-chatbot/app.log` (2012 라인, 2026-05-11). CHAT_START 152건, CHAT_END 146건, LLM_DONE 122건.

### 발동 매트릭스

| 지점 | 발동 | 발동률 | mean | p50 | p95 | max | 비고 |
|---|---|---|---|---|---|---|---|
| **L1 generate** | 122건 | 122/146 = 83.6% | 5,123ms | 4,421ms | 10,409ms | 21,036ms | 24건은 path=direct로 LLM 우회 |
| **L1 거절** | 7건 | 7/122 = 5.7% | n/a | n/a | n/a | n/a | "관련 정보를 찾을 수 없습니다" 응답 |
| **L1 Ollama 연결실패** | 1건 | 1/122 = 0.8% | n/a | n/a | n/a | n/a | 일시 장애 |
| **L2 rewrite_query** | **0건** | **0%** | — | — | — | — | confidence<0.3 조건이 발동 안 함 |
| **L3 follow-up rewrite** | **0건** | **0%** | — | — | — | — | **follow_up=no_history 122건 전부** |
| **L4 translator** | **0건** | **0%** | — | — | — | — | lang=ko 100% (EN 트래픽 없음) |
| **path=direct** | 24건 | 24/146 = 16.4% | 4,924ms | 3,732ms | 11,234ms | 15,733ms | direct_answer 우회 |

### 단계별 PIPELINE_TIMING 평균 (CHAT_END 122 기준)

| 단계 | mean | max | 비고 |
|---|---|---|---|
| follow_up | 0ms | 0ms | follow_up_detector 룰 — 거의 0 |
| **rewrite** | **0ms** | **0ms** | **모든 케이스에서 rewrite 미발동** |
| analyze | 139ms | 748ms | query_analyzer 룰 + QuestionType embedding |
| search | 7,014ms | 79,946ms | hybrid retrieval (chroma+bm25+graph+rerank) |
| merge | 0ms | 0ms | context_merger — 인메모리 |
| retry | 0ms | 0ms | L2 저신뢰 재시도 미발동 |
| **generate** | **5,123ms** | **21,036ms** | L1 메인 LLM |
| validate | 0ms | 0ms | response_validator 룰 |

### 관찰 1 — 본 로그는 "첫턴 only" 트래픽

`follow_up=no_history` 122건 전부. **멀티턴 follow-up 시나리오 0건 측정** → L3 (follow-up rewrite)의 실제 행동(빈도·지연·실패율)은 본 로그로 측정 불가.

### 관찰 2 — search > generate

search mean 7,014ms vs generate 5,123ms (search가 1.4배 더 비쌈). 다만 max 79,946ms 같은 outlier가 mean을 왜곡 (팀원의 "COURSE_INFO 30초+" 패턴과 일치).

### 관찰 3 — L2 저신뢰 재시도 발동 0건

[chat.py:619-624](backend/routers/chat.py#L619-L624)의 발동 조건 `context_confidence < 0.3` AND no direct_answer AND no transcript_context — 본 로그 트래픽에선 한 번도 만족 안 됨. **실질적으로 dead code 상태일 가능성**.

### 관찰 4 — 인-프로세스 모델(M1/M2)은 로그에 안 잡힘

reranker · embedder 호출은 인-프로세스로 별도 시작 로그가 없음. 비용은 search 7,014ms 안에 묶여 있음.

---

## Phase 3 — 코드 의도 vs 실제 로그 행동 (크로스 분석)

### 갭 1 — "rewrite=28초"의 출처

| | main 코드 | 팀원 보고 측정치 |
|---|---|---|
| 모듈 | `query_rewriter.py` (Stage 2 룰 + Stage 3 LLM 0.8s timeout) | `query_understanding.py` (PR #24, 1차+2차 LLM + 룰 폴백 3단계) |
| 최대 비용 | **0.8초** | **28초** (97% rule_fallback) |
| "rule_fallback" 용어 | 존재 안 함 | `UnderstandingResult.source` 값 |
| app.log 실측 | rewrite=0ms (122/122) | 별도 환경 |

**결론**: 팀원의 "28초 rewrite / 97% rule_fallback" 보고는 **PR #24 활성 환경의 측정치**. main 운영 환경에선 해당 현상 존재 불가.

→ PR #24가 머지되지 않는 한, 운영 환경엔 영향 없음. 단 PR #24 머지 검토 시 **반드시 GPU 환경에서 LLM 성공률 재측정** 필요.

### 갭 2 — L2 저신뢰 재시도 dead code 의심

[chat.py:619-624](backend/routers/chat.py#L619-L624) 조건이 본 로그 122건 중 0건 발동. 이유 후보:
- (a) `context_confidence` 산정 로직이 0.3 미만으로 떨어지지 않게 보정됨 (PR #25 게이트 적용 후 confidence 그라데이션 변경 가능성)
- (b) direct_answer가 폴백 추출로 거의 항상 채워져서 not direct_answer 조건이 거의 false
- (c) `transcript_context`가 transcript 첨부 사용자만 발동인데 본 트래픽엔 없음 (가능성 높음)

→ **검증 필요**: confidence 분포 측정 (현재 분포는?), 실제 발동 케이스가 운영 트래픽에 있는지

### 갭 3 — follow-up 트래픽 측정 부재

본 로그는 첫턴 only. 실제 운영에서 follow-up이 얼마나 자주 일어나는지 측정 데이터 없음. 만약 운영에서 follow-up 비중이 큰데 L3 (Stage 3 LLM) 타임아웃 0.8초가 너무 빡빡하면 룰 폴백 비중이 높아질 수 있음 — 측정 필요.

### 갭 4 — L4 translator dead code (KO-only 환경)

lang=ko 100%. PR #19/#20에서 영어 사용자 경험 강화했지만 본 로그엔 EN 사용자 0건. translator 비용 0이지만 **EN UX 검증이 운영 데이터로 안 됨**.

### 갭 5 — 4원칙 위반 (하드코딩 타임아웃)

| 위치 | 값 | 영향 |
|---|---|---|
| [answer_generator.py:756](app/pipeline/answer_generator.py#L756) `rewrite_query` | 5.0s | 운영자가 조절 불가. CPU 환경에서 5초로 충분치 않을 수 있음. |
| [answer_generator.py:1153](app/pipeline/answer_generator.py#L1153) `health_check` | 5s | 동일 |

### 갭 6 — 캐싱 비대칭

| 지점 | 캐시 |
|---|---|
| L1 메인 답변 | ✅ TTL 3600s, 256 entries |
| L2 재시도 재작성 | ❌ |
| L3 follow-up 재작성 | ❌ |
| L4 번역 | ❌ |

L1만 캐싱 — 동일 follow-up 쿼리에서도 매번 LLM. 다만 본 로그에선 follow-up 0건이라 영향 미미.

### 갭 7 — 인-프로세스 모델 관찰성 부재

M1 (embedder), M2 (reranker) 호출 비용·실패가 별도 로그에 안 잡힘. search 7초 안에서 어디가 비싼지(임베딩 vs BM25 vs graph vs rerank) 분리 불가. **PR #26의 12-stage trace 로그가 이걸 해결할 가능성**.

---

## Phase 4 — 우선순위 + 권고

### 🔴 P1 — 즉시 액션 가능 (main 영향 적음, ROI 높음)

#### P1.1 하드코딩 타임아웃 env 추출

[answer_generator.py:756](app/pipeline/answer_generator.py#L756) `rewrite_query` 5.0s + [:1153](app/pipeline/answer_generator.py#L1153) `health_check` 5s 를 settings 화. CLAUDE.md 4원칙 직접 위반이고 PR 사이즈 작음 (~20줄).

```python
# config.py LLMConfig
rewrite_query_timeout: float = float(os.getenv("LLM_REWRITE_QUERY_TIMEOUT", "5.0"))
health_check_timeout: float = float(os.getenv("LLM_HEALTH_CHECK_TIMEOUT", "5.0"))
```

#### P1.2 L2 저신뢰 재시도 운영 발동률 측정

본 로그에서 0% 발동. **dead code인지, 빡빡한 조건 때문인지, transcript 첨부 경로의 특수 트래픽에서만 발동인지** 확인 필요. 운영 로그(최근 1주) 가져와서 발동률 측정. **0%이면 제거 검토**, 의미 있는 비중이면 캐싱 추가 검토.

#### P1.3 인-프로세스 모델 관찰성

search 7초가 어디서 오는지 분리 가능하게 로깅 추가:
- ChromaDB query latency
- BM25 query latency
- Graph traversal latency
- Reranker batch latency (candidate count 포함)

PR #26이 이미 12-stage 로그를 추가 중 — 그게 머지되면 자연스럽게 해결. 별도 PR 불필요.

### 🟡 P2 — 측정·결정 의존 (운영 데이터 필요)

#### P2.1 follow-up 운영 비중 측정

본 로그는 첫턴 only라 L3 (Stage 3 LLM 0.8s) 실효성 측정 불가. **운영 로그에서 `follow_up=` 분포 확인**:
- `no_history`: 첫턴
- `is_follow_up=True`: 실제 follow-up
- `False`: history 있으나 follow-up 아님

비율에 따라 결정:
- follow-up 5% 미만 → 0.8초 타임아웃 적절, 현 상태 유지
- follow-up 20%+ → 타임아웃 너무 빡빡하면 룰 폴백 비중 측정 → 필요 시 1.5초 상향

#### P2.2 L4 translator 운영 사용 측정

본 로그 EN 0건. PR #19/#20의 EN UX 개선이 실제 사용되는지 운영에서 EN 트래픽 측정 필요. 0이면 L4 비용 = 0 (현 상태 유지). 의미 있으면 EN p95 측정.

### 🟢 P3 — PR #24 영향 (별도 트랙)

#### P3.1 PR #24의 "97% rule_fallback" 진단

PR #24 머지 검토 시 다음 데이터 요구 (이미 우리가 팀원에게 요청 메시지 초안 작성 완료):
1. `understand[llm]` vs `understand[rule_fallback]` 비율 (운영 환경에서)
2. 1차/2차 LLM 실패 사유 분포 (JSON 파싱·타임아웃·필드 누락)
3. GPU 환경 (H100 47GB)에서 동일 측정

→ **PR #24가 정상 동작하면 main의 L3(0.8s)을 대체할 수 있음. 안 그러면 28초 회귀.**

#### P3.2 main의 L3 vs PR #24의 understanding 선택

PR #24 머지 결정 전 다음 비교 표 필요:

| | main L3 (현재) | PR #24 understanding |
|---|---|---|
| 비용 | 0.8s (max) | ? (GPU 환경에서 측정 필요) |
| 효과 | follow-up 재작성만 | follow-up + intent + entities + qtype 통합 |
| 폴백 | 0.8s 타임아웃 시 원본 사용 | 3단계 (LLM→LLM→룰) |
| 실패 모드 | 0.8초 후 원본 (graceful) | 28초 후 룰 (current) |

### 🔵 P4 — 큰 그림 (수정 안 필요, 인식 필요)

#### P4.1 main 운영 환경의 진짜 병목 = generate

본 로그 mean: search 7s, generate 5s. **둘 다 비싸지만 search가 generate 위에**. search 7초의 분해 진단이 P1.3 관찰성으로 가능. generate 5초는 LLM 자체 속도 — GPU 이전이 가장 큰 효과.

#### P4.2 "rewrite 28초"는 main 문제가 아니다

운영(main)에선 rewrite=0ms (122/122). 28초는 PR #24 환경의 문제. 자문 미팅에서 "rewrite 56%"를 언급할 때 **PR #24 환경 측정치임을 명확히** 해야 함.

#### P4.3 path=direct 24건 (16%)이 효과적

PR #25 게이트 적용 후에도 direct_answer 우회가 16%로 작동. mean 4.9초로 LLM 경유(5.1초)보다 약간만 빠름 — 큰 이점이 아닐 수도. 다만 **잘못된 direct_answer 차단**이 게이트의 진짜 가치(질).

---

## 종합 요약

1. **main 운영 환경의 LLM 호출은 단순함**: L1 (답변 LLM) 매 요청 + L3 (follow-up rewrite) 조건부. L2/L4는 본 트래픽에선 실질 dead. M1/M2는 매 요청 인-프로세스.
2. **팀원의 "rewrite 28초 / 97% rule_fallback"은 main이 아니라 PR #24 환경**. main 운영엔 그 문제 존재 불가 (rewrite 최대 0.8초).
3. **main의 즉시 액션 가능 작업**: 하드코딩 타임아웃 env 추출(P1.1), L2 발동률 운영 측정(P1.2).
4. **PR #24 머지 결정에 가장 중요한 데이터**: GPU 환경에서 understanding LLM 성공률 측정 (P3.1).
5. **자문 미팅 우선순위 재배치**: search latency 진단(인-프로세스 관찰성)이 main 기준에선 가장 큰 미해결 영역.

