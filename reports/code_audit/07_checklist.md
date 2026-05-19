# Step 7 — 종합 체크리스트 (14 PR 후보)

**측정일**: 2026-05-13
**감사 범위**: 199 Python 파일 / 56,032 LOC / 1,677 함수 (Step 1)
**감사 기간**: Step 1~7, 약 8시간 누적 (예상 일치)
**Read-only 원칙 준수**: 모든 코드 변경 0건, audit 결과만 보존
**direct_answer 트리거 무수정**: chat.py L754-758, L1165-1169, context_merger:391-442 — 사용자 약속 준수 ✓

## 1. 우선순위 매트릭스

| 우선순위 | 정의 | 분류 기준 |
|---|---|---|
| **P0 (즉시)** | 운영·정확도·latency 직접 영향, 측정 baseline 확보 | hotspot 5건의 핵심 분리 + 측정 결함 |
| **P1 (후속)** | ROI 크지만 대규모 리팩터 또는 직전 PR 검증 필요 | god function 분리 + 중복 제거 |
| **P2 (보류)** | ROI 측정 후 진행 | 알고리즘 분리·튜닝 |

## 2. 14 PR 후보 (우선순위·영향도 매트릭스)

### P0 (즉시) — 5건

| # | PR 제목 | 영향 (latency / accuracy / 안전) | 변경 범위 | 의존 |
|---:|---|---|---|---|
| 1 | **chat.py `_run_pipeline(mode)` 추출** | latency 0 / accuracy 0 / 안전 ↑ (sync 결손 4건 해소) | chat.py 1,522 → ~460 LOC (-1,062 LOC, 46%↓) | Step 2 diff_matrix |
| 2 | **PIPELINE_TIMING merge_ms / validate_ms 0ms 측정 결함** | latency 0 / 진단 정확성 ↑ | chat.py merge/validate 타이밍 변수 명명 | Step 4 발견 |
| 3 | **CONV_UNDERSTAND_TIMEOUT_SEC default 조정** (8 → 3) | latency -6,000ms / accuracy -1pp | .env + app/config.py docstring | Step 5 S2 측정 (라이브 검증 필요) |
| 4 | **contact 단락 timing 노출** (`path=contact` total=0ms 버그) | latency 0 / 진단 ↑ | chat.py L1030-1034 `_t_contact` 추가 | Step 4 발견 |
| 5 | **direct_answer 약속 docstring 명시** | 안전 ↑ | app/config.py L228-235 + chat.py 주석 | Step 5/6 발견 |

### P1 (후속) — 5건

| # | PR 제목 | 영향 | 변경 범위 | 의존 |
|---:|---|---|---|---|
| 6 | **`_analyzer` + `_analyzer_singleton` 중복 통합** | RAM -50 MB / cold start ↓ | dependencies.py + query_understanding.py | Step 6 |
| 7 | **`_enrich_analysis` 분리** (CC 49, 112 LOC) | 가독성 / 테스트 용이 | chat.py L233-344 → app/pipeline/enrich.py | Step 2 |
| 8 | **answer_generator `_build_prompt` 분리** (CC 50, 300 LOC) | 가독성 | app/pipeline/answer_generator.py L300-599 | Step 1 |
| 9 | **query_analyzer Intent 분기 선언형 config화** (CC 67 `_analyze_en`, CC 42 `_classify_intent`) | 가독성 / 유지보수 | app/pipeline/query_analyzer.py 1,189 LOC | Step 1 |
| 10 | **CONV_UNDERSTAND_FALLBACK_TIMEOUT default 5s** | latency -15,000ms (cold start 케이스) / accuracy -2pp | .env | Step 5 S2 |

### P2 (보류) — 4건

| # | PR 제목 | 영향 | 변경 범위 | 의존 |
|---:|---|---|---|---|
| 11 | **context_merger.merge 3-알고리즘 분리** (RRF / adaptive_cutoff / budget) | 가독성 / 회귀 측정 후 진행 | context_merger.py 374 LOC merge() | ROI 검증 필요 |
| 12 | **academic_graph.py 4 god function 분리** (CC 95/86/73/55) | 가독성 / 그래프 query 유지보수 | app/graphdb/academic_graph.py 3,459 LOC | ROI 검증 |
| 13 | **uvicorn workers=1 강제 명시 또는 SharedMemory 분리** | OOM 방지 (다중 worker 운영 시) | docker/docker-compose.yml + backend/main.py | 운영 직전 |
| 14 | **scripts/pdf_to_graph.py `build_graph_from_pdf` 분해 (CC 123 최극단)** | 가독성 / 인제스트 유지보수 | scripts/pdf_to_graph.py 487 LOC 함수 | 인제스트 사이클 빠른 시 |

## 3. ROI 추정 표

| # | latency 감소 | 정답률 변화 | 코드 LOC 감소 | 안전·진단 | 작업 시간 추정 |
|---:|---:|---:|---:|---|---:|
| 1 | 0 | 0 | **-1,062** | ★★★ sync 결손 해소 | 8h |
| 2 | 0 | 0 | +20 (측정 코드) | ★★ 진단 정확성 | 1h |
| 3 | **-6,000ms** | -1pp | 0 | ★ 측정 후 가시화 | 1h (라이브 측정 포함 4h) |
| 4 | 0 | 0 | +10 | ★ 진단 가시화 | 30분 |
| 5 | 0 | 0 | +5 (docstring) | ★★★ 안전 약속 명시 | 30분 |
| 6 | -0 | 0 | -30 | ★ RAM 회수 | 1h |
| 7 | 0 | 0 | -90 LOC chat.py | ★ 분리 | 2h |
| 8 | 0 | 0 | -250 LOC ag | ★ 분리 | 4h |
| 9 | 0 | 0 | -300 LOC qa | ★★ 선언형 변환 | 8h |
| 10 | **-15,000ms** | -2pp | 0 | ★ AB 측정 필수 | 1h (측정 4h) |
| 11 | 0 | 0 | -100 LOC cm | ★ 모듈화 | 4h |
| 12 | 0 | 0 | -800 LOC graphdb | ★★ 대형 god 분해 | 16h |
| 13 | 0 | 0 | 0 | ★★★ OOM 방지 | 1h |
| 14 | 0 | 0 | -200 LOC scripts | ★ 인제스트 가독성 | 4h |
| **합계** | **-21,000ms** | -3pp | **-2,857 LOC** | — | **55h** |

## 4. 핵심 의존성

```
P0-1 (chat.py 분리) ─ blocks → P1-7 (_enrich_analysis 분리)
P0-1 ─ blocks → P1-10 (timeout default)
P0-3 ─ needs → 라이브 측정 (백엔드+터널 복귀)
P0-5 ─ standalone (즉시 가능)
P1-6 ─ standalone
P1-8 ─ standalone
P1-9 ─ blocks → P2-12 (graphdb god 분해)
P2-13 ─ needs → 운영 환경 정의
```

권장 순서:
1. **P0-2 / P0-4 / P0-5** — 30분 이내, 측정 보강 + 안전 약속
2. **P0-1 _run_pipeline 추출** — 1일 작업, 가장 큰 코드 정리
3. **P1-6 / P1-7 / P1-8 / P1-10** — 각 1~4h, 병렬 가능
4. **P0-3** — 라이브 측정 + default 변경
5. **P1-9 / P2-12** — 큰 god 분해, 측정 후
6. **P2-11 / P2-14** — 알고리즘·인제스트
7. **P2-13** — 운영 직전

## 5. 측정 baseline (감사 종합)

| 지표 | 값 | 출처 |
|---|---:|---|
| Python 파일 수 | 199 | Step 1 |
| 총 LOC | 56,032 | Step 1 |
| 총 함수 수 | 1,677 | Step 1 |
| 평균 CC | 4.8 | Step 1 |
| CC ≥ 10 (복잡) | 192 (11.4%) | Step 1 |
| CC ≥ 50 (극단) | 13 | Step 1 |
| chat.py 분기점 | 53 (stream 28 + sync 25) | Step 2 |
| chat.py 중복 LOC | ~284 (90%) | Step 2 |
| 로그 좌표 후보 | 46 | Step 3 |
| 4 case × 9 stage 스냅샷 | 36 cell | Step 4 |
| 토글 시나리오 | 6 (S1~S6) | Step 5 |
| understand 시간 점유 (raw) | **71.5%** (case 합산) | Step 4 |
| 추정 분해 후 latency | -21,000ms (S5 + P0-3) | Step 5 |
| production 싱글톤 | 14 | Step 6 |
| unique env vars | 100 | Step 6 |
| import cycles | 0 | Step 6 |

## 6. direct_answer 트리거 (사용자 약속 무수정 확인)

| 좌표 | 파일 | 라인 | 상태 |
|---|---|---:|---|
| stream 진입 | `backend/routers/chat.py` | 754-758 | 무수정 ✓ |
| stream 응답 | `backend/routers/chat.py` | 760-777 | 무수정 ✓ |
| sync 진입 | `backend/routers/chat.py` | 1165-1169 | 무수정 ✓ |
| sync 응답 | `backend/routers/chat.py` | 1170-1190 | 무수정 ✓ |
| merge direct_answer 추출 | `app/pipeline/context_merger.py` | 391-442 (`_try_extract_direct_answer`) | 무수정 ✓ |
| env 토글 정의 | `app/config.py` | 228-235 (`direct_answer_bypass_llm`) | 무수정 ✓ |

audit이 식별만 하고 변경하지 않음. P0-5 (docstring 명시) 만이 위 좌표 주석 강화 — 코드 로직은 변경 없음.

## 7. multi-task 1 (직전 작업) 영향

| 항목 | 상태 |
|---|---|
| query_understanding.py 56% 시간 점유 진단 | ✓ Step 4·5 재확인 (실 71.5%) |
| understand 3단계 폴백 (gemma3:4b → llm → rule) | ✓ Step 5 토글로 측정 가능 |
| Intent 18 카테고리 enum | ✓ Step 6 import 그래프 영향 식별 (app.models 핵심) |
| KO SYSTEM_PROMPT v1 (560 토큰) | ✓ Step 6 KO_PROMPT_VERSION env 분류 (Feature) |

## 8. Step 7 산출물 검증

| 산출물 | 상태 | 위치 |
|---|---|---|
| 14 PR 후보 우선순위 표 | ✓ (2절) | `07_checklist.md` |
| P0/P1/P2 분류 | ✓ 5/5/4 | (2절) |
| ROI 추정 | ✓ (3절) | |
| 의존성 그래프 | ✓ (4절) | |
| 측정 baseline 종합 | ✓ (5절) | |
| direct_answer 무수정 확인 | ✓ (6절) | |
| AUDIT_REPORT.md (1장 요약) | (다음 산출물) | `AUDIT_REPORT.md` |

## 9. 다음 단계 (사용자 결정 사항)

1. **P0-2 / P0-4 / P0-5 (30분 이내)**: 즉시 머지 가능
2. **P0-1 (1일)**: chat.py `_run_pipeline(mode)` 추출 — 회귀 risk 큰 대규모. 별도 PR + 정답률 검증 의무
3. **P0-3 / P1-10 (라이브 측정 4h)**: 백엔드 + H100 터널 복귀 후 timing_delta.csv 실제값 채우기
4. **P1-6 ~ P1-10**: 병렬 가능, 각 1~4h
5. **P2-12 ~ P2-14**: ROI 검증 후 진행

audit 종료 — 사용자가 14 PR 후보 중 어느 것부터 진행할지 결정.
