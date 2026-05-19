# Step 2 — chat_sync 호출 트리

**대상**: `backend/routers/chat.py:1002-1339` (`chat_sync`)
**총 LOC**: 338
**CC**: 47 (god function, Top 16)

## 0. 진입점 트리

```
POST /api/chat  ─── FastAPI route, response_model=ChatResponse
└─ chat_sync(request, session_id, question, access_token)   [L1002-1339, async]
    └── (아래 9 stage 순차)
    ※ stream과 달리 generator/yield 없음, 한 번에 ChatResponse 반환
```

## 1. 9 Stage 흐름 (분기점 포함)

### Stage A — 초기화·세션·언어·JWT 해석 (L1012-1027)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 1012-1014 | _is_test = X-Test-Mode 헤더 → set_skip_log | set_skip_log | **#1** test 모드 분기 |
| 1016 | _t0 = time.monotonic() | | |
| 1017-1018 | _ms_* 변수 8개 0 초기화 | | (stream에는 없음 — preset) |
| 1019 | sid, session_data = session_store.get_or_create(session_id) | session_store | |
| 1020 | user_id = `_resolve_user_id`(access_token) | `_resolve_user_id` (L58) | **#2** access_token → user_id |
| 1023-1026 | _current_lang + `_handle_clarification_reply` | `_handle_clarification_reply` (L155) | **#3** pending 재실행 |
| 1027 | question = effective_question | (재할당, **nonlocal 불필요 — 함수 인자** ) | — |

### Stage B — Contact short-circuit (L1030-1034)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 1030 | contact = `_format_contact_answer`(question, lang) | `_format_contact_answer` (L78) | (내부) |
| 1031-1034 | if contact → `_try_log_simple` + `_log_chat_sync_timing` + **return ChatResponse**(answer, intent="CONTACT", duration_ms) | `_try_log_simple` + `_log_chat_sync_timing` (L984) | **#4** Contact 조기 종료 |

### Stage C — 파이프라인 컴포넌트 확보 (L1036-1040)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 1036-1040 | analyzer/router_inst/merger/generator/validator | get_* lazy | (없음) |
| **(없음)** | **stream과 달리 `if not all([...]) → error` 게이트가 sync에는 없음** | — | **★ 결손**: 컴포넌트 미초기화 상태에서 진행 시 AttributeError 가능 |

### Stage D — Understanding/Rewrite/Analyze (L1042-1100)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 1043-1048 | follow_up_detector/query_rewriter import + prior_messages + lang | | |
| **1051** | **if _conv_cfg.understanding_enabled:** | settings.conversation | **#5 ★ multi-task 1 분기점** (stream과 대칭) |
| 1055-1057 | query_understanding.understand(...) | `query_understanding.understand` | (3단계 폴백) |
| 1059-1061 | unpack: follow_up_signal / search_query / analysis | | |
| 1062-1063 | if lang == "en": analysis.lang = "en" | | **#6** EN 강제 |
| 1064-1066 | `_enrich_analysis`(...) | `_enrich_analysis` (L233, CC 49) | **#7** transcript/profile |
| 1067-1069 | _ms_follow_up=0, _ms_rewrite=_ms_understand, _ms_analyze=0 | | |
| **1075** | **else (rule 경로):** | | **#8 ★ 분기점** |
| 1077-1091 | follow_up_detector.detect + (옵션) query_rewriter.rewrite | | **#9** rewrite 분기 |
| 1094 | analysis = analyzer.analyze(search_query) | QueryAnalyzer.analyze | (내부 분기 폭주) |
| 1095-1096 | if lang == "en": analysis.lang = "en" | | **#10** EN 강제 (rule) |
| 1097-1099 | `_enrich_analysis`(...) | (재호출) | (분기 두 번째 인스턴스) |

### Stage E — Clarification 게이트 (L1103-1113)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 1104-1106 | `_check_clarification_gate`(...) | `_check_clarification_gate` (L195) | **#11** 필수 필드 |
| 1107-1113 | if _clarify_msg_sync → `_try_log_simple` + **return ChatResponse**(intent="CLARIFICATION") | | **#12** clarification 조기 종료 |
| **(없음)** | **stream과 달리 `_log_chat_sync_timing` 호출 없음** | — | **★ timing 누락** (Stage E clarification 경로) |

### Stage F — Search & Merge (L1115-1130)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 1116 | _search_query = analysis.normalized_query or search_query | (or) | **#13** glossary |
| 1117-1119 | _t4; search_results = router_inst.route_and_search; _ms_search | router_inst | (내부) |
| 1120-1130 | _t5; merger.merge(...); _ms_merge | ContextMerger.merge | (내부 RRF) |
| **(없음)** | **stream에는 있는 P4 저신뢰 재시도 (L673-726, 4중 AND, _ms_retry) — sync에는 없음** | — | **★ 결손**: confidence<0.3 retry 미적용. _ms_retry=0ms로 PIPELINE_TIMING 출력 |

### Stage G — 빈 context / direct_answer / Cache (L1135-1219)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 1135-1161 | if not merged.formatted_context.strip() → 거부 메시지 + `_log_chat_sync_timing`(path="empty_context") + return | `_log_chat_sync_timing` | **#14** 빈 context |
| **1165-1169** | **direct_answer 트리거** (4중 AND: settings.pipeline.direct_answer_bypass_llm & direct_answer & lang!="en") | settings.pipeline | **#15 ★★ 절대 수정 금지 (사용자 약속)** |
| 1170-1190 | direct_answer: messages.append + `_log_chat_sync_timing`(path="direct_answer") + return ChatResponse(source_urls=...) | session_store.update | (LLM 우회) |
| 1192-1219 | cache_kwargs + cached_answer = generator.get_cached_response → if 있으면 path="cached" return | `_build_generation_cache_kwargs` | **#16** cache hit |

### Stage H — LLM 전체 수집 (L1223-1244)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 1223 | _t6 = time.monotonic() | | |
| **1225** | **async with _LLM_SEMAPHORE:** (PR #22, OOM 방어) | asyncio.Semaphore | **#17 ★** 동시 LLM 상한 (semaphore wait + generate 합산이 _ms_gen에 포함, HEAD 의도) |
| 1226-1239 | async for token in generator.generate(...): full_answer += token | AnswerGenerator.generate | (스트림이 아닌 누적) |
| 1240-1242 | if token == "\x00CLEAR\x00": full_answer="" | | **#18** CLEAR 리셋 |
| 1244 | _ms_gen = int((time.monotonic() - _t6) * 1000) | | |
| **(없음)** | **stream과 달리 toekn별 yield 없음 — 전체 수집 후 한 번 반환** | — | (논스트리밍 본질) |

### Stage I — 후처리: 빈응답 방어 / soft warn prepend / Phase 4 / Validator (L1247-1306)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 1247-1252 | if not full_answer.strip() → 기본 메시지 (KO/EN) | | **#19** 빈 LLM |
| 1255-1259 | if _soft_warn_fields_sync → `clarification.build_soft_warning` prepend | clarification | **#20** soft warn (KO/EN) — **stream과 반대로 prepend** |
| 1262 | ~ 이스케이프 (re.sub) | re | |
| 1264 | _t7 = time.monotonic() | | |
| 1266 | **if analysis.lang != "en" and full_answer.strip():** | | **#21 ★** KO Phase 4 게이트 |
| 1275 | if not _rv._is_no_context_response(full_answer): | ResponseValidator | **#22** refusal skip |
| 1276-1290 | verify_answer_against_context → 환각이면 거부; verify_completeness → fill_from_context | | **#23** 환각/누락 |
| 1295-1305 | validator.validate(...) → warnings append | ResponseValidator | **#24** validator warnings |
| 1306 | _ms_val = int((time.monotonic() - _t7) * 1000) | | |

### Stage J — Footer + Cache 저장 + 메시지 이력 + 로그 + return (L1309-1339)

| line | 동작 | 호출 | 분기 |
|---:|---|---|---|
| 1309-1311 | footer = `_get_contact_footer` → append | `_get_contact_footer` (L115) | **#25** |
| 1313 | generator.store_cached_response(...) | | |
| 1316-1319 | messages.append (user + assistant) + session_store.update | | |
| 1322 | `_try_log`(question, full_answer, sid, analysis, _t0, context_confidence, user_id) | `_try_log` (L1436) | |
| 1324-1331 | total_ms = `_log_chat_sync_timing`(path="generated", ...) | `_log_chat_sync_timing` (L984) | (PIPELINE_TIMING + endpoint=sync) |
| 1332-1339 | **return ChatResponse**(answer, source_urls, results=[SearchResultItem(...) for ...], intent, duration_ms) | SearchResultItem(L?) | (응답 단일 객체) |

## 2. 호출 정리

**chat_sync 내부 호출 (24개 함수 중)**:
- `_resolve_user_id` (L58)
- `_format_contact_answer` (L78)
- `_get_contact_footer` (L115)
- `_handle_clarification_reply` (L155)
- `_check_clarification_gate` (L195)
- `_enrich_analysis` (L233) — **2회 호출** (understand/rule)
- `_serialize_results` (L398) — **2회 호출** (cache return + 최종 return)
- `_build_generation_cache_kwargs` (L421)
- `_log_chat_sync_timing` (L984) — **5회 호출** (contact / empty_context / direct_answer / cached / generated)
- `_try_log` / `_try_log_simple`

**외부 호출 (31종)** — stream과 비교 시 적음:
- ChatResponse / SearchResultItem / SourceURL (Pydantic 모델)
- print (PIPELINE_TIMING via `_log_chat_sync_timing`)
- 기타 stream과 공통

## 3. stream과의 결손 4건

| 결손 | stream 라인 | sync 미적용 | 영향 |
|---|---|---|---|
| `if not all([...]) → error` 게이트 | L554-559 | Stage C 누락 | 컴포넌트 미초기화 시 AttributeError 가능 |
| P4 저신뢰 재시도 (confidence<0.3) | L673-726 | Stage F 누락 | _ms_retry=0ms 강제, 재시도 안 됨 |
| Clarification 경로 timing 로그 | L640-649 (자체 timing 없음) | Stage E의 `_log_chat_sync_timing` 호출 누락 | PIPELINE_TIMING 부재 |
| 스트리밍 token yield + soft warn 즉시 yield | L823 | Stage H/I (전체 수집 + prepend) | UX 차이 — 본질 |

## 4. 핵심 발견

1. **9 Stage, 분기점 25개** — stream(28) 대비 -3. 차이는 (Stage C 게이트 / Stage F retry / Stage E timing).
2. **chat_sync는 함수 한 덩어리 338 LOC** — stream의 `event_generator` + `_inner_generator` 분리 패턴 없음.
3. **`_log_chat_sync_timing`이 5번 호출**되어 path 라벨(contact/empty_context/direct_answer/cached/generated)로 stream의 단일 PIPELINE_TIMING을 분기 추적. 좋은 패턴.
4. **direct_answer 트리거 (Stage G L1165-1190)** — stream과 동일, 무수정.
5. **stream과 90% 중복** — Stage A·B·C·D·E·F·G·H·I·J 모두 거의 1:1 대응. `_run_pipeline(mode="stream"|"sync")` 추출 후보 1순위.
