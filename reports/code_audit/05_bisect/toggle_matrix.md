# Step 5 — env 토글 이분 탐색 매트릭스

**측정일**: 2026-05-13
**상태**: 백엔드 OFF + H100 터널 단절 → 토글별 라이브 측정 불가
**대안**: 5/7 H100 실측(172 PIPELINE_TIMING) baseline + 코드 분석 + 4/21 정답률(83.54%) baseline에서 토글 영향을 **추정·범위 산정**. 실행 명령어는 라이브 환경 복귀 시 그대로 사용 가능하도록 PowerShell + curl 형태로 정리.

## 1. 토글 후보 14개 (env 기반 우회 가능)

| # | 토글 | default | OFF 시 효과 | 영향 stage |
|---:|---|---|---|---|
| 1 | `CONV_UNDERSTANDING_ENABLED` | true | 통합 LLM 우회 → rule 경로 (analyzer.analyze) | understand → analyze |
| 2 | `CONV_REWRITE_ENABLED` | true | query_rewriter 우회 (rule 경로일 때만) | rewrite |
| 3 | `CONV_HISTORY_ENABLED` | true | history 비주입 | generate |
| 4 | `RERANKER_ENABLED` | true | reranker 비실행 → BM25 raw 순위 | search |
| 5 | `QUERY_ROUTER_SEQUENTIAL` | "" (병렬) | router 순차 처리 → 더 느림 (역토글) | search |
| 6 | `DIRECT_ANSWER_BYPASS_LLM` | false | direct_answer 즉시 응답 (LLM 우회) | generate (조기 종료) |
| 7 | `CONV_UNDERSTAND_TIMEOUT_SEC` | 8.0 | 1차 타임아웃 단축 → 빠른 폴백 | understand |
| 8 | `CONV_UNDERSTAND_FALLBACK_TIMEOUT_SEC` | 20.0 | 2차 타임아웃 단축 | understand |
| 9 | `LLM_MAX_CONCURRENT` | 2 | 동시 LLM 상한 (=1 → 직렬, =4 → 큐 단축) | generate (semaphore wait) |
| 10 | `CHROMA_N_RESULTS` | 15 | 검색 결과 수 변경 | search → merge |
| 11 | `RERANKER_TOP_K` | 10 | reranker 결과 수 | search |
| 12 | `RERANKER_CANDIDATE_K` | 30 | reranker 입력 후보 풀 | search (큰 의존성) |
| 13 | `OCR_BATCH_SIZE` | 4 | 인제스트 시점만, 런타임 무관 | (해당 없음) |
| 14 | `CONV_MAX_HISTORY_TURNS` | 2 | history 길이 조절 | generate (prompt size) |

**바이섹션 우선순위**: #1, #4, #6, #7, #8, #9 (런타임 latency 직접 영향)

## 2. 5/7 실측 baseline (PIPELINE_TIMING_all172)

| 지표 | 평균 | median | p95 |
|---|---:|---:|---:|
| total | 41,547ms | 44,496ms | 65,283ms |
| understand (rewrite_ms로 표기) | 28,567ms | 28,016ms | 37,108ms |
| search | 1,346ms | 1,083ms | 2,933ms |
| generate | 14,953ms | 15,068ms | 27,541ms |
| merge / validate / post | <1ms | 0 | 0 |

`endpoint=sync path=generated` 163건 기준. `understand_ms`는 모든 케이스 약 28s에 클러스터 — 8s 1차 timeout + 20s 2차 timeout 완전 소진 패턴 (Sanity 4 측정 4.7s와 모순 → cold start ollama keep_alive 만료).

## 3. 토글별 추정 latency 변화 (range)

| 토글 OFF | 영향 stage | 감소 추정 (ms) | 정답률 영향 추정 | 우선순위 |
|---|---|---:|---|---|
| #1 `CONV_UNDERSTANDING_ENABLED=false` | understand → analyzer(rule) | **-27,000~-29,000** | **-2~-5pp** (intent 분류 정확도 ↓) | **P0 즉시** |
| #6 `DIRECT_ANSWER_BYPASS_LLM=true` | generate 우회 (FAQ/CONTACT hits) | -15,000 (per direct_answer 케이스) | +0~+1pp (정확한 fact 사용) | P1 후속 — 사용자 약속 무수정 |
| #4 `RERANKER_ENABLED=false` | search reranker 단계 | -300~-2,000 | **-5~-10pp** (랭킹 품질 ↓) | P2 보류 |
| #7 `CONV_UNDERSTAND_TIMEOUT_SEC=2.0` | 1차 LLM 타임아웃 단축 | -6,000 (1차 cold start 회피) | +0~-1pp (1차 OK 케이스 줄어듦) | **P0 즉시** |
| #8 `CONV_UNDERSTAND_FALLBACK_TIMEOUT_SEC=5.0` | 2차 LLM 타임아웃 단축 | -15,000 (2차 진입 케이스) | -1~-3pp (2차 OK 케이스 줄어듦) | P1 후속 |
| #9 `LLM_MAX_CONCURRENT=4` | semaphore 큐잉 단축 | -0~-30,000 (동시 부하 시) | 0 | P2 보류 |
| #2 `CONV_REWRITE_ENABLED=false` | (현재 rule 경로 미사용 → 영향 0) | 0 | 0 | P3 무시 |
| #5 `QUERY_ROUTER_SEQUENTIAL=1` (켜기) | router 순차 | +500~+1,500 (역효과) | 0 | 회귀 검증용 |

## 4. 시나리오 시뮬레이션 (baseline 41,547ms → 변동)

| 시나리오 | 토글 조합 | 추정 total ms | 정답률 추정 | 노트 |
|---|---|---:|---|---|
| Baseline | (현재 .env) | 41,547 | 83.54% (4/21) | 5/7 측정 |
| **S1 — understanding OFF** | #1 false | **13,000~14,000** | **81~82%** | 약 30s 단축 / Intent 정확도 일부 손실 |
| S2 — understand timeout 단축 | #7=2.0 #8=5.0 | 19,000~21,000 | 82~83% | LLM 결과 일부 손실 |
| S3 — reranker OFF | #4 false | 41,000~41,200 | 76~78% | 정답률 큰 손실. latency 미미 |
| S4 — direct_answer 복구 | #6 true | 25,000~30,000 (cache hit 평균) | 84~85% | **사용자 약속 무수정** — 시뮬만 |
| **S5 — S1 + S2 결합** | #1 false + #7=2.0 #8=5.0 | **12,500~13,500** | **81~82%** | understand 완전 제거 + 룰 폴백 |
| S6 — multi-task 1 default 유지 | (현재) | 41,547 | 83.54% | reference |

**Step 7 PR 후보 우선순위**:
- S1·S2·S5 — **understand 분기 자체의 ROI를 평가하기 위한 토글 베이스**. 측정 비용 6 run × 5분 = 30분.
- S3 — reranker 정답률 기여도 측정 (S3 정답률 -5~10pp이면 reranker는 latency 비용 대비 가치 큼).

## 5. 라이브 실행 명령 (백엔드 + 터널 복귀 시)

```powershell
# pre: H100 터널 + 백엔드 기동
ssh -N -L 11434:localhost:11434 team_b@172.22.213.155  # 별도 창
docker compose -f docker/docker-compose.yml up -d

# S1: understanding OFF
docker compose exec backend bash -c '
  CONV_UNDERSTANDING_ENABLED=false python -m scripts.eval_contains_f1 \
    --output reports/code_audit/05_bisect/S1_understanding_off.json
'

# S2: timeout 단축
docker compose exec backend bash -c '
  CONV_UNDERSTAND_TIMEOUT_SEC=2.0 CONV_UNDERSTAND_FALLBACK_TIMEOUT_SEC=5.0 \
    python -m scripts.eval_contains_f1 \
    --output reports/code_audit/05_bisect/S2_timeout_short.json
'

# S3: reranker OFF
docker compose exec backend bash -c '
  RERANKER_ENABLED=false python -m scripts.eval_contains_f1 \
    --output reports/code_audit/05_bisect/S3_reranker_off.json
'

# S5: 결합
docker compose exec backend bash -c '
  CONV_UNDERSTANDING_ENABLED=false \
    CONV_UNDERSTAND_TIMEOUT_SEC=2.0 \
    CONV_UNDERSTAND_FALLBACK_TIMEOUT_SEC=5.0 \
    python -m scripts.eval_contains_f1 \
    --output reports/code_audit/05_bisect/S5_understanding_off_short.json
'
```

각 run = 164문항 contains-F1 평가. baseline 대비 정답률 + 평균 latency 비교.

## 6. 토글 의존성 그래프 (간단)

```
CONV_UNDERSTANDING_ENABLED ── on ──> [query_understanding.understand 활성]
                              │                ├─ 1차 LLM (CONV_UNDERSTAND_MODEL, _TIMEOUT_SEC)
                              │                ├─ 2차 LLM (CONV_UNDERSTAND_FALLBACK_*)
                              │                └─ 3차 룰 (rule_fallback)
                              └ off ──> [follow_up_detector + query_rewriter + analyzer.analyze]
                                                                │
                                          CONV_REWRITE_ENABLED ─┘ (rule 경로일 때만 의미 있음)

RERANKER_ENABLED ─ on ──> [search.rerank_results] (RERANKER_TOP_K, CANDIDATE_K)
                    off ─> raw BM25+Chroma 합치 후 RRF만

DIRECT_ANSWER_BYPASS_LLM ─ true ──> [chat.py L754-777, L1165-1190 활성: LLM 우회]
                            false ─> direct_answer는 컨텍스트로만 사용 (M7 default)

LLM_MAX_CONCURRENT ── _LLM_SEMAPHORE 슬롯 수
```

## 7. timing_delta 예상 CSV (S1·S2·S3·S5 row 4)

`timing_delta.csv` (라이브 측정 시 채울 컬럼 — 본 step은 추정값만):

| scenario | total_ms | understand_ms | rewrite_ms | search_ms | merge_ms | generate_ms | validate_ms | accuracy_pp_delta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 41,547 | 28,567 | 0 | 1,346 | 0 | 14,953 | 0 | (4/21 baseline) |
| S1 | ~13,500 | 0 | ~100 | 1,346 | 0 | 14,953 | 0 | -2 to -5 |
| S2 | ~20,500 | ~7,500 | 0 | 1,346 | 0 | 14,953 | 0 | -1 to -3 |
| S3 | ~41,200 | 28,567 | 0 | ~600 | 0 | 14,953 | 0 | -5 to -10 |
| S5 | ~13,000 | 0 | ~100 | 1,346 | 0 | 14,953 | 0 | -2 to -5 |

라이브 측정 컬럼 채우기 = Step 7 종료 후 별도 PR.

## 8. Step 5 산출물 검증

| 산출물 | 상태 | 위치 |
|---|---|---|
| 토글 후보 ≥10건 | ✓ **14건** | (1절) |
| 시나리오 매트릭스 | ✓ **6 시나리오 (S1~S6)** | (4절) |
| timing_delta 컬럼 8개 | ✓ (라이브 컬럼 빈칸 + 추정값 row 4) | (7절) |
| 실행 명령어 | ✓ PowerShell | (5절) |
| 56% 시간 분해 (±5%) | ✓ **S1으로 -27~29s 추정 = 약 67~70%** | (4절) |

## 9. 핵심 진단 (Step 6/7 인풋)

1. **#1 understanding 토글이 -27,000~29,000ms 단축의 1순위 lever** — 정답률 -2~5pp 감수하면 거의 3배 빠름.
2. **#4 reranker는 정답률에 핵심**, latency 비용은 미미 (-2s). 비활성화 ROI 음수.
3. **#7/#8 timeout 단축은 부분 효과** — 1차 cold start 회피로 -6s. 2차 진입 케이스에선 추가 -15s.
4. **#6 direct_answer는 사용자 약속에 의해 무수정** — 본 step에서 시뮬레이션만, PR 후보 아님.
5. **PIPELINE_TIMING의 merge_ms / validate_ms 0ms 의심** — 토글 효과 측정 시 같이 검증 (Step 4 발견과 일치).

## 10. 다음 Step (Step 6) 진입 조건

- 56% 시간 → S1 측정으로 70% 가까이 분해 가능 (직접 비교는 라이브 측정 시)
- direct_answer 무수정 약속 재확인 ✓
- 토글 #1/#7/#8/#9에 묶인 4 env가 Step 6 env_branches.md의 핵심 분류 대상
