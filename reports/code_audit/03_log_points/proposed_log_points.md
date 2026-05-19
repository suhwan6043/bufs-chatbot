# Step 3 — 로그 포인트 후보 (실제 삽입은 별도 PR)

**목적**: 5 hotspot 각각의 진입·분기·예외·종료 좌표에 로그를 심어 향후 데이터 흐름·이분 탐색의 빠른 진단 baseline 확보.

**대원칙**:
1. 신규 로그 prefix는 `[모듈명-시그]` (e.g. `[understand-call]`)로 통일 — 기존 `[understand-call]` 패턴 일관성 유지
2. logger.debug = 평소 OFF, logger.info = 평소 ON, logger.warning = 분기 실패
3. 메시지에 elapsed_ms·size_chars·count 같은 측정값 1~2개 포함 (자유 텍스트만 금지)
4. **direct_answer 트리거 좌표(chat.py L754-758, L1165-1169, context_merger:391-442)에는 로그 추가 금지** (사용자 약속)
5. 본 step에서는 **삽입하지 않고 좌표·메시지·레벨만 설계** — 별도 PR에서 적용

**측정일**: 2026-05-13
**5 hotspot**: chat.py(1,522 LOC) / query_understanding.py(566) / answer_generator.py(1,150) / query_analyzer.py(1,189) / context_merger.py(1,031)

---

## 1. chat.py 로그 포인트 (10좌표, hotspot #1)

| # | line | 단계 | 메시지 템플릿 | level | 비고 |
|---:|---:|---|---|---|---|
| 1 | 491-492 | event_generator 진입 | `[stream-in] sid=%s lang=%s test_mode=%s qlen=%d` | info | test 모드 확인 |
| 2 | 537 | Contact short-circuit (stream) | `[stream-contact] sid=%s elapsed=%dms answer_chars=%d` | info | 조기 종료 |
| 3 | 569 | understanding_enabled 분기 (stream) | `[stream-stageD] mode=%s prior_msgs=%d` (mode=llm/rule) | debug | Stage D 진입 |
| 4 | 596-600 | rewrite 적용 시 (stream) | `[stream-rewrite] src=%s reason=%s '%s' → '%s'` | info | (이미 존재, 형식 일치 확인) |
| 5 | 638-640 | clarification short-circuit | `[stream-clarify] sid=%s missing=%s msg_chars=%d` | info | 누락 필드 |
| 6 | 673-678 | P4 retry 트리거 | `[stream-retry] conf=%.2f direct_ans=%s tx_ctx=%s → trigger=%s` | info | retry AND 조건 가시화 |
| 7 | 712-719 | retry 채택 | `[stream-retry-accept] before=%.2f after=%.2f rewritten='%s'` | info | (이미 존재, 강화) |
| 8 | 786 | cache hit (stream) | `[stream-cache] hit sid=%s key_chars=%d` | info | cache hit 빈도 |
| 9 | 827 | _LLM_SEMAPHORE wait (stream) | `[stream-sem] acquire wait_ms=%d available=%d` | debug | 동시 LLM 큐잉 |
| 10 | 947-955 | PIPELINE_TIMING (이미 print) | (그대로) — print → logger.info 전환 권장 | info | print 일관성 |

추가 sync 좌표:
| # | line | 단계 | 메시지 템플릿 | level |
|---:|---:|---|---|---|
| 11 | 1019 | chat_sync 진입 | `[sync-in] sid=%s lang=%s test_mode=%s qlen=%d` | info |
| 12 | 1031 | Contact short-circuit (sync) | `[sync-contact] sid=%s elapsed=%dms` | info |
| 13 | 1051 | understanding_enabled 분기 (sync) | `[sync-stageD] mode=%s prior_msgs=%d` | debug |
| 14 | 1107 | clarification short-circuit (sync) | `[sync-clarify] sid=%s missing=%s` | info |
| 15 | 1135 | empty_context (sync) | `[sync-empty] intent=%s` | info |

**chat.py 좌표 합계: 15개**

---

## 2. query_understanding.py 로그 포인트 (8좌표, hotspot #2 — 56% 시간 점유)

이미 추가된 `[understand-call] OK/TIMEOUT/EXCEPTION/JSON_PARSE_FAIL/JSON_OK` 5개에 더해:

| # | line | 단계 | 메시지 템플릿 | level | 비고 |
|---:|---:|---|---|---|---|
| 16 | 509 | understand 진입 | `[understand-in] qlen=%d lang=%s history_turns=%d` | debug | 입력 메타 |
| 17 | 512-514 | 빈 쿼리 단축 | `[understand-empty] qlen=%d → rule_fallback` | debug | 안전 경로 |
| 18 | 526-532 | 1차 LLM 호출 직전 | `[understand-1st] model=%s timeout=%.1fs max_tokens=%d` | debug | 1차 진입 |
| 19 | 533-540 | 1차 LLM JSON 필수 필드 누락 | (이미 debug 존재) `[understand-1st-incomplete] missing keys=%s` | warning | 강화 |
| 20 | 547-553 | 2차 LLM 호출 직전 | `[understand-2nd] model=%s timeout=%.1fs` | debug | 2차 진입 |
| 21 | 554-561 | 2차 LLM JSON 필수 필드 누락 | (이미 debug 존재) `[understand-2nd-incomplete] missing=%s` | warning |
| 22 | 564-566 | rule_fallback 진입 | (이미 info 존재) — elapsed 추가: `+ elapsed=%dms` | info | 3단계 완전 실패 timing |
| 23 | 487-494 (`_rule_fallback`) | rule 분석 완료 | `[understand-rule-out] intent=%s qtype=%s student_id=%s` | info | rule 결과 가시화 |

**query_understanding.py 좌표 합계: 8개 (기존 5 + 신규 8 = 합계 13개 좌표 후보)**

**우선순위**: #16/#18/#20/#22 — 3단계 폴백 경로의 각 단계 진입 시점 정확히 표기. 현재는 OK/TIMEOUT/EXCEPTION만 후처리에서 확인 가능.

---

## 3. answer_generator.py 로그 포인트 (8좌표, hotspot #3)

| # | line | 단계 | 메시지 템플릿 | level | 비고 |
|---:|---:|---|---|---|---|
| 24 | 118-162 (`_make_cache_key`) | cache key 생성 | `[gen-cache-key] hash=%s share=%s chars=%d` | debug | cache 분포 |
| 25 | 164-179 (`get_cached_response`) | cache hit/miss | `[gen-cache] %s key_hash=%s` (HIT/MISS) | info | cache 효과 측정 |
| 26 | 197-257 (`_resolve_max_tokens`) | max_tokens 결정 분기 | `[gen-tokens] intent=%s qt=%s ctx_chars=%d → max=%d` | debug | token budget 가시화 |
| 27 | 300-599 (`_build_prompt`) 진입 | 프롬프트 빌드 | `[gen-prompt] intent=%s qt=%s ctx_chars=%d history=%d` | debug | 거대 함수의 진입점 |
| 28 | 300-599 (`_build_prompt`) 분기 종료 | 빌드 후 길이 | `[gen-prompt-out] total_chars=%d sections=%s` | debug | prompt size 회귀 감지 |
| 29 | 605-695 (`_stream_one_pass`) 진입 | 단일 스트림 호출 | `[gen-stream] model=%s lang=%s max_tokens=%d` | info | 모델별 timing 분리 |
| 30 | 605-695 (`_stream_one_pass`) 완료/오류 | LLM 호출 결과 | `[gen-stream-out] elapsed=%dms tokens=%d` 또는 `[gen-stream-err] %s` | info / warning | 답변 생성 elapsed |
| 31 | 699-772 (`rewrite_query`) | P4 retry용 LLM rewrite | `[gen-rewrite] '%s' → '%s' elapsed=%dms` | info | P4 retry 비용 |

**answer_generator.py 좌표 합계: 8개**

**우선순위**: #29 — 답변 생성 elapsed가 PIPELINE_TIMING의 generate=Xms와 일치하는지 검증. #28 — prompt size 회귀 감지 (KO prompt v1 토큰 2배 → 측정 필요).

---

## 4. query_analyzer.py 로그 포인트 (8좌표, hotspot #4)

| # | line | 단계 | 메시지 템플릿 | level | 비고 |
|---:|---:|---|---|---|---|
| 32 | 430-509 (`analyze`) 진입 | 분석 시작 | `[qa-in] qlen=%d` | debug | analyzer 진입 |
| 33 | 430-509 (`analyze`) 종료 | 분석 결과 | `[qa-out] intent=%s qt=%s student_id=%s entities=%d normalized='%s'` | info | analyzer 결과 |
| 34 | 511-747 (`_analyze_en`) 진입 | EN 분석 | `[qa-en-in] qlen=%d` | debug | EN 경로 |
| 35 | 511-747 (`_analyze_en`) 종료 | EN 결과 | `[qa-en-out] intent=%s qt=%s elapsed=%dms` | info | (CC 67 god) |
| 36 | 754-784 (`_extract_student_id`) | 학번 추출 | `[qa-sid] '%s' → %s` | debug | 정규식 50+ 진단 |
| 37 | 843-968 (`_classify_intent`) | intent 분류 결정 | `[qa-intent] decided=%s top_scores=%s` | debug | 분류 god (CC 42) 가시화 |
| 38 | 970-1067 (`_extract_entities`) | entity 추출 | `[qa-ent] keys=%s` | debug | (CC 34) |
| 39 | 1117-1189 (`_classify_question_type`) | qt 분류 | `[qa-qt] qt=%s top_sim=%.2f` | debug | embedding 분기 |

**query_analyzer.py 좌표 합계: 8개**

**우선순위**: #33 — `[qa-out]`이 chat.py:633(`_ms_analyze`)와 동치되는 결과 가시화. #37/#39 — 정확도 진단 시 어떤 분기로 갔는지 확인.

---

## 5. context_merger.py 로그 포인트 (8좌표, hotspot #5)

| # | line | 단계 | 메시지 템플릿 | level | 비고 |
|---:|---:|---|---|---|---|
| 40 | 141-181 (`_adaptive_cutoff`) | cutoff 결정 | `[cm-cut] intent=%s top_score=%.2f → cut=%.2f kept=%d` | debug | adaptive 분기 |
| 41 | 184-227 (`_rrf_merge`) 시작 | RRF 입력 | `[cm-rrf-in] vec=%d graph=%d` | debug | RRF 비교 baseline |
| 42 | 184-227 (`_rrf_merge`) 종료 | RRF 출력 | `[cm-rrf-out] merged=%d top_score=%.2f` | debug | (CC 9) |
| 43 | 236-609 (`merge`) 진입 | 병합 시작 | `[cm-merge-in] intent=%s qt=%s vec=%d graph=%d tx=%d` | info | (CC 75, 374 LOC god) |
| 44 | 236-609 (`merge`) 종료 | 병합 결과 | `[cm-merge-out] kept=%d ctx_chars=%d conf=%.2f direct_ans=%s` | info | merge 결과 |
| 45 | 612-728 (`_try_extract_direct_answer`) 진입 | direct_answer 시도 | `[cm-direct-in] intent=%s qt=%s` | debug | **❌ 무수정 (사용자 약속, L391-442)** → **로그 없이 둠** |
| 46 | 741-781 (`_filter_by_entity`) | entity 필터 | `[cm-ent-filter] target='%s' before=%d after=%d` | debug | entity 필터 효과 |
| 47 | 784-822 (`_strip_ocu_section`) | OCU 제거 | `[cm-ocu-strip] before_chars=%d after_chars=%d` | debug | OCU 분기 |
| 48 | 833-916 (`_slice_evidence_text`) | evidence 슬라이싱 | `[cm-slice] mode=%s before=%d after=%d` | debug | (CC 20) |

**context_merger.py 좌표 합계: 7개 (45번 제외)**

**우선순위**: #43/#44 — merge 진입/종료 정상 모니터링. #40 — adaptive cutoff 의도대로 동작하는지. **#45번 direct_answer 좌표는 audit에서 식별만 하고 무수정 약속 준수**.

---

## 6. 총괄 매트릭스

| Hotspot | 신규 좌표 수 | 우선 위치 | level 분포 |
|---|---:|---|---|
| chat.py | 15 | Stage D/F/G/H 경계 (#3, #6, #8, #9) | info 11 / debug 4 |
| query_understanding.py | 8 | 3단계 폴백 진입 (#16, #18, #20, #22) | info 4 / warning 2 / debug 2 |
| answer_generator.py | 8 | _stream_one_pass elapsed (#29, #30) | info 5 / debug 3 |
| query_analyzer.py | 8 | analyze 출력 (#33) | info 2 / debug 6 |
| context_merger.py | 7 | merge in/out (#43, #44) | info 2 / debug 5 |
| **합계** | **46** | — | info 24 / debug 20 / warning 2 |

**수용 조건 충족**: 5 hotspot × ≥6 좌표 = ≥30 ✓ (실제 46), 총 ≥40 ✓.

---

## 7. 적용 우선순위 (실제 삽입 시)

**Phase 1 — 즉시 (Step 5 이분 탐색 직전, 5 좌표만)**:
- #16 `[understand-in]` — 진입 메타
- #18 `[understand-1st]` — 1차 LLM 시작
- #20 `[understand-2nd]` — 2차 LLM 시작
- #29 `[gen-stream]` — 답변 생성 시작
- #43 `[cm-merge-in]` — 병합 시작

Phase 1 5건만 추가해도 PIPELINE_TIMING과 교차 검증 가능. **Step 5 토글 매트릭스 측정 후 P0 hotspot 진단 직접 데이터 확보**.

**Phase 2 — Step 7 종료 후**:
나머지 41 좌표 — 회귀 모니터링용 baseline.

---

## 8. 절대 무수정 좌표 (확인)

| 파일 | line | 이유 |
|---|---|---|
| `backend/routers/chat.py` | L754-758, L760-777 | direct_answer 트리거 (stream) — 사용자 약속 |
| `backend/routers/chat.py` | L1165-1169, L1170-1190 | direct_answer 트리거 (sync) — 사용자 약속 |
| `app/pipeline/context_merger.py` | L391-442 (`merge` 내 direct_answer 분기) | 사용자 약속 |

이 좌표들은 Step 3 로그 후보 목록에서 명시적으로 제외함.

---

## 9. Step 3 산출물 검증

| 산출물 | 상태 | 위치 |
|---|---|---|
| 5 hotspot × ≥6 좌표 | ✓ 평균 9.2 | (위 1~5절) |
| 총 ≥40 좌표 | ✓ **46** | (6절 매트릭스) |
| direct_answer 무수정 명시 | ✓ | (8절) |
| 실제 삽입 = 별도 PR | ✓ (이 문서는 설계만) | — |

## 10. 다음 Step (Step 4) 진입 조건

- 좌표 46개 식별 완료 ✓ — Step 4 데이터 흐름 스냅샷의 sample 기준점
- 9 stage 경계 vs 신규 로그 좌표 mapping 가능 ✓
- Step 4 신규 스크립트 `audit_data_flow.py` 설계 인풋 확보
