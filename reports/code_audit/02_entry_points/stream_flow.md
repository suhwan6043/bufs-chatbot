# Step 2 — chat_stream 호출 트리

**대상**: `backend/routers/chat.py:469-979` (`chat_stream` + `event_generator` + `_inner_generator`)
**총 LOC**: 511 (chat_stream 래퍼 45 + event_generator 20 + _inner_generator 464)
**CC**: 76 / 4 / **73** — _inner_generator가 god function

## 0. 진입점 트리 (Caller → Callee)

```
GET /api/chat/stream  ─── FastAPI route
└─ chat_stream(request, session_id, question, access_token)   [L469-512, async]
    ├─ set_skip_log(_is_test)                                  [L490]   X-Test-Mode 분기
    └─ event_generator()                                       [L493-512, AsyncGenerator]
        ├─ session_store.get_or_create(session_id)             [L499]   ※ peek (lang 사전조회)
        ├─ async for event in _inner_generator(_t0):           [L505]
        └─ except Exception → yield error event                [L507-512]

_inner_generator(_t0)                                          [L514-977, async, CC 73]
└── (아래 9 stage 순차)
```

## 1. 9 Stage 흐름 (분기점 포함)

### Stage A — 세션·언어·JWT 해석 (L520-532)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 521 | session_store.get_or_create(session_id) | session_store | (없음) |
| 524 | user_id = _resolve_user_id(access_token) | `_resolve_user_id` (L58) | **#1** access_token None/JWT 만료 → user_id=None (개인 DB 스킵) |
| 527 | _current_lang = session_data.get("lang","ko") | dict | (없음) |
| 528-530 | _handle_clarification_reply(...) | `_handle_clarification_reply` (L155, CC 12) | **#2** pending 있으면 원질문 재실행 |
| 532 | question = effective_question | (재할당) | **closure nonlocal** (L518) |

### Stage B — Contact short-circuit (L535-545)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 535 | contact_answer = `_format_contact_answer` (L78, CC 9) | dept_searcher | (내부 분기) |
| 536-545 | if contact_answer → `_try_log_simple` + yield "done" + **return** | `_try_log_simple` (L1494) | **#3** Contact 조기 종료 (LLM 우회) |

### Stage C — 파이프라인 컴포넌트 확보 (L548-559)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 548-552 | analyzer / router_inst / merger / generator / validator | `get_analyzer`, `get_router`, `get_merger`, `get_generator`, `get_validator` | (없음) |
| 554-559 | if not all([...]) → yield error + **return** | dependencies | **#4** 초기화 실패 차단 |

### Stage D — Understanding/Rewrite/Analyze (L562-633) ★ 56% 시간 점유

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 562-567 | follow_up_detector / query_rewriter import + prior_messages | (lazy import) | (없음) |
| **569** | **if _conv_cfg.understanding_enabled:** | `_settings.conversation` | **#5 ★ multi-task 1 분기점** |
| 573-577 | query_understanding.understand(question, prior_messages, lang) | `query_understanding.understand` | **3단계 폴백** (gemma3:4b → llm.model → rule) |
| 579-581 | follow_up_signal / search_query / analysis = _understand.* | (unpack) | (없음) |
| 582-583 | if lang == "en": analysis.lang = "en" | (단순 분기) | **#6** EN 강제 분기 |
| 584-586 | analysis, transcript_context, student_context = `_enrich_analysis`(...) | `_enrich_analysis` (L233, CC 49) | **#7** 내부 transcript/profile 분기 |
| 588-590 | _ms_follow_up=0, _ms_rewrite=_ms_understand, _ms_analyze=0 | (할당) | (timing 단순화) |
| **601** | **else (rule-based 경로):** | | **#8 ★ 분기점** |
| 603 | follow_up_signal = follow_up_detector.detect(...) | follow_up_detector | (내부) |
| 606-623 | if rewrite_enabled and follow_up_signal.is_follow_up: query_rewriter.rewrite | query_rewriter | **#9** rewrite skip 조건 |
| 627 | analysis = analyzer.analyze(search_query) | QueryAnalyzer.analyze | (1,189 LOC 내부 분기 폭주) |
| 628-629 | if lang == "en": analysis.lang = "en" | | **#10** EN 강제 (rule 경로) |
| 630-632 | _enrich_analysis(...) | `_enrich_analysis` (L233, CC 49) | (Stage A의 #7과 동일 함수 두 번째 호출 분기) |

### Stage E — Clarification 게이트 (L636-649)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 637-639 | _check_clarification_gate(...) | `_check_clarification_gate` (L195, CC 5) | **#11** 필수 필드 누락 |
| 640-649 | if _clarify_msg → `_try_log_simple` + yield done + **return** | _try_log_simple | **#12** clarification 조기 종료 |

### Stage F — Search & Merge & Retry (L653-726)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 654 | _search_query = analysis.normalized_query or search_query | (or) | **#13** glossary 정규화 |
| 655 | search_results = router_inst.route_and_search(...) | QueryRouter | (내부 BM25+vector+graph 분기) |
| 660-668 | merged = merger.merge(...) | ContextMerger.merge (CC 75, 374 LOC) | (내부 RRF+cutoff+budget) |
| **673-678** | **P4 저신뢰 재시도**: confidence<0.3 & not direct_answer & not transcript_context | | **#14 ★ 4중 AND 조건** |
| 680-684 | generator.rewrite_query(...) | AnswerGenerator.rewrite_query | (LLM 호출) |
| 685-723 | retry_results 병합 (중복 제거) + 다시 merger.merge | (set 중복 제거 2회) | (성능 비용) |
| 712-719 | if merged_retry.context_confidence > merged.context_confidence: 채택 | | **#15** retry 채택 조건 |

### Stage G — 빈 context 거부 / direct_answer 우회 / Cache (L729-808)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 729-749 | if not merged.formatted_context.strip() → 거부 메시지 + yield done + return | `_try_log` (L1436) | **#16** 빈 context (KO/EN 분기) |
| **754-758** | **direct_answer 트리거** (4중 AND: DIRECT_ANSWER_BYPASS_LLM=true & direct_answer & lang!="en") | settings.pipeline | **#17 ★★ 절대 수정 금지 (사용자 약속)** |
| 760-777 | direct_answer: messages.append + yield done + **return** | session_store.update | (LLM 우회) |
| 779-808 | cache_kwargs = `_build_generation_cache_kwargs` + cached_answer = generator.get_cached_response | `_build_generation_cache_kwargs` (L421) | **#18** cache hit → 조기 종료 |

### Stage H — LLM 스트리밍 생성 (L811-863)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 811 | _t5 = time.monotonic() | | |
| 815-824 | _soft_warn_fields → soft warning 토큰 즉시 yield (KO만) | `clarification.build_soft_warning` | **#19** KO/EN 분기 |
| 827 | **async with _LLM_SEMAPHORE:** (백프레셔, OOM 방어) | asyncio.Semaphore | **#20 ★** 동시 LLM 상한 큐잉 |
| 828-841 | async for token in generator.generate(...) | AnswerGenerator.generate (yields tokens) | (LLM 스트리밍) |
| 842-851 | if token == "\x00CLEAR\x00" → yield clear + warning 주입 | | **#21** EN 원패스 CLEAR 신호 |
| 852-855 | else: full_answer += token; yield token event | | |
| 856-862 | except Exception → yield error + return | | **#22** 생성 예외 |

### Stage I — 후처리: 빈응답 방어 / 환각검증 / Validator / Footer / Cache 저장 (L865-944)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 866-871 | if not full_answer.strip() → 기본 메시지 (KO/EN) | | **#23** 빈 LLM 응답 |
| 874 | ~ 이스케이프 (re.sub) | re | (단일 분기 없음) |
| 877 | **if analysis.lang != "en" and full_answer.strip():** | | **#24 ★** KO 전용 Phase 4 품질 게이트 |
| 879-883 | answer_units 3종 + ResponseValidator import | (lazy) | |
| 887 | if not _rv._is_no_context_response(full_answer): | | **#25** refusal 응답 skip |
| 889-905 | verify_answer_against_context → 환각 시 거부 메시지; verify_completeness → fill_from_context | | **#26** 환각/누락 |
| 910-922 | validator.validate(...) → warnings 있으면 append | ResponseValidator | **#27** validator warnings |
| 925-927 | footer = `_get_contact_footer` (L115) → append | `_get_contact_footer` | **#28** 학사/학과 연락처 첨부 |
| 929 | generator.store_cached_response(...) | | (cache write) |
| 932-940 | messages.append + session_store.update | | |
| 943 | `_try_log`(question, full_answer, sid, analysis, _t0, context_confidence, user_id) | | |

### Stage J — Timing 로그 + done 이벤트 (L946-977)

| line | 동작 | 분기 |
|---:|---|---|
| 947-955 | PIPELINE_TIMING print (follow_up/rewrite/analyze/search/merge/retry/generate/validate) | (없음) |
| 958-977 | yield done {answer, source_urls, results, intent, duration_ms, **timing**:{...}} | (없음) |

## 2. 호출 정리

**chat_stream 내부 호출 (24개 함수 중)**:
- `_resolve_user_id` (L58)
- `_format_contact_answer` (L78)
- `_get_contact_footer` (L115)
- `_handle_clarification_reply` (L155)
- `_check_clarification_gate` (L195)
- `_enrich_analysis` (L233) — **2회 호출 (understand/rule 경로 각 1)**
- `_serialize_results` (L398) — **3회 호출 (direct_answer / cache / 최종 done)**
- `_build_generation_cache_kwargs` (L421)
- `_try_log` (L1436), `_try_log_simple` (L1494)

**외부 호출 (39종)** — 주요:
- `session_store.get_or_create / update`
- `get_analyzer / get_router / get_merger / get_generator / get_validator` (lazy 싱글톤)
- `query_understanding.understand` / `follow_up_detector.detect` / `query_rewriter.rewrite`
- `router_inst.route_and_search`
- `merger.merge`
- `generator.rewrite_query / generate / get_cached_response / store_cached_response`
- `validator.validate`
- `clarification.build_soft_warning`
- `_LLM_SEMAPHORE` (asyncio.Semaphore)
- print (PIPELINE_TIMING)
- json.dumps, time.monotonic, re.sub

## 3. 핵심 발견

1. **9 Stage, 분기점 28개** — 평균 stage 당 3.1 분기. CC 73이 9 stage에 걸쳐 산포.
2. **`_inner_generator`만 분리**해도 `chat_stream` 본체는 45 LOC. 분리 정당.
3. **Stage D (Understanding/Rewrite/Analyze)가 if/else 2 경로 88 LOC** — multi-task 1 후 정리되지 않은 dead branch (rule 경로). 56% 시간 점유 구간.
4. **direct_answer 트리거 (Stage G L754-777)** — 사용자 약속에 따라 무수정. audit에서도 무수정 확인. 28 분기 중 17번.
5. **`_enrich_analysis` (CC 49, 112 LOC)** — Stage D에서 2 경로 모두 호출. 분리 우선순위 P1.
