# Step 2 — chat_stream vs chat_sync diff 매트릭스

**비교 대상**:
- `chat_stream` + `_inner_generator`: chat.py:469-979, **511 LOC**, CC 76+73
- `chat_sync`: chat.py:1002-1339, **338 LOC**, CC 47

**합계 god LOC**: 849 (단일 라우터 파일의 56%)

## 1. 9 Stage × 2 경로 매트릭스

| Stage | 책임 | stream (line) | sync (line) | 동일? | 차이/결손 |
|---|---|---|---|---|---|
| A | 세션·언어·JWT·clarification reply | L520-532 | L1019-1027 | ≈ | sync에 `_ms_*` 변수 8개 사전 초기화 (L1017-1018, stream에 없음) |
| B | Contact short-circuit | L535-545 | L1030-1034 | ≈ | stream: yield event_dict; sync: return ChatResponse + `_log_chat_sync_timing` 호출 |
| C | 파이프라인 컴포넌트 확보 | L548-559 | L1036-1040 | **≠** | **stream에만 `if not all([...]) → error` 게이트 (L554-559). sync 결손**. |
| D | Understanding/Rewrite/Analyze | L562-633 (72 LOC) | L1042-1100 (59 LOC) | ≈ | 동일 if/else 2 경로 구조, stream에 logger.info 1건 추가 (L597-600 rewrite log) |
| E | Clarification 게이트 | L636-649 | L1103-1113 | **≠** | sync에 `_log_chat_sync_timing` 호출 없음 (timing 누락) |
| F | Search & Merge & **Retry** | L653-726 (74 LOC) | L1115-1130 (16 LOC) | **≠** | **sync에 P4 저신뢰 재시도 (74 LOC) 전체 결손**. _ms_retry 강제 0 |
| G | 빈 context / direct_answer / Cache | L729-808 | L1135-1219 | ≈ | stream: yield event; sync: return ChatResponse + path 라벨 timing. direct_answer 트리거 4중 AND 조건 동일 |
| H | LLM 생성 | L811-863 (53 LOC) | L1223-1244 (22 LOC) | **≠** | stream: 토큰별 yield + CLEAR + soft warn 즉시 yield; sync: 전체 수집 + soft warn prepend |
| I | 후처리 (빈 응답 / Phase 4 / Validator) | L865-922 | L1247-1306 | ≈ | 거의 1:1 동일. sync에 _t7 변수명 (stream의 _t6과 충돌 회피) |
| J | Footer / Cache 저장 / 메시지 / 로그 / 응답 | L925-977 (53 LOC) | L1309-1339 (31 LOC) | ≈ | stream: yield done + timing dict; sync: return ChatResponse + `_log_chat_sync_timing`(path="generated") |

## 2. 분기점 28(stream) vs 25(sync) 매핑

| # | 분기 내용 | stream | sync |
|---:|---|---|---|
| 1 | X-Test-Mode 헤더 분기 | L490 | L1013 |
| 2 | access_token → user_id 해석 | L524 | L1020 |
| 3 | clarification pending 재실행 | L528-530 | L1023-1026 |
| 4 | Contact short-circuit 조기 종료 | L536-545 | L1031-1034 |
| 5 | 파이프라인 컴포넌트 초기화 게이트 | **L554-559 (있음)** | **L1040 (없음)** |
| 6 | understanding_enabled 분기 | L569 | L1051 |
| 7 | EN 강제 (understand 경로) | L582-583 | L1062-1063 |
| 8 | EN 강제 (rule 경로) | L628-629 | L1095-1096 |
| 9 | rewrite_enabled and follow_up_signal.is_follow_up | L606-623 | L1079-1091 |
| 10 | `_enrich_analysis` 호출 (2 경로 각 1회) | L584-586, L630-632 | L1064-1066, L1097-1099 |
| 11 | clarification 필수 필드 누락 게이트 | L640-649 | L1107-1113 |
| 12 | glossary 정규화 (normalized_query or search_query) | L654 | L1116 |
| 13 | P4 저신뢰 재시도 4중 AND | **L673-678 (있음)** | **(없음)** |
| 14 | retry merged_retry.context_confidence 채택 | **L712-719 (있음)** | **(없음)** |
| 15 | 빈 context 거부 (KO/EN) | L729-742 | L1135-1148 |
| 16 | **direct_answer 트리거 4중 AND** ★ 무수정 | L754-758 | L1165-1169 |
| 17 | cache hit | L786-808 | L1198-1219 |
| 18 | _LLM_SEMAPHORE 백프레셔 | L827 | L1225 |
| 19 | CLEAR 토큰 처리 (EN 원패스) | L842-851 (yield clear + warn 주입) | L1240-1242 (full_answer="") |
| 20 | LLM 생성 예외 | L856-862 (yield error + return) | (try/except 없음 — 외부 전파) |
| 21 | 빈 LLM 응답 방어 (KO/EN) | L866-871 | L1247-1252 |
| 22 | soft_warn 출력 위치 | **L820-824 (즉시 yield)** | **L1255-1259 (prepend)** |
| 23 | Phase 4 KO 게이트 lang!="en" | L877 | L1266 |
| 24 | refusal skip (_is_no_context_response) | L887 | L1275 |
| 25 | verify_answer_against_context 환각 | L889-895 | L1276-1282 |
| 26 | verify_completeness + fill_from_context | L897-905 | L1284-1290 |
| 27 | validator warnings append | L917-919 | L1301-1303 |
| 28 | footer append | L925-927 | L1309-1311 |

★ = direct_answer 트리거 (사용자 약속: 무수정)
**≠ 항목 4건**: #5 (sync 결손), #13/#14 (sync 결손, retry 전체), #20 (sync 결손 — try/except 없음), #22 (UX 차이는 본질)

## 3. 중복 LOC 추정 (HEAD 기준)

| Stage | stream LOC | sync LOC | 중복 LOC (≈) | 비중 |
|---|---:|---:|---:|---:|
| A | 13 | 9 | 9 | 100% |
| B | 11 | 5 | 5 | 100% |
| C | 12 | 5 | 5 | 100% |
| D | 72 | 59 | 56 | 95% |
| E | 14 | 11 | 11 | 100% |
| F | 74 | 16 | 16 | 100% (sync 부분만) |
| G | 80 | 85 | 76 | 90% |
| H | 53 | 22 | 22 | (모듈화 가능 80% / 본질 차이 20%) |
| I | 58 | 60 | 56 | 95% |
| J | 53 | 31 | 28 | (timing/응답 객체 본질 차이) |
| **합계** | **440** | **303** | **284** | **약 90% 중복 (의도, P0 분석 일치)** |

※ chat_stream 본체(L469-512, 45 LOC) + event_generator(20) + `_inner_generator` 외장 분리는 차이의 본질이 아닌 wrapper 패턴.

## 4. UX 차이 (본질 vs 우연)

| 차이 | 본질? | 분리 가능? |
|---|---|---|
| token 별 yield vs 전체 수집 | **본질** | 단일 generator 함수 + caller가 모드별 소비 (`async for token in _run_pipeline()`)로 통합 가능 |
| soft warn 즉시 yield vs prepend | 본질 (UX 의도) | 동일 — caller가 처리 |
| CLEAR 토큰 + 토큰 yield 일시 중단 | 본질 (EN 원패스) | 동일 |
| ChatResponse(Pydantic) vs done event_dict | 본질 | wrapper 2개로 분리 |
| P4 retry 누락 (Stage F) | **우연 (sync 결손)** | **수정 필요 — sync에도 적용** |
| 컴포넌트 초기화 게이트 누락 (Stage C) | **우연 (sync 결손)** | **수정 필요** |
| LLM try/except 누락 (Stage H) | **우연 (sync 결손)** | **수정 필요** |
| Clarification timing 누락 (Stage E) | **우연 (sync 결손)** | **수정 필요** |

## 5. 통합 리팩토링 후보 (Step 7 인풋)

**P0 추출 1순위 — `_run_pipeline(mode: str)` 함수**:

```python
async def _run_pipeline(
    *, request, session_id, question, user_id, mode: str,
    yield_token_event=None,  # callable(str) 또는 None (전체 수집)
) -> tuple[str, dict]:  # (full_answer, metadata) 또는 yield 통해 streaming
    # Stage A-J 공통 로직 (필요 시 yield 사용)
    # mode "stream" / "sync"별 차이:
    #  - return 형식: 호출자가 ChatResponse / EventSourceResponse 변환
    #  - token 처리: callable이면 즉시 호출, 없으면 누적
```

기대 효과:
- chat_stream → 50 LOC (인자 처리 + EventSourceResponse 래핑)
- chat_sync → 30 LOC (인자 처리 + ChatResponse 빌드)
- 공통 `_run_pipeline` → 약 380 LOC (Stage A-J)
- 총 460 LOC (현재 849에서 -389 LOC = **46% 감소**)
- sync 결손 4건 자동 해소

## 6. Step 2 산출물 검증

| 산출물 | 상태 | 위치 |
|---|---|---|
| `chat_funcs.json` 24 함수 좌표 | ✓ | `02_entry_points/chat_funcs.json` |
| `stream_flow.md` 9 stage + 28 분기 | ✓ | `02_entry_points/stream_flow.md` |
| `sync_flow.md` 9 stage + 25 분기 | ✓ | `02_entry_points/sync_flow.md` |
| `diff_matrix.md` (이 문서) — 28+25=53 분기 매핑, 중복 추정 90% | ✓ | `02_entry_points/diff_matrix.md` |
| 분기점 ≥18개 (요구) | ✓ **53** (stream 28 + sync 25) | — |
| 중복 LOC 추정치 | ✓ **284 LOC ≈ 90%** | — |

## 7. 다음 Step (Step 3) 진입 조건

- 9 stage 경계 식별 ✓ — 로그 좌표 후보의 입출 지점
- god function 3개 (chat_stream/_inner_generator/chat_sync) 분기 53개 식별 ✓
- direct_answer 트리거 좌표 (L754-758, L1165-1169) 재확인 — Step 3 로그 좌표에서 무수정 명시 예정
