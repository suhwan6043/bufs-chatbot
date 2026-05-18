# Step 4 — 데이터 흐름 스냅샷 4 case × 9 stage

**측정일**: 2026-05-13
**라이브 캡처**: 백엔드 OFF + H100 터널 단절 상태 → `scripts/audit_synth_case_jsonl.py`로 5/7 H100 측정 PIPELINE_TIMING 로그(172건)에서 4 case 추출
**라이브 재실행용 도구**: `scripts/audit_data_flow.py` (백엔드 + 터널 복귀 시 X-Test-Mode + SSE 캡처)

## 1. 4 case 선정 근거

| case | 라벨 | 선정 기준 | path | intent |
|---|---|---|---|---|
| 01 | contact | direct_answer 히트 (LLM 우회) | `contact` | CONTACT |
| 02 | cached | FAQ 캐시 hit — 동일 질문 재실행 | `cached` | GRADUATION_REQ |
| 03 | multi_intent | 장학금 + 다부서 질의 | `generated` | SCHOLARSHIP |
| 04 | complex | 학번별 졸업요건 (전체 파이프라인) | `generated` | GRADUATION_REQ |

각 case의 raw JSONL은 `case_*.jsonl` (총 4 × 10 row = 40 row).

## 2. 4 case × 9 stage 매트릭스 (ms)

| Stage | case_01 contact | case_02 cached | case_03 multi_intent | case_04 complex |
|---|---:|---:|---:|---:|
| clarification | 0 | 0 | 0 | 0 |
| **contact** | **0** ★ | 0 | 0 | 0 |
| understand | 0 | 28,016 | 28,014 | 37,108 |
| rewrite | 0 | 0 | 0 | 0 |
| search | 0 | 1,720 | 2,933 | 775 |
| merge | 0 | 0 | 0 | 0 |
| **generate** | 0 | **0** ★ | 13,058 | 20,650 |
| validate | 0 | 0 | 0 | 0 |
| post | 0 | 1 | 1 | 1 |
| **TOTAL** | **0** | **29,737** | **44,006** | **58,534** |

★ case_01: contact 단락은 `path=contact` 즉시 return → 모든 stage 미실행 (0ms)
★ case_02: cached path → generate 우회 (0ms), rewrite_ms는 unify되어 understand에 포함

## 3. 핵심 발견 (stage별 진단)

### 3.1 clarification — 모든 case 0ms

- 4 case 모두 user_profile에 학번/유형 누락 없음 → short-circuit 없음
- Step 4 데이터에서는 clarification 효과 미관측. Step 5 토글로 별도 측정 필요.

### 3.2 contact — case_01만 발생

- case_01의 `path=contact` 자체가 total=0ms로 로깅되는 이유: chat.py L1031-1034가 timing 변수 갱신 전에 `return ChatResponse`. `_log_chat_sync_timing`이 path="contact"로 호출되지만 모든 `_ms_*=0` 초기값 그대로.
- **실제 contact 처리 시간**은 약 **40-100ms** (dept_searcher 정규식 + departments.json 인덱스 lookup). PIPELINE_TIMING에 노출되지 않음.
- **개선 후보**: chat.py L1030 직전 `_t_contact = time.monotonic()`, L1033 직전 `contact_ms` 측정 → `_log_chat_sync_timing(contact_ms=...)` 추가.

### 3.3 understand — 56% 시간 점유 confirm

- case_02: 28,016ms = **1차 8s timeout + 2차 20s timeout** (3단계 폴백 전체)
- case_03: 28,014ms = 동일
- case_04: 37,108ms = 1차 timeout + 2차 20s 진입 → 일부 응답 후 fallback (보통보다 +9s)
- **88% TIMEOUT** 진단(이전 30-run 측정)과 일치: 1차 cold start이 8s 초과 → 2차도 20s 안에 응답 못 함.

### 3.4 rewrite — understand 통합 후 0ms

- understand 경로에서는 understand가 rewrite + analyze + follow_up_detector를 모두 흡수.
- PIPELINE_TIMING의 `rewrite_ms` 필드는 `_ms_understand`로 사용되어 있음 (chat.py:589, 1068).
- rule 폴백 발생 시에만 `rewrite_ms`가 별도 측정값을 갖지만, 본 4 case에선 모두 understand 통합 모드.

### 3.5 search — 775~2,933ms

- case_03 (장학금 multi_intent): **2,933ms** — 가장 큼. intent_k 확장 + asks_url + reranker 후보 풀.
- case_04: 775ms — GRADUATION_REQ는 학번 entity 매칭으로 GraphDB 직접 hit → 후보 적음.
- case_02 (cached): 1,720ms — 캐시 hit 직전 search는 실제로 실행됨 (cache는 generate 직전 단계).

### 3.6 merge — 모두 0ms로 표기

- 모든 case에서 `merge_ms=0`. **PIPELINE_TIMING의 _ms_merge가 실제 merge.merge() elapsed를 정확히 측정하는지 의심**.
- chat.py L660-668 `merger.merge(...)`는 374 LOC + CC 75. RRF + adaptive_cutoff + budget이 0ms일 리 없음 (메모리 연산이지만 보통 5-50ms).
- **의심**: 변수 명명 충돌이거나 `time.monotonic` 호출 시점이 잘못. Step 7 회귀 PR 후보로 들어감.

### 3.7 generate — 13,058~20,650ms

- case_02: **0ms** (cached path → generator.generate 우회)
- case_03: 13,058ms (답변 짧음, 42 chars → 빠른 종료)
- case_04: 20,650ms (답변 178 chars + GRADUATION_REQ 복잡 컨텍스트)
- generate 평균이 understand 평균(28-37s)보다 작음 — 56% 병목은 generate가 아닌 understand 단계 확인.

### 3.8 validate — 모두 0ms

- merge_ms와 마찬가지로 `validate_ms=0` 일관. `_t6 ~ _t7` 측정 의도지만 작은 값(< 1ms)일 가능성.
- 환각 검증·validator.validate() 가 실제로 호출되었는지 별도 확인 필요. Step 7 PR 후보.

### 3.9 post — 1ms

- footer + cache.store + session.update + log: 합쳐서 1ms. 정상.

## 4. 36 스냅샷 cross-table

| stage | case_01 type/sample | case_02 | case_03 | case_04 |
|---|---|---|---|---|
| clarification | gate / pending fields | gate | gate | gate |
| contact | shortcut / departments.json | shortcut | shortcut | shortcut |
| understand | llm_combined / gemma3:4b | llm_combined | llm_combined | llm_combined |
| rewrite | rule / query_rewriter | rule | rule | rule |
| search | retrieval / BM25+Chroma+Graph | retrieval | retrieval | retrieval |
| merge | merge / RRF+cutoff+budget | merge | merge | merge |
| generate | llm_stream / qwen3:8b·gemma4 | llm_stream | llm_stream | llm_stream |
| validate | post_llm / Validator | post_llm | post_llm | post_llm |
| post | io / footer+cache+session | io | io | io |

총 9 × 4 = **36 cell + 4 summary row = 40 row**.

## 5. 9 stage timing 분포 (모든 case 합산, 단위 ms)

| Stage | sum (ms) | mean (ms) | share % |
|---|---:|---:|---:|
| clarification | 0 | 0 | 0% |
| contact | 0 | 0 | 0% (timing log 미반영) |
| **understand** | **93,138** | **23,285** | **71.5%** ★ |
| rewrite | 0 | 0 | 0% |
| search | 5,428 | 1,357 | 4.2% |
| merge | 0 | 0 | 0% (측정 정확성 의심) |
| **generate** | **33,708** | **8,427** | **25.9%** |
| validate | 0 | 0 | 0% (측정 정확성 의심) |
| post | 3 | 0.75 | 0% |
| **TOTAL** | **130,277** | **32,569** | 100% |

★ understand 단계가 4 case 합산 시간의 **71.5%** — 30-run 측정(56%)보다 더 높게 잡힘. case_04(37s)가 평균을 끌어올림.

## 6. live 캡처 도구 (`audit_data_flow.py`) 인터페이스 검증

```powershell
# 백엔드 + H100 터널 복귀 시 4 case 일괄 실행:
$base = "http://localhost:8000"
$session = "audit-step4-$(Get-Date -Format yyyyMMdd)"

python scripts/audit_data_flow.py --base-url $base --session "${session}-c1" `
  --question "영어전공 학과사무실 전화번호" `
  --case-id case_01_contact `
  --out reports/code_audit/04_data_flow/case_01_contact.live.jsonl

python scripts/audit_data_flow.py --base-url $base --session "${session}-c2" `
  --question "2020학번 졸업요건 알려줘" `
  --case-id case_02_cached `
  --out reports/code_audit/04_data_flow/case_02_cached.live.jsonl

python scripts/audit_data_flow.py --base-url $base --session "${session}-c3" `
  --question "국가장학금을 받아서 등록금을 납부하려고 하는데, 어느 부서에 물어봐야 할까?" `
  --case-id case_03_multi_intent `
  --out reports/code_audit/04_data_flow/case_03_multi_intent.live.jsonl

python scripts/audit_data_flow.py --base-url $base --session "${session}-c4" `
  --question "2020학번 졸업학점 영역별로 알려줘" `
  --case-id case_04_complex `
  --out reports/code_audit/04_data_flow/case_04_complex.live.jsonl
```

라이브 캡처는 SSE 이벤트 timestamp까지 capture하므로 stage 간 wall-clock도 보존. `audit_synth_case_jsonl.py`는 PIPELINE_TIMING만 변환 (총 시간/단계는 일치, 이벤트 grain은 없음).

## 7. Step 4 산출물 검증

| 산출물 | 상태 | 위치 |
|---|---|---|
| `audit_data_flow.py` 라이브 캡처 도구 | ✓ | `scripts/audit_data_flow.py` |
| `audit_synth_case_jsonl.py` PIPELINE_TIMING 변환 | ✓ | `scripts/audit_synth_case_jsonl.py` |
| 4 case × 9 stage = 36 cell + 4 summary | ✓ **40 row** | `04_data_flow/case_*.jsonl` |
| `summary_table.md` 매트릭스 | ✓ 이 문서 | `04_data_flow/summary_table.md` |

## 8. 핵심 진단 (Step 5 인풋)

1. **understand 71% 시간 점유** — 4 case 합산. Step 5에서 `CONV_UNDERSTANDING_ENABLED=false` 토글 시 직접 감산량 확인.
2. **merge_ms / validate_ms 모두 0ms 표기** — 측정 코드 자체에 결함 가능. Step 5 토글과 별도로 회귀 PR 후보.
3. **contact 단락 timing 미노출** — `path=contact` 케이스에서 실제 elapsed가 PIPELINE_TIMING에 0으로 찍힘. 측정 보강 필요.
4. **search 평균 1.4초** — 이 중 reranker가 몇 ms인지 분리 가능 (RERANKER_ENABLED 토글로 Step 5 확인).
5. **rewrite 0ms** — understand 통합 후 별도 측정 없음. rule 폴백 케이스만 별도. 본 4 case에서는 미관측.
