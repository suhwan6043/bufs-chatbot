# BUFS Chatbot 코드 전수조사 — 종합 보고서

**감사 기간**: 2026-05-13 (7 step, 8h)
**범위**: read-only audit. 코드 수정 0건, 발견 후 수정은 별도 PR.
**누적 커밋**: 28 (multi-task 1 + PR #21/22/23 머지 + 21 commit + audit 7 commit)

## 1. 한 페이지 진단

| 지표 | 측정값 | 한 줄 진단 |
|---|---:|---|
| Python 파일 / LOC | 199 / 56,032 | 모놀리식 위험 5건 |
| chat.py 라우터 LOC | 1,522 | god 4건 (CC 76·73·49·47) — **stream/sync 90% 중복** |
| 평균 latency (5/7 H100) | 41,547ms | understand 단계 71.5% 점유 |
| 정답률 baseline | 83.54% (4/21) | 학사지원팀 피드백 반영 후 |
| import cycles | 0 | clean (good) |
| 글로벌 싱글톤 | 14 (production) | _analyzer 1건 중복 |
| 100 unique env | 41/7/52 (기능/디버그/실험) | 일관성 양호 |

**결론**: 코드 꼬임의 80%가 **chat.py 중복(284 LOC, 90%) + query_understanding 시간 점유(71.5%)** 2 지점에 집중. 14 PR로 -2,857 LOC + -21,000ms latency 가능 (정답률 ≤-3pp 트레이드).

## 2. 7-step 산출물 인덱스

| Step | 산출 | 커밋 | 위치 |
|---|---|---|---|
| 1 | 구조·LOC·CC 매트릭스 (23 디렉터리) | b41a804 | `01_structure/` |
| 2 | chat.py 진입점 53 분기 + diff_matrix | 73b21ce | `02_entry_points/` |
| 3 | 로그 좌표 46개 설계 | 8eeef90 | `03_log_points/` |
| 4 | 4 case × 9 stage 스냅샷 | abd0760 | `04_data_flow/` |
| 5 | 6 시나리오 토글 매트릭스 | ca4d00c | `05_bisect/` |
| 6 | 14 싱글톤·100 env·import 그래프 | 84a2b65 | `06_dependencies/` |
| 7 | 14 PR 후보 + 본 보고서 | (이 커밋) | `07_checklist.md`, `AUDIT_REPORT.md` |

## 3. 핵심 발견 5건

### 3.1 chat.py god function 4건 / 90% 중복

- `chat_stream` (L469, CC 76, 511 LOC) + `_inner_generator` (L514, CC 73, 464 LOC)
- `chat_sync` (L1002, CC 47, 338 LOC)
- `_enrich_analysis` (L233, CC 49, 112 LOC)
- 9 stage 흐름이 두 경로에 거의 1:1 동일 — 중복 284 LOC, 90% 추출 가능
- sync 결손 4건: ① 컴포넌트 초기화 게이트 ② P4 retry 74 LOC ③ Clarification timing 로그 ④ LLM try/except
- **P0-1: `_run_pipeline(mode)` 추출 → -1,062 LOC, sync 결손 자동 해소**

### 3.2 query_understanding 시간 점유 71.5% (Step 5 추정 라이브 -28s 가능)

- 1차 LLM(gemma3:4b, 8s timeout) → 2차 LLM(메인 모델, 20s timeout) → 룰 폴백
- 5/7 측정: 모든 케이스 약 28s = 1차+2차 timeout 완전 소진 (cold start ollama keep_alive 만료)
- **P0-3 / P1-10**: timeout 단축 (8→3s, 20→5s) → -21s, accuracy -2~3pp

### 3.3 PIPELINE_TIMING 측정 결함 2건

- `merge_ms` 일관되게 0 — merger.merge() 374 LOC + CC 75가 0ms일 리 없음
- `validate_ms` 일관되게 0 — `_t6 ~ _t7` 측정 의도와 다름
- `path=contact` 케이스에서 `total=0ms` 잘못 로깅 (chat.py L1031-1034가 timing 변수 갱신 전 return)
- **P0-2 / P0-4**: 측정 코드 수정 (각 30분~1h)

### 3.4 글로벌 싱글톤 중복

- `_analyzer` (backend/dependencies.py:14) + `_analyzer_singleton` (query_understanding.py:54) — **동일 QueryAnalyzer 2 인스턴스**
- RAM 50 MB + 정규식 컴파일 중복
- **P1-6**: 통합 가능 (1h)

### 3.5 direct_answer 트리거 무수정 약속 준수

- 좌표: chat.py L754-758 / L1165-1169 / context_merger.py L391-442
- audit 결과 식별만, 변경 0건
- **P0-5**: docstring 명시 (안전 보장 강화)

## 4. ROI 우선순위 (P0~P2 14건)

| 분류 | 건수 | latency 합 | LOC 합 | 시간 합 |
|---|---:|---:|---:|---:|
| P0 (즉시) | 5 | -6,000ms | -1,032 | 11h |
| P1 (후속) | 5 | -15,000ms | -670 | 16h |
| P2 (보류) | 4 | 0 | -1,100 | 25h |
| **합계** | **14** | **-21,000ms** | **-2,857** | **55h** |

## 5. multi-task 1과의 관계

multi-task 1 (query_understanding 통합 + KO prompt v1 + Intent 18 카테고리)이 본 audit의 **3가지 핵심 발견 모두에 부분 해결**:

| 발견 | multi-task 1 영향 |
|---|---|
| understand 71.5% 시간 점유 | multi-task 1 자체가 통합 호출 도입 — 그러나 88% TIMEOUT으로 사실상 룰 폴백 가동 |
| Intent 18 카테고리 | analyzer + query_understanding 양쪽에서 import → app.models 핵심 의존성 |
| KO_PROMPT_VERSION env | Step 6 분류 (Feature 41건 중 하나) |

multi-task 1 작업 영향 없이 audit 완료. 향후 PR는 multi-task 1 default 유지 + Step 5 라이브 측정 후 토글 default 변경 검토.

## 6. 사용자 결정 사항

1. **다음 PR 선택**: P0 5건 중 어느 것부터?
   - 즉시 (30분~1h): P0-2 / P0-4 / P0-5
   - 1일: P0-1 `_run_pipeline` 추출 (대규모, 회귀 risk)
   - 라이브 측정 필요 (4h): P0-3 / P1-10
2. **백엔드 + H100 터널 복귀 시점**: Step 4/5 라이브 데이터 채우기
3. **운영 환경 구체화**: P2-13 (uvicorn workers=1) — 다중 worker OOM 방지

## 7. 감사 종료 조건 충족

| 조건 | 상태 |
|---|---|
| 7 step 별 1 커밋 (총 7 커밋) | ✓ b41a804 / 73b21ce / 8eeef90 / abd0760 / ca4d00c / 84a2b65 / (현재 PR) |
| 14 PR 후보 식별 | ✓ |
| direct_answer 무수정 약속 | ✓ |
| Read-only 원칙 | ✓ |
| AUDIT_REPORT.md 1장 요약 | ✓ (본 문서) |
| 종합 체크리스트 | ✓ `07_checklist.md` |
